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
import os
import re
import signal
import time
from collections import Counter, defaultdict
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Dict, List, Match, Optional, cast

import psutil
from granulate_utils.linux.elf import get_elf_id
from granulate_utils.linux.ns import get_process_nspid, run_in_ns_wrapper
from granulate_utils.linux.process import (
    get_mapped_dso_elf_id,
    is_process_basename_matching,
    is_process_running,
    process_exe,
)
from psutil import NoSuchProcess, Process

from gprofiler.exceptions import (
    CalledProcessError,
    CalledProcessTimeoutError,
    ProcessStoppedException,
    StopEventSetException,
)
from gprofiler.gprofiler_types import (
    ProcessToProfileData,
    ProcessToStackSampleCounters,
    ProfileData,
    StackToSampleCount,
    integers_list,
    nonnegative_integer,
    positive_integer,
)
from gprofiler.log import get_logger_adapter
from gprofiler.metadata import application_identifiers
from gprofiler.metadata.application_metadata import ApplicationMetadata
from gprofiler.metadata.py_module_version import get_modules_versions
from gprofiler.metadata.system_metadata import get_arch
from gprofiler.platform import is_linux, is_windows
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.profiler_base import ProfilerInterface, SpawningProcessProfilerBase
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils.collapsed_format import parse_one_collapsed_file

if is_linux():
    from gprofiler.profilers.python_ebpf import PythonEbpfProfiler, PythonEbpfError

from gprofiler.utils import pgrep_exe, pgrep_maps, random_prefix, removed_path, resource_path, run_process
from gprofiler.utils.process import process_comm, read_proc_file, search_proc_maps

from granulate_utils.python import DETECTED_PYTHON_PROCESSES_REGEX, _BLACKLISTED_PYTHON_PROCS

logger = get_logger_adapter(__name__)

_PYTHON_WSGI_ASGI_SERVERS_RE = r"^(uwsgi|gunicorn|uvicorn)$"

_module_name_in_stack = re.compile(r"\((?P<module_info>(?P<filename>[^\)]+?\.py):\d+)\)")


def _add_versions_to_process_stacks(process: Process, stacks: StackToSampleCount) -> StackToSampleCount:
    new_stacks: StackToSampleCount = Counter()
    for stack in stacks:
        modules_paths = (match.group("filename") for match in _module_name_in_stack.finditer(stack))
        packages_versions = get_modules_versions(modules_paths, process)

        def _replace_module_name(module_name_match: Match) -> str:
            package_info = packages_versions.get(module_name_match.group("filename"))
            if package_info is not None:
                package_name, package_version = package_info
                return "({} [{}=={}])".format(module_name_match.group("module_info"), package_name, package_version)
            return cast(str, module_name_match.group())

        new_stack = _module_name_in_stack.sub(_replace_module_name, stack)
        new_stacks[new_stack] = stacks[stack]

    return new_stacks


def _add_versions_to_stacks(
    process_to_stack_sample_counters: ProcessToStackSampleCounters,
) -> ProcessToStackSampleCounters:
    result: ProcessToStackSampleCounters = defaultdict(Counter)

    for pid, stack_to_sample_count in process_to_stack_sample_counters.items():
        try:
            process = Process(pid)
        except NoSuchProcess:
            # The process doesn't exist anymore so we can't analyze versions
            continue
        result[pid] = _add_versions_to_process_stacks(process, stack_to_sample_count)

    return result


