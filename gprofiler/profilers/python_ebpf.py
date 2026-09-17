#
# Copyright (C) 2022 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import glob
import os
import resource
import selectors
import shutil
import signal
from pathlib import Path
from subprocess import Popen
from typing import Dict, List, Optional, Tuple, Union, cast

from granulate_utils.linux.ns import is_running_in_init_pid
from psutil import NoSuchProcess, Process

from gprofiler.exceptions import CalledProcessError, StopEventSetException
from gprofiler.gprofiler_types import ProcessToProfileData, ProfileData
from gprofiler.log import get_logger_adapter
from gprofiler.metadata import application_identifiers
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers import python
from gprofiler.profilers.profiler_base import ProfilerBase
from gprofiler.utils import (
    poll_process,
    random_prefix,
    reap_process,
    resource_path,
    run_process,
    start_process,
    wait_event,
    wait_for_file_by_prefix,
)
from gprofiler.utils.collapsed_format import parse_many_collapsed

logger = get_logger_adapter(__name__)


class PythonEbpfError(CalledProcessError):
    """
    An error encountered while running PyPerf.
    """


class PythonEbpfProfiler(ProfilerBase):
    MAX_FREQUENCY = 1000
    PYPERF_RESOURCE = "python/pyperf/PyPerf"
    _GET_FS_OFFSET_RESOURCE = "python/pyperf/get_fs_offset"
    _GET_STACK_OFFSET_RESOURCE = "python/pyperf/get_stack_offset"
    _EVENTS_BUFFER_PAGES = 256  # 1mb and needs to be physically contiguous
    # ❌ REMOVED: _is_system_profiler = True  # PyPerf now has its own skip logic
    
    def _should_limit_processes(self) -> bool:
        """eBPF Python profiler is system-wide and should not limit processes."""
        return False
    
    def _is_system_wide_profiler(self) -> bool:
        """eBPF Python profiler is system-wide and can be disabled on busy systems."""
        return True
    # 28mb (each symbol is 224 bytes), but needn't be physicall contiguous so don't care
    _SYMBOLS_MAP_SIZE = 131072
    _DUMP_SIGNAL = signal.SIGUSR2
    _DUMP_TIMEOUT = 5  # seconds
    _POLL_TIMEOUT = 10  # seconds
    _GET_OFFSETS_TIMEOUT = 5  # seconds
    _OUTPUT_READ_SIZE = 65536  # bytes read every cycle from stderr
    
    # Error detection constants
    _DELETED_LIBRARY_ERROR_PATTERN = "Failed to iterate over ELF symbols"
    _DELETED_FILE_MARKER = "(deleted)"
    _PYTHON_SETUP_FAILURE = "Setup new python failed"
    _TEMPORARY_FILE_PATTERNS = ["runfiles_", ".tmp/", "build_", "temp_"]  # Generic temporary file patterns

    def __init__(
        self,
        frequency: int,
        duration: int,
        profiler_state: ProfilerState,
        *,
        add_versions: bool,
        user_stacks_pages: Optional[int] = None,
        verbose: bool,
        min_duration: int = 10,
        python_skip_pyperf_profiler_above: int = 0,
    ):
        super().__init__(frequency, duration, profiler_state, min_duration)
        self.process: Optional[Popen] = None
        self.process_selector: Optional[selectors.BaseSelector] = None
        self.output_path = Path(self._profiler_state.storage_dir) / f"pyperf.{random_prefix()}.col"
        self.add_versions = add_versions
        self.user_stacks_pages = user_stacks_pages
        self._kernel_offsets: Dict[str, int] = {}
        self._metadata = python.PythonMetadata(self._profiler_state.stop_event)
        self._verbose = verbose
        self._pyperf_staticx_tmpdir: Optional[Path] = None
        self._python_skip_pyperf_profiler_above = python_skip_pyperf_profiler_above
        if os.environ.get("TMPDIR", None) is not None:
            # We want to create a new level of hirerachy in our current staticx tempdir.
            self._pyperf_staticx_tmpdir = Path(os.environ["TMPDIR"]) / ("pyperf_" + random_prefix())

    def _count_python_processes(self) -> int:
        """
        Count Python processes using the same detection logic as py-spy.
        This ensures consistent counting between PyPerf skip logic and py-spy process selection.
        """
        try:
            from gprofiler.utils import pgrep_maps, pgrep_exe
            from granulate_utils.python import DETECTED_PYTHON_PROCESSES_REGEX
            from gprofiler.platform import is_windows
            
            if is_windows():
                # Windows: Use executable name matching
                all_processes = [x for x in pgrep_exe("python")]
            else:
                # Linux: Use memory map scanning (same as py-spy)
                all_processes = [x for x in pgrep_maps(DETECTED_PYTHON_PROCESSES_REGEX)]
            
            return len(all_processes)
        except Exception as e:
            logger.warning(f"Could not count Python processes for PyPerf skip logic: {e}")
            return 0

    def should_skip_due_to_python_threshold(self) -> bool:
        """
        Check if PyPerf should be skipped due to too many Python processes.
        This provides PyPerf-specific resource management independent of system profiler logic.
        """
        if self._python_skip_pyperf_profiler_above <= 0:
            return False  # No threshold set, don't skip
        
        python_process_count = self._count_python_processes()
        should_skip = python_process_count > self._python_skip_pyperf_profiler_above
        
        if should_skip:
            logger.info(
                f"Skipping PyPerf - {python_process_count} Python processes exceed threshold "
                f"of {self._python_skip_pyperf_profiler_above}. py-spy fallback will be used for Python profiling."
            )
        else:
            logger.debug(f"PyPerf: Python process count {python_process_count} (threshold: {self._python_skip_pyperf_profiler_above})")
        
        return should_skip

    @classmethod
    def _check_output(cls, process: Popen, output_path: Path) -> None:
        if not glob.glob(f"{str(output_path)}.*"):
            # opened in pipe mode, so these aren't None.
            assert process.stdout is not None
            assert process.stderr is not None
            assert isinstance(process.args, list) and all(
                isinstance(s, str) for s in process.args
            ), process.args  # mypy
            stdout = process.stdout.read().decode()
            stderr = process.stderr.read().decode()
            raise PythonEbpfError(process.returncode, process.args, stdout, stderr)

    @staticmethod
    def _ebpf_environment() -> None:
        """
        Make sure the environment is ready so that libbpf-based programs can run.
        Technically this is needed only for container environments, but there's no reason not
        to verify those conditions stand anyway (and during our tests - we run gProfiler's executable
        in a container, so these steps have to run)
        """
        # see explanation in https://github.com/intel/gprofiler/issues/443#issuecomment-1229515568
        assert is_running_in_init_pid(), "PyPerf must run in init PID NS!"

        # increase memlock (Docker defaults to 64k which is not enough for the get_offset programs)
        resource.setrlimit(resource.RLIMIT_MEMLOCK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))

        # mount /sys/kernel/debug in our container
        if not os.path.ismount("/sys/kernel/debug"):
            os.makedirs("/sys/kernel/debug", exist_ok=True)
            run_process(["mount", "-t", "debugfs", "none", "/sys/kernel/debug"])

    def _get_offset(self, prog: str) -> int:
        return int(
            run_process(
                resource_path(prog), stop_event=self._profiler_state.stop_event, timeout=self._GET_OFFSETS_TIMEOUT
            ).stdout.strip()
        )

    def _kernel_fs_offset(self) -> int:
        try:
            return self._kernel_offsets["task_struct_fs"]
        except KeyError:
            offset = self._kernel_offsets["task_struct_fs"] = self._get_offset(self._GET_FS_OFFSET_RESOURCE)
            return offset

    def _kernel_stack_offset(self) -> int:
        try:
            return self._kernel_offsets["task_struct_stack"]
        except KeyError:
            offset = self._kernel_offsets["task_struct_stack"] = self._get_offset(self._GET_STACK_OFFSET_RESOURCE)
            return offset

    def _pyperf_base_command(self) -> List[str]:
        cmd = [
            resource_path(self.PYPERF_RESOURCE),
            "--fs-offset",
            str(self._kernel_fs_offset()),
            "--stack-offset",
            str(self._kernel_stack_offset()),
        ]
        if self._verbose:
            # 4 is the max verbosityLevel in PyPerf.
            cmd.extend(["-v", "4"])
        return cmd

    def _staticx_cleanup(self) -> None:
        """
        We experienced issues with PyPerf's staticx'd directory, so we want to clean it up on termination.
        """
        assert not self.is_running(), "_staticx_cleanup is impossible while PyPerf is running"
        if self._pyperf_staticx_tmpdir is not None and self._pyperf_staticx_tmpdir.exists():
            shutil.rmtree(self._pyperf_staticx_tmpdir.as_posix())

    def test(self) -> None:
        self._ebpf_environment()

        for f in glob.glob(f"{str(self.output_path)}.*"):
            os.unlink(f)

        # Run the process and check if the output file is properly created.
        # Wait up to 10sec for the process to terminate.
        # Allow cancellation via the stop_event.
        cmd = self._pyperf_base_command() + [
            "--output",
            str(self.output_path),
            "-F",
            "1",
            "--duration",
            "1",
        ]
        # pyperf sometimes has a lot of output to stdout and stderr, which makes the process halt until read.
        process = start_process(cmd, tmpdir=self._pyperf_staticx_tmpdir, pipesize=1024 * 1024)
        try:
            poll_process(process, self._POLL_TIMEOUT, self._profiler_state.stop_event)
        except TimeoutError:
            process.kill()
            raise
        else:
            self._check_output(process, self.output_path)
        finally:
            self._staticx_cleanup()

    def start(self) -> None:
        logger.info("Starting profiling of Python processes with PyPerf")
        cmd = self._pyperf_base_command() + [
            "--output",
            str(self.output_path),
            "-F",
            str(self._frequency),
            "--events-buffer-pages",
            str(self._EVENTS_BUFFER_PAGES),
            "--symbols-map-size",
            str(self._SYMBOLS_MAP_SIZE),
            # Duration is irrelevant here, we want to run continuously.
        ]
        if self._profiler_state.insert_dso_name:
            cmd.extend(["--insert-dso-name"])

        if self.user_stacks_pages is not None:
            cmd.extend(["--user-stacks-pages", str(self.user_stacks_pages)])

        for f in glob.glob(f"{str(self.output_path)}.*"):
            os.unlink(f)

        process = start_process(cmd, tmpdir=self._pyperf_staticx_tmpdir)
        # wait until the transient data file appears - because once returning from here, PyPerf may
        # be polled via snapshot() and we need it to finish installing its signal handler.
        try:
            wait_event(self._POLL_TIMEOUT, self._profiler_state.stop_event, lambda: os.path.exists(self.output_path))
        except TimeoutError:
            process.kill()
            assert process.stdout is not None and process.stderr is not None
            stdout = process.stdout.read()
            stderr = process.stderr.read()
            logger.error("PyPerf failed to start", stdout=stdout, stderr=stderr)
            self._staticx_cleanup()
            raise
        else:
            self.process = process
            self._register_process_selectors()

    def _register_process_selectors(self) -> None:
        self.process_selector = selectors.DefaultSelector()
        assert self.process_selector and self.process and self.process.stdout and self.process.stderr  # for mypy
        self.process_selector.register(self.process.stdout, selectors.EVENT_READ)
        self.process_selector.register(self.process.stderr, selectors.EVENT_READ)

    def _unregister_process_selectors(self) -> None:
        if self.process_selector is not None:
            try:
                self.process_selector.close()
            except (OSError, ValueError) as e:
                # Selector might already be closed by cleanup process
                logger.debug(f"Selector close failed (likely already closed): {e}")
            finally:
                self.process_selector = None

    def _read_process_standard_outputs(self) -> Tuple[Optional[str], Optional[str]]:
        """
        Read the process standard outputs in a non-blocking manner.

        :return: tuple[stdout, stderr]
        """
        stdout: Optional[str] = None
        stderr: Optional[str] = None
        assert self.process_selector and self.process
        for key, _ in self.process_selector.select(timeout=0):
            output = key.fileobj.read1(self._OUTPUT_READ_SIZE)  # type: ignore
            # Properly convert bytes to string if needed
            if isinstance(output, bytes):
                output = output.decode('utf-8', errors='replace')
            if key.fileobj is self.process.stdout:
                stdout = output
            elif key.fileobj is self.process.stderr:
                stderr = output
        return stdout, stderr

    def _is_deleted_library_error(self, stderr_str: str) -> bool:
        """Check if stderr contains deleted library errors."""
        return (self._DELETED_LIBRARY_ERROR_PATTERN in stderr_str and 
                self._DELETED_FILE_MARKER in stderr_str)

    def _is_temporary_file_error(self, stderr_str: str) -> bool:
        """Check if stderr contains temporary file system errors."""
        return (self._PYTHON_SETUP_FAILURE in stderr_str and 
                any(pattern in stderr_str for pattern in self._TEMPORARY_FILE_PATTERNS))

    def _process_pyperf_stderr(self, stderr_str: str, stdout: bytes) -> None:
        """Process PyPerf stderr output and log appropriately."""
        # Check for deleted library errors and handle gracefully
        if self._is_deleted_library_error(stderr_str):
            deleted_lib_errors = stderr_str.count(self._PYTHON_SETUP_FAILURE)
            if deleted_lib_errors > 0:
                logger.info(f"PyPerf skipped {deleted_lib_errors} processes with deleted libraries - "
                          f"this is normal for temporary/containerized environments")
        
        # Filter verbose debug output for temporary file systems
        if self._is_temporary_file_error(stderr_str):
            error_count = stderr_str.count(self._PYTHON_SETUP_FAILURE)
            logger.debug(f"PyPerf dump output (filtered {error_count} temporary file errors)", 
                        stdout=stdout, stderr="<temporary file errors filtered>")
        else:
            logger.debug("PyPerf dump output", stdout=stdout, stderr=stderr_str)

    def _dump(self) -> Path:
        assert self.is_running()
        assert self.process is not None  # for mypy
        self.process.send_signal(self._DUMP_SIGNAL)

        try:
            # important to not grab the transient data file - hence the following '.'
            output = wait_for_file_by_prefix(
                f"{self.output_path}.", self._DUMP_TIMEOUT, self._profiler_state.stop_event
            )
            stdout, stderr = self._read_process_standard_outputs()
            
            # Handle stderr processing using helper methods
            if stderr:
                stderr_str = stderr.decode('utf-8', errors='replace') if isinstance(stderr, bytes) else stderr
                self._process_pyperf_stderr(stderr_str, stdout)
            else:
                logger.debug("PyPerf dump output", stdout=stdout, stderr="")
            return output
        except TimeoutError:
            # error flow :(
            logger.warning("PyPerf dead/not responding, killing it")
            process = self.process  # save it
            exit_status, stderr, stdout = self._terminate()
            assert exit_status is not None, "PyPerf didn't exit after _terminate()!"
            assert isinstance(process.args, list) and all(
                isinstance(s, str) for s in process.args
            ), process.args  # mypy
            
            # Check if the error is related to deleted libraries before raising
            if stderr:
                stderr_str = stderr.decode('utf-8', errors='replace') if isinstance(stderr, bytes) else stderr
                if self._is_deleted_library_error(stderr_str):
                    deleted_lib_count = stderr_str.count(self._PYTHON_SETUP_FAILURE)
                    logger.info(f"PyPerf failed due to {deleted_lib_count} processes with deleted libraries - "
                              f"this is expected in containerized/temporary environments and doesn't indicate a real error")
            
            raise PythonEbpfError(exit_status, process.args, stdout, stderr)

    def snapshot(self) -> ProcessToProfileData:
        if self._profiler_state.stop_event.wait(self._duration):
            raise StopEventSetException()
        collapsed_path = self._dump()
        try:
            collapsed_text = collapsed_path.read_text()
        finally:
            # always remove, even if we get read/decode errors
            collapsed_path.unlink()
        parsed = parse_many_collapsed(collapsed_text)
        if self.add_versions:
            parsed = python._add_versions_to_stacks(parsed)
        profiles = {}
        for pid in parsed:
            try:
                process = Process(pid)
                # Because of https://github.com/intel/gprofiler/issues/764,
                # for now we only filter output of pyperf to return only profiles from chosen pids
                if self._profiler_state.processes_to_profile is not None:
                    if process not in self._profiler_state.processes_to_profile:
                        continue
                appid = application_identifiers.get_python_app_id(process)
                app_metadata = self._metadata.get_metadata(process)
                container_name = self._profiler_state.get_container_name(pid)
            except NoSuchProcess:
                appid = None
                app_metadata = None
                container_name = None

            profiles[pid] = ProfileData(parsed[pid], appid, app_metadata, container_name)
        return profiles

    def _terminate(self) -> Tuple[Optional[int], str, str]:
        exit_status: Optional[int] = None
        stdout: Union[str, bytes] = ""
        stderr: Union[str, bytes] = ""

        self._unregister_process_selectors()
        if self.is_running():
            assert self.process is not None  # for mypy
            self.process.terminate()  # okay to call even if process is already dead
            
            try:
                exit_status, stdout, stderr = reap_process(self.process)
            except AttributeError as e:
                # Check if this is the specific case where cleanup_completed_processes() has already
                # processed our process and closed its pipes, corrupting the internal state for communicate()
                pipes_already_closed = (
                    (self.process.stdout and self.process.stdout.closed) or
                    (self.process.stderr and self.process.stderr.closed)
                )
                if pipes_already_closed:
                    # This happens when cleanup_completed_processes() has already processed 
                    # our process and closed its pipes. This is actually okay - the process
                    # cleanup has already been handled by the global cleanup mechanism.
                    logger.debug("PyPerf process was already cleaned up by global subprocess cleanup - this is expected")
                    exit_status = self.process.poll()  # Get final exit status if available
                    # stdout/stderr remain empty as they were already processed
                else:
                    # Re-raise if it's a different AttributeError
                    raise
            
            self.process = None

        stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
        stderr = stderr.decode() if isinstance(stderr, bytes) else stderr

        assert self.process is None  # means we're not running
        self._staticx_cleanup()
        return exit_status, stdout, stderr

    def stop(self) -> None:
        exit_status, stdout, stderr = self._terminate()
        if exit_status is not None:
            logger.info(
                "Finished profiling Python processes with PyPerf", exit_status=exit_status, stdout=stdout, stderr=stderr
            )

    def is_running(self) -> bool:
        return self.process is not None
