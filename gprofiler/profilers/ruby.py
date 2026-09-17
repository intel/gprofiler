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

import functools
import os
import signal
import time
from pathlib import Path
from typing import Any, Dict, List

from granulate_utils.linux.elf import get_elf_id
from granulate_utils.linux.process import get_mapped_dso_elf_id, is_process_basename_matching
from psutil import Process, NoSuchProcess, ZombieProcess

from gprofiler.exceptions import CalledProcessError, ProcessStoppedException, StopEventSetException
from gprofiler.gprofiler_types import ProfileData
from gprofiler.log import get_logger_adapter
from gprofiler.metadata import application_identifiers
from gprofiler.metadata.application_metadata import ApplicationMetadata
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.profiler_base import SpawningProcessProfilerBase
from gprofiler.profilers.registry import register_profiler
from gprofiler.utils import pgrep_maps, random_prefix, removed_path, resource_path, run_process
from gprofiler.utils.collapsed_format import parse_one_collapsed_file
from gprofiler.utils.process import is_process_running, process_comm, search_proc_maps

logger = get_logger_adapter(__name__)

# Ruby profiler error detection constants
_NO_SUCH_FILE_ERROR = "No such file or directory"
_DROPPED_TRACES_MARKER = "dropped"
_NO_SAMPLES_ERROR = "no profile samples were collected"


class RubyMetadata(ApplicationMetadata):
    _RUBY_VERSION_TIMEOUT = 3

    @functools.lru_cache(4096)
    def _get_ruby_version(self, process: Process) -> str:
        if not is_process_basename_matching(process, r"^ruby"):  # startswith match
            # TODO: for dynamic executables, find the ruby binary that works with the loaded libruby, and
            # check it instead. For static executables embedding libruby - :shrug:
            raise NotImplementedError
        version = self.get_exe_version(process)  # not using cached version here since this wrapper is a cache
        return version

    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        # ruby version
        version = self._get_ruby_version(process)

        # ruby elfid & libruby elfid, if exists
        exe_elfid = get_elf_id(f"/proc/{process.pid}/exe")
        libruby_elfid = get_mapped_dso_elf_id(process, "/libruby")

        metadata = {"ruby_version": version, "exe_elfid": exe_elfid, "libruby_elfid": libruby_elfid}

        metadata.update(super().make_application_metadata(process))
        return metadata


