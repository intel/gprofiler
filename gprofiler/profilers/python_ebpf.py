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
    # 28mb (each symbol is 224 bytes), but needn't be physicall contiguous so don't care
    _SYMBOLS_MAP_SIZE = 131072
    _DUMP_SIGNAL = signal.SIGUSR2
    _DUMP_TIMEOUT = 5  # seconds
    _POLL_TIMEOUT = 10  # seconds
    _GET_OFFSETS_TIMEOUT = 5  # seconds
    _OUTPUT_READ_SIZE = 65536  # bytes read every cycle from stderr

    def __init__(
        self,
        frequency: int,
        duration: int,
        profiler_state: ProfilerState,
        *,
        add_versions: bool,
        user_stacks_pages: Optional[int] = None,
        verbose: bool,
    ):
        super().__init__(frequency, duration, profiler_state)
        self.process: Optional[Popen] = None
        self.process_selector: Optional[selectors.BaseSelector] = None
        self.output_path = Path(self._profiler_state.storage_dir) / f"pyperf.{random_prefix()}.col"
        self.add_versions = add_versions
        self.user_stacks_pages = user_stacks_pages
        self._kernel_offsets: Dict[str, int] = {}
        self._metadata = python.PythonMetadata(self._profiler_state.stop_event)
        self._verbose = verbose
        self._pyperf_staticx_tmpdir: Optional[Path] = None
        if os.environ.get("TMPDIR", None) is not None:
            # We want to create a new level of hirerachy in our current staticx tempdir.
            self._pyperf_staticx_tmpdir = Path(os.environ["TMPDIR"]) / ("pyperf_" + random_prefix())

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
        assert self.process_selector
        self.process_selector.close()
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
            output = cast(str, output)
            if key.fileobj is self.process.stdout:
                stdout = output
            elif key.fileobj is self.process.stderr:
                stderr = output
        return stdout, stderr

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
            logger.debug("PyPerf dump output", stdout=stdout, stderr=stderr)
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
            exit_status, stdout, stderr = reap_process(self.process)
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