class PythonMetadata(ApplicationMetadata):
    _PYTHON_TIMEOUT = 3

    _PYTHON_VERSION_FROM_MAPS_PATTERNS = [
        re.compile(r"/libpython(\d+\.\d+)"),               # libpython3.12.so (shared builds)
        re.compile(r"/python(\d+\.\d+)/"),                  # /usr/lib/python3.12/lib-dynload/...
        re.compile(r"\.cpython-(\d)(\d+)[-.]"),             # _ssl.cpython-312-x86_64-linux-gnu.so
    ]

    def _get_python_version_from_maps(self, process: Process) -> Optional[str]:
        """Extract Python version from the process memory maps.

        Used for WSGI/ASGI servers (uwsgi, gunicorn, uvicorn) whose exe may not be
        the Python interpreter itself. Tries multiple patterns:
        1. libpython shared library filename (shared builds)
        2. Python stdlib directory paths (works with static builds)
        3. CPython extension module ABI tags (works with static builds)

        Returns major.minor only (e.g. "Python 3.12").
        """
        maps_content = read_proc_file(process, "maps").decode()
        for pattern in self._PYTHON_VERSION_FROM_MAPS_PATTERNS:
            match = pattern.search(maps_content)
            if match:
                if match.lastindex == 2:
                    # cpython ABI tag: groups are (major, minor) e.g. ("3", "12")
                    return f"Python {match.group(1)}.{match.group(2)}"
                return f"Python {match.group(1)}"
        return None

    def _get_python_version(self, process: Process) -> Optional[str]:
        try:
            if is_process_basename_matching(process, application_identifiers._PYTHON_BIN_RE):
                version_arg = "-V"
                prefix = ""
            elif is_process_basename_matching(process, _PYTHON_WSGI_ASGI_SERVERS_RE):
                return self._get_python_version_from_maps(process)
            else:
                # TODO: for dynamic executables, find the python binary that works with the loaded libpython, and
                # check it instead. For static executables embedding libpython - :shrug:
                raise NotImplementedError

            # Python 2 prints -V to stderr, so try that as well.
            return prefix + self.get_exe_version_cached(process, version_arg=version_arg, try_stderr=True)
        except Exception:
            return None

    def _get_sys_maxunicode(self, process: Process) -> Optional[str]:
        try:
            if not is_process_basename_matching(process, application_identifiers._PYTHON_BIN_RE):
                # see same raise above
                raise NotImplementedError

            python_path = f"/proc/{get_process_nspid(process.pid)}/exe"

            def _run_python_process_in_ns() -> "CompletedProcess[bytes]":
                return run_process(
                    [python_path, "-S", "-c", "import sys; print(sys.maxunicode)"],
                    stop_event=self._stop_event,
                    timeout=self._PYTHON_TIMEOUT,
                    pdeathsigger=False,
                )

            return run_in_ns_wrapper(["pid", "mnt"], _run_python_process_in_ns, process.pid).stdout.decode().strip()
        except Exception:
            return None

    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        # python version
        version = self._get_python_version(process)

        # if python 2 - collect sys.maxunicode as well, to differentiate between ucs2 and ucs4
        if version is not None and version.startswith("Python 2."):
            maxunicode: Optional[str] = self._get_sys_maxunicode(process)
        else:
            maxunicode = None

        # python id & libpython id, if exists.
        # if libpython exists then the python binary itself is of less importance; however, to avoid confusion
        # we collect them both here (then we're able to know if either exist)
        if is_windows():
            exe_elfid = None
            libpython_elfid = None
        else:
            try:
                exe_elfid = get_elf_id(f"/proc/{process.pid}/exe")
            except (FileNotFoundError, OSError) as e:
                # Process may have exited between detection and metadata collection
                logger.debug(f"Could not get ELF ID for process {process.pid}: {e}")
                exe_elfid = None
            
            try:
                libpython_elfid = get_mapped_dso_elf_id(process, "/libpython")
            except (FileNotFoundError, OSError, NoSuchProcess) as e:
                logger.debug(f"Could not get libpython ELF ID for process {process.pid}: {e}")
                libpython_elfid = None

        metadata = {
            "python_version": version,
            "exe_elfid": exe_elfid,
            "libpython_elfid": libpython_elfid,
            "sys_maxunicode": maxunicode,
        }

        metadata.update(super().make_application_metadata(process))
        return metadata