@register_profiler(
    "Ruby",
    possible_modes=["rbspy", "disabled"],
    supported_archs=["x86_64", "aarch64"],
    default_mode="rbspy",
    supported_profiling_modes=["cpu"],
)
class RbSpyProfiler(SpawningProcessProfilerBase):
    RESOURCE_PATH = "ruby/rbspy"
    MAX_FREQUENCY = 100
    _EXTRA_TIMEOUT = 10  # extra time like given to py-spy
    DETECTED_RUBY_PROCESSES_REGEX = r"(^.+/ruby[^/]*$)"

    def __init__(
        self,
        frequency: int,
        duration: int,
        profiler_state: ProfilerState,
        ruby_mode: str,
        min_duration: int = 10,
    ):
        super().__init__(frequency, duration, profiler_state, min_duration)
        assert ruby_mode == "rbspy", "Ruby profiler should not be initialized, wrong ruby_mode value given"
        self._metadata = RubyMetadata(self._profiler_state.stop_event)

    def _is_process_exit_during_profiling_error(self, stderr: str) -> bool:
        """Check if error indicates process exited during profiling."""
        return _NO_SUCH_FILE_ERROR in stderr and _DROPPED_TRACES_MARKER in stderr.lower()
    
    def _is_no_samples_collected_error(self, stderr: str) -> bool:
        """Check if error indicates no samples were collected."""
        return _NO_SAMPLES_ERROR in stderr.lower()
    
    def _count_dropped_traces(self, stderr: str) -> int:
        """Count number of dropped traces from rbspy stderr."""
        return stderr.count(_NO_SUCH_FILE_ERROR)

    def _make_command(self, pid: int, output_path: str, duration: int) -> List[str]:
        return [
            resource_path(self.RESOURCE_PATH),
            "record",
            "--silent",
            "-r",
            str(self._frequency),
            "-d",
            str(duration),
            "--nonblocking",  # Don't pause the ruby process when collecting stack samples.
            "--oncpu",  # only record when CPU is active
            "--format=collapsed",
            "--file",
            output_path,
            "--raw-file",
            "/dev/null",  # We don't need that file and there is no other way to avoid creating it
            "-p",
            str(pid),
        ]

    def _profile_process(self, process: Process, duration: int, spawned: bool) -> ProfileData:
        # Use full duration since young processes are now skipped entirely in _should_skip_process
        actual_duration = duration
        
        logger.info(
            f"Profiling{' spawned' if spawned else ''} process {process.pid} with rbspy",
            cmdline=" ".join(process.cmdline()),
            no_extra_to_server=True,
        )
        
        comm = process_comm(process)
        container_name = self._profiler_state.get_container_name(process.pid)
        app_metadata = self._metadata.get_metadata(process)
        appid = application_identifiers.get_ruby_app_id(process)

        local_output_path = os.path.join(self._profiler_state.storage_dir, f"rbspy.{random_prefix()}.{process.pid}.col")
        with removed_path(local_output_path):
            try:
                # Check if process is still alive before starting rbspy
                if not is_process_running(process):
                    logger.debug(f"Process {process.pid} exited before rbspy could start")
                    return ProfileData(
                        self._profiling_error_stack("warning", "process exited before profiling", comm),
                        appid, app_metadata, container_name
                    )
                
                run_process(
                    self._make_command(process.pid, local_output_path, actual_duration),
                    stop_event=self._profiler_state.stop_event,
                    timeout=actual_duration + self._EXTRA_TIMEOUT,
                    kill_signal=signal.SIGKILL,
                )
            except ProcessStoppedException:
                raise StopEventSetException
            except CalledProcessError as e:
                # Enhanced error handling for rbspy-specific issues
                stderr_str = e.stderr if isinstance(e.stderr, str) else ""
                
                if self._is_process_exit_during_profiling_error(stderr_str):
                    dropped_count = self._count_dropped_traces(stderr_str)
                    logger.info(f"Process {process.pid} exited during profiling, rbspy dropped {dropped_count} stack traces - this is normal for dynamic processes")
                    return ProfileData(
                        self._profiling_error_stack("info", "process exited during profiling", comm),
                        appid, app_metadata, container_name
                    )
                elif self._is_no_samples_collected_error(stderr_str):
                    logger.info(f"No samples collected for process {process.pid}, likely too short-lived")
                    return ProfileData(
                        self._profiling_error_stack("info", "no samples collected, process too short-lived", comm),
                        appid, app_metadata, container_name
                    )
                
                # Re-raise for other errors
                raise

            logger.info(f"Finished profiling process {process.pid} with rbspy")
            return ProfileData(
                parse_one_collapsed_file(Path(local_output_path), comm), appid, app_metadata, container_name
            )

    def _select_processes_to_profile(self) -> List[Process]:
        return pgrep_maps(self.DETECTED_RUBY_PROCESSES_REGEX)

    def _should_profile_process(self, process: Process) -> bool:
        return search_proc_maps(process, self.DETECTED_RUBY_PROCESSES_REGEX) is not None and not self._should_skip_process(process)
    
    def _should_skip_process(self, process: Process) -> bool:
        # Skip short-lived processes - if a process is younger than min_duration,
        # it's likely to exit before profiling completes
        try:
            process_age = self._get_process_age(process)
            if process_age < self._min_duration:
                logger.debug(f"Skipping young Ruby process {process.pid} (age: {process_age:.1f}s < min_duration: {self._min_duration}s)")
                return True
        except Exception as e:
            logger.debug(f"Could not determine age for Ruby process {process.pid}: {e}")
        
        return False