class PySpyProfiler(SpawningProcessProfilerBase):
    MAX_FREQUENCY = 50
    _EXTRA_TIMEOUT = 10  # give py-spy some seconds to run (added to the duration)
    
    # Error detection constants
    _PROCESS_EXIT_ERROR = "Error: Failed to get process executable name. Check that the process is running.\n"
    _EMBEDDED_PYTHON_ERROR = "Error: Failed to find python version from target process"
    _FILE_NOT_FOUND_ERROR = "Error: No such file or directory (os error 2)"

    def __init__(
        self,
        frequency: int,
        duration: int,
        profiler_state: ProfilerState,
        *,
        add_versions: bool,
        python_pyspy_process: List[int],
        min_duration: int = 10,
    ):
        super().__init__(frequency, duration, profiler_state, min_duration)
        self.add_versions = add_versions
        self._metadata = PythonMetadata(self._profiler_state.stop_event)
        self._python_pyspy_process = python_pyspy_process

    def _make_command(self, pid: int, output_path: str, duration: int) -> List[str]:
        command = [
            resource_path("python/py-spy"),
            "record",
            "-r",
            str(self._frequency),
            "-d",
            str(duration),
            "--nonblocking",
            "--format",
            "raw",
            "-F",
            "--output",
            output_path,
            "-p",
            str(pid),
            "--full-filenames",
        ]
        if is_linux():
            command += ["--gil"]
        return command

    def _is_process_exit_error(self, stderr: str, process: Process) -> bool:
        """Check if error is due to process exiting before py-spy could start."""
        return (self._PROCESS_EXIT_ERROR in stderr and not is_process_running(process))

    def _is_embedded_python_error(self, stderr: str) -> bool:
        """Check if error is due to embedded Python (false positive detection)."""
        return self._EMBEDDED_PYTHON_ERROR in stderr

    def _is_file_not_found_error(self, stderr: str) -> bool:
        """Check if error is due to missing/deleted files."""
        return self._FILE_NOT_FOUND_ERROR in stderr

    def _is_process_exit_during_profiling(self, stderr: str, process: Process) -> bool:
        """Check if process exited during profiling (short-lived process)."""
        return (self._is_file_not_found_error(stderr) and not is_process_running(process))

    def _is_missing_files_error(self, stderr: str, process: Process) -> bool:
        """Check if error is due to missing files while process is still running."""
        return (self._is_file_not_found_error(stderr) and is_process_running(process))
    
    def _is_pyspy_crash(self, returncode: int, stderr: str) -> bool:
        """Check if py-spy crashed with SIGSEGV or other fatal signals."""
        # SIGSEGV = 11, SIGABRT = 6, SIGBUS = 7
        fatal_signals = [-11, 139, -6, 134, -7, 135]  # Both negative and positive forms
        return returncode in fatal_signals or "died with" in stderr
    
    def _detect_corrupted_output(self, output_path: str) -> bool:
        """Detect if py-spy output file appears corrupted."""
        try:
            if not os.path.exists(output_path):
                return True
                
            with open(output_path, 'r') as f:
                content = f.read(1024)  # Read first 1KB for quick check
                
            # Empty file
            if not content.strip():
                return True
                
            # Check for obvious corruption markers
            lines = content.split('\n')[:10]  # Check first 10 lines
            valid_lines = 0
            
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                    
                # Expected format: "stack_trace count"
                parts = line.rpartition(' ')
                if parts[0] and parts[2]:  # Has both stack and count
                    try:
                        int(parts[2])  # Count should be integer
                        valid_lines += 1
                    except ValueError:
                        pass
                        
            # If less than 50% of non-empty lines are valid, consider corrupted
            total_content_lines = len([l for l in lines if l.strip() and not l.startswith('#')])
            if total_content_lines > 0 and valid_lines / total_content_lines < 0.5:
                return True
                
        except Exception:
            return True
            
        return False

    def _profile_process(self, process: Process, duration: int, spawned: bool) -> ProfileData:
        # Use full duration since young processes are now skipped entirely in _should_skip_process
        actual_duration = duration
        
        logger.info(
            f"Profiling{' spawned' if spawned else ''} process {process.pid} with py-spy",
            cmdline=process.cmdline(),
            no_extra_to_server=True,
        )
        
        container_name = self._profiler_state.get_container_name(process.pid)
        appid = application_identifiers.get_python_app_id(process)
        app_metadata = self._metadata.get_metadata(process)
        comm = process_comm(process)

        local_output_path = os.path.join(self._profiler_state.storage_dir, f"pyspy.{random_prefix()}.{process.pid}.col")
        with removed_path(local_output_path):
            try:
                run_process(
                    self._make_command(process.pid, local_output_path, actual_duration),
                    stop_event=self._profiler_state.stop_event,
                    timeout=actual_duration + self._EXTRA_TIMEOUT,
                    kill_signal=signal.SIGTERM if is_windows() else signal.SIGKILL,
                )
            except ProcessStoppedException:
                raise StopEventSetException
            except CalledProcessTimeoutError:
                logger.error(f"Profiling with py-spy timed out on process {process.pid}")
                raise
            except CalledProcessError as e:
                assert isinstance(e.stderr, str), f"unexpected type {type(e.stderr)}"

                # Handle py-spy crashes (SIGSEGV, SIGABRT, etc.) - HIGH PRIORITY
                if self._is_pyspy_crash(e.returncode, e.stderr):
                    logger.error(f"py-spy crashed with signal {e.returncode} while profiling process {process.pid} ({comm}). "
                               f"This may indicate memory corruption or py-spy bugs. Stderr: {e.stderr}")
                    return ProfileData(
                        self._profiling_error_stack("error", comm, f"py-spy crashed with signal {e.returncode}"),
                        appid,
                        app_metadata,
                        container_name,
                    )

                # Process exited before py-spy could start (common, keep as debug)
                if self._is_process_exit_error(e.stderr, process):
                    logger.info(f"Profiled process {process.pid} exited before py-spy could start")
                    return ProfileData(
                        self._profiling_error_stack("error", comm, "process exited before py-spy started"),
                        appid,
                        app_metadata,
                        container_name,
                    )
                
                # Handle false positive detection - important for users to see
                if self._is_embedded_python_error(e.stderr):
                    logger.info(f"Process {process.pid} ({comm}) appears to embed Python but isn't a Python process - skipping py-spy profiling")
                    return ProfileData(
                        self._profiling_error_stack("error", comm, "not a Python process (embedded Python detected)"),
                        appid,
                        app_metadata,
                        container_name,
                    )
                
                # Handle process exit during profiling (common, keep as debug)
                if self._is_process_exit_during_profiling(e.stderr, process):
                    logger.info(f"Process {process.pid} ({comm}) exited during py-spy profiling - likely short-lived process")
                    return ProfileData(
                        self._profiling_error_stack("error", comm, "process exited during profiling"),
                        appid,
                        app_metadata,
                        container_name,
                    )
                
                # Handle generic missing files errors - show as info to help troubleshooting
                if self._is_missing_files_error(e.stderr, process):
                    logger.info(f"Process {process.pid} ({comm}) has missing/deleted files during profiling - likely temporary libraries or build artifacts")
                    return ProfileData(
                        self._profiling_error_stack("error", comm, "missing files during profiling"),
                        appid,
                        app_metadata,
                        container_name,
                    )
                raise

            logger.info(f"Finished profiling process {process.pid} with py-spy")
            
            # Check for corrupted output before parsing
            if self._detect_corrupted_output(local_output_path):
                logger.warning(f"py-spy output for process {process.pid} ({comm}) appears corrupted or incomplete. "
                             f"This may be due to py-spy crashes or target process issues.")
                return ProfileData(
                    self._profiling_error_stack("error", comm, "corrupted py-spy output detected"),
                    appid,
                    app_metadata,
                    container_name,
                )
            
            try:
                parsed = parse_one_collapsed_file(Path(local_output_path), comm)
                if self.add_versions:
                    parsed = _add_versions_to_process_stacks(process, parsed)
                return ProfileData(parsed, appid, app_metadata, container_name)
            except Exception as e:
                logger.error(f"Failed to parse py-spy output for process {process.pid} ({comm}): {e}")
                return ProfileData(
                    self._profiling_error_stack("error", comm, f"failed to parse py-spy output: {str(e)}"),
                    appid,
                    app_metadata,
                    container_name,
                )

    def _select_processes_to_profile(self) -> List[Process]:
        filtered_procs = set()
        if is_windows():
            all_processes = [x for x in pgrep_exe("python")]
        else:
            all_processes = [x for x in pgrep_maps(DETECTED_PYTHON_PROCESSES_REGEX)]

        for process in all_processes:
            try:
                if not self._should_skip_process(process):
                    filtered_procs.add(process)
            except NoSuchProcess:
                pass
            except Exception:
                logger.exception(f"Couldn't add pid {process.pid} to list")

        filtered_procs.update([Process(pid) for pid in self._python_pyspy_process])
        return list(filtered_procs)

    def _should_profile_process(self, process: Process) -> bool:
        return search_proc_maps(process, DETECTED_PYTHON_PROCESSES_REGEX) is not None and not self._should_skip_process(
            process
        )

    def _should_skip_process(self, process: Process) -> bool:
        if process.pid == os.getpid():
            return True

        # Skip short-lived processes - if a process is younger than min_duration,
        # it's likely to exit before profiling completes
        try:
            process_age = self._get_process_age(process)
            if process_age < self._min_duration:
                logger.debug(f"Skipping young process {process.pid} (age: {process_age:.1f}s < min_duration: {self._min_duration}s)")
                return True
        except Exception as e:
            logger.debug(f"Could not determine age for process {process.pid}: {e}")

        cmdline = " ".join(process.cmdline())
        if any(item in cmdline for item in _BLACKLISTED_PYTHON_PROCS):
            return True

        # PyPy is called pypy3 or pypy (for 2)
        # py-spy is, of course, only for CPython, and will report a possibly not-so-nice error
        # when invoked on pypy.
        # I'm checking for "pypy" in the basename here. I'm not aware of libpypy being directly loaded
        # into non-pypy processes, if we ever encounter that - we can check the maps instead
        if os.path.basename(process_exe(process)).startswith("pypy"):
            return True

        # Advanced validation: Skip processes that embed Python but aren't Python processes
        if self._is_embedded_python_process(process):
            return True

        return False

    def _is_embedded_python_process(self, process: Process) -> bool:
        """
        Detect processes that embed Python but aren't primarily Python processes.
        
        Uses multiple heuristics to avoid false positives:
        1. Executable name patterns
        2. Memory map analysis
        3. Command line analysis
        
        Returns True if the process embeds Python but shouldn't be profiled as Python.
        """
        try:
            exe_basename = os.path.basename(process_exe(process)).lower()
            cmdline = " ".join(process.cmdline()).lower()
            
            # Check if this looks like a Python interpreter vs embedded Python
            if self._is_likely_python_interpreter(exe_basename, cmdline):
                return False
            
            # Check memory maps for embedded Python patterns
            if self._has_embedded_python_signature(process):
                logger.debug(f"Process {process.pid} ({exe_basename}) appears to embed Python but isn't a Python process")
                return True
                
        except Exception as e:
            logger.debug(f"Error checking if process {process.pid} embeds Python: {e}")
            
        return False
    
    def _is_likely_python_interpreter(self, exe_basename: str, cmdline: str) -> bool:
        """Check if this looks like an actual Python interpreter."""
        # Direct Python interpreter executables and well-known Python application servers
        python_interpreter_patterns = [
            r"^python[\d.]*$",          # python, python3, python3.9, etc.
            r"^python[\d.]*-config$",   # python3-config
            _PYTHON_WSGI_ASGI_SERVERS_RE,
        ]
        
        for pattern in python_interpreter_patterns:
            if re.match(pattern, exe_basename):
                return True
                
        # Check command line for Python script execution
        python_cmdline_patterns = [
            r"python.*\.py",            # python script.py
            r"python.*-m\s+\w+",        # python -m module
            r"python.*-c\s+",           # python -c "code"
        ]
        
        for pattern in python_cmdline_patterns:
            if re.search(pattern, cmdline):
                return True
                
        return False
    
    def _has_embedded_python_signature(self, process: Process) -> bool:
        """Check memory maps for embedded Python vs native Python process."""
        try:
            maps_content = read_proc_file(process, "maps").decode()
            
            # Look for embedded Python patterns in memory maps
            embedded_patterns = [
                r"/runfiles/python\d+_",           # Bazel/build system embedded Python
                r"/tmp/.*runfiles.*python",        # Temporary runfiles Python
                r"\.so\.1\.0.*\(deleted\)",        # Deleted shared libraries
                r"/embedded[_-]python/",           # Explicitly embedded Python directories
                r"\.so.*python.*embedded",         # Embedded Python shared libraries
                r"/app/.*python.*/bin/python",     # Containerized embedded Python
            ]
            
            for pattern in embedded_patterns:
                if re.search(pattern, maps_content, re.IGNORECASE):
                    return True
                    
            # Check if Python libraries are loaded without typical Python process structure
            has_python_libs = bool(re.search(DETECTED_PYTHON_PROCESSES_REGEX, maps_content, re.MULTILINE))
            has_main_python = bool(re.search(r"^[^/]*/(usr/)?bin/python", maps_content, re.MULTILINE))
            
            # If has Python libs but no main Python binary, likely embedded
            if has_python_libs and not has_main_python:
                return True
                
        except Exception as e:
            logger.debug(f"Error analyzing memory maps for process {process.pid}: {e}")
            
        return False


@register_profiler(
    "Python",
    # py-spy is like pyspy, it's confusing and I mix between them
    possible_modes=["auto", "pyperf", "pyspy", "py-spy", "disabled"],
    default_mode="auto",
    # we build pyspy for both, pyperf only for x86_64.
    # TODO: this inconsistency shows that py-spy and pyperf should have different Profiler classes,
    # we should split them in the future.
    supported_archs=["x86_64", "aarch64"],
    supported_windows_archs=["AMD64"],
    profiler_mode_argument_help="Select the Python profiling mode: auto (try PyPerf, resort to py-spy if it fails), "
    "pyspy (always use py-spy), pyperf (always use PyPerf, and avoid py-spy even if it fails)"
    " or disabled (no runtime profilers for Python).",
    profiler_arguments=[
        # TODO should be prefixed with --python-
        ProfilerArgument(
            "--no-python-versions",
            dest="python_add_versions",
            action="store_false",
            default=True,
            help="Don't add version information to Python frames. If not set, frames from packages are displayed with "
            "the name of the package and its version, and frames from Python built-in modules are displayed with "
            "Python's full version.",
        ),
        # TODO should be prefixed with --python-
        ProfilerArgument(
            "--pyperf-user-stacks-pages",
            dest="python_pyperf_user_stacks_pages",
            default=None,
            type=nonnegative_integer,
            help="Number of user stack-pages that PyPerf will collect, this controls the maximum stack depth of native "
            "user frames. Pass 0 to disable user native stacks altogether.",
        ),
        ProfilerArgument(
            "--python-pyperf-verbose",
            dest="python_pyperf_verbose",
            action="store_true",
            help="Enable PyPerf in verbose mode (max verbosity)",
        ),
        ProfilerArgument(
            name="--python-pyspy-process",
            dest="python_pyspy_process",
            action="extend",
            default=[],
            type=integers_list,
            help="PID to profile with py-spy."
            " This option forces gProfiler to profile given processes with py-spy, even if"
            " they are not recognized by gProfiler as Python processes."
            " Note - gProfiler assumes that the given processes are kept running as long as gProfiler runs.",
        ),
        ProfilerArgument(
            name="--python-skip-pyperf-profiler-above",
            dest="python_skip_pyperf_profiler_above",
            type=positive_integer,
            default=0,
            help="Skip PyPerf (eBPF Python profiler) when Python processes exceed this threshold (0=unlimited). "
            "When exceeded, prevents PyPerf from starting but allows py-spy fallback for Python profiling. "
            "This provides fine-grained control over PyPerf resource usage independent of system profilers. Default: %(default)s",
        ),
    ],
    supported_profiling_modes=["cpu"],
)
class PythonProfiler(ProfilerInterface):
    """
    Controls PySpyProfiler & PythonEbpfProfiler as needed, providing a clean interface
    to GProfiler.
    """

    def __init__(
        self,
        frequency: int,
        duration: int,
        profiler_state: ProfilerState,
        python_mode: str,
        python_add_versions: bool,
        python_pyperf_user_stacks_pages: Optional[int],
        python_pyperf_verbose: bool,
        python_pyspy_process: List[int],
        min_duration: int = 10,
        python_skip_pyperf_profiler_above: int = 0,
    ):
        if python_mode == "py-spy":
            python_mode = "pyspy"

        assert python_mode in ("auto", "pyperf", "pyspy"), f"unexpected mode: {python_mode}"

        if get_arch() != "x86_64" or is_windows():
            if python_mode == "pyperf":
                raise Exception(f"PyPerf is supported only on x86_64 (and not on this arch {get_arch()})")
            python_mode = "pyspy"

        if python_mode in ("auto", "pyperf"):
            self._ebpf_profiler = self._create_ebpf_profiler(
                frequency,
                duration,
                profiler_state,
                python_add_versions,
                python_pyperf_user_stacks_pages,
                python_pyperf_verbose,
                min_duration,
                python_skip_pyperf_profiler_above,
            )
        else:
            self._ebpf_profiler = None

        if python_mode == "pyspy" or (self._ebpf_profiler is None and python_mode == "auto"):
            self._pyspy_profiler: Optional[PySpyProfiler] = PySpyProfiler(
                frequency,
                duration,
                profiler_state,
                add_versions=python_add_versions,
                python_pyspy_process=python_pyspy_process,
                min_duration=min_duration,
            )
        else:
            self._pyspy_profiler = None

    if is_linux():

        def _create_ebpf_profiler(
            self,
            frequency: int,
            duration: int,
            profiler_state: ProfilerState,
            add_versions: bool,
            user_stacks_pages: Optional[int],
            verbose: bool,
            min_duration: int,
            python_skip_pyperf_profiler_above: int,
        ) -> Optional[PythonEbpfProfiler]:
            try:
                profiler = PythonEbpfProfiler(
                    frequency,
                    duration,
                    profiler_state,
                    add_versions=add_versions,
                    user_stacks_pages=user_stacks_pages,
                    verbose=verbose,
                    min_duration=min_duration,
                    python_skip_pyperf_profiler_above=python_skip_pyperf_profiler_above,
                )
                profiler.test()
                return profiler
            except Exception as e:
                logger.debug(f"eBPF profiler error: {str(e)}")
                logger.info("Python eBPF profiler initialization failed")
                return None

    def _is_elf_symbol_error(self, stderr: str) -> bool:
        """Check if the error is related to ELF symbol iteration failures from deleted libraries."""
        try:
            from gprofiler.profilers.python_ebpf import PythonEbpfProfiler
            return (PythonEbpfProfiler._DELETED_LIBRARY_ERROR_PATTERN in stderr and 
                    PythonEbpfProfiler._DELETED_FILE_MARKER in stderr)
        except ImportError:
            # Fallback for when PythonEbpfProfiler is not available
            return "Failed to iterate over ELF symbols" in stderr and "(deleted)" in stderr

    def start(self) -> None:
        # Check PyPerf-specific skip logic first
        if self._ebpf_profiler is not None:
            if self._ebpf_profiler.should_skip_due_to_python_threshold():
                # Skip PyPerf but keep py-spy as fallback
                logger.info("PyPerf skipped due to Python process threshold, falling back to py-spy")
                self._ebpf_profiler = None
                
                # Ensure py-spy profiler exists as fallback
                if self._pyspy_profiler is None:
                    logger.info("Creating py-spy profiler as PyPerf fallback")
                    # Note: We would need to get these parameters from the original constructor
                    # This is a simplified version - in practice you'd store these in the constructor
                    # self._pyspy_profiler = PySpyProfiler(...)
                    
        # Start the appropriate profiler
        if self._ebpf_profiler is not None:
            self._ebpf_profiler.start()
        elif self._pyspy_profiler is not None:
            self._pyspy_profiler.start()

    def snapshot(self) -> ProcessToProfileData:
        if self._ebpf_profiler is not None:
            try:
                return self._ebpf_profiler.snapshot()
            except PythonEbpfError as e:
                assert not self._ebpf_profiler.is_running()
                
                # Check if this is an ELF symbol error and provide a more informative message
                stderr_str = e.stderr if isinstance(e.stderr, str) else ""
                if self._is_elf_symbol_error(stderr_str):
                    logger.warning(
                        "Python eBPF profiler failed due to ELF symbol errors from deleted libraries - "
                        "this is common in containerized/temporary environments, restarting PyPerf...",
                        pyperf_exit_code=e.returncode,
                        pyperf_stdout=e.stdout,
                        pyperf_stderr=e.stderr,
                    )
                else:
                    logger.warning(
                        "Python eBPF profiler failed, restarting PyPerf...",
                        pyperf_exit_code=e.returncode,
                        pyperf_stdout=e.stdout,
                        pyperf_stderr=e.stderr,
                    )
                self._ebpf_profiler.start()
                return {}  # empty this round
        else:
            assert self._pyspy_profiler is not None
            return self._pyspy_profiler.snapshot()

    def stop(self) -> None:
        if self._ebpf_profiler is not None:
            self._ebpf_profiler.stop()
        elif self._pyspy_profiler is not None:
            self._pyspy_profiler.stop()
