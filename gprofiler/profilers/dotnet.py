#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import signal
import subprocess
from pathlib import Path

from threading import Event
from typing import Any, Dict, List, Optional

from psutil import Process

from gprofiler import merge
from gprofiler.exceptions import ProcessStoppedException, StopEventSetException
from gprofiler.gprofiler_types import ProfileData
from gprofiler.log import get_logger_adapter
from gprofiler.profilers.profiler_base import ProcessProfilerBase
from gprofiler.profilers.registry import register_profiler
from gprofiler.utils import pgrep_maps, random_prefix, removed_path, resource_path, run_process
from gprofiler.utils.process import process_comm
from gprofiler.utils.speedscope import load_speedscope_as_collapsed
logger = get_logger_adapter(__name__)

# class DotnetMetadata(ApplicationMetadata):
#     _DOTNET_VERSION_TIMEOUT = 3

#     def _get_dotnet_version(self, process: Process) -> str:
#         if not os.path.basename(process.exe()).startswith("ruby"):
#             # TODO: for dynamic executables, find the ruby binary that works with the loaded libruby, and
#             # check it instead. For static executables embedding libruby - :shrug:
#             raise NotImplementedError

#         dotnet_path = f"/proc/{get_process_nspid(process.pid)}/exe"

#         def _run_ruby_version() -> "CompletedProcess[bytes]":
#             return run_process(
#                 [
#                     dotnet_path,
#                     "--version",
#                 ],
#                 stop_event=self._stop_event,
#                 timeout=self._RUBY_VERSION_TIMEOUT,
#             )

#         return run_in_ns(["pid", "mnt"], _run_ruby_version, process.pid).stdout.decode().strip()

#     def make_application_metadata(self, process: Process) -> Dict[str, Any]:
#         # ruby version
#         version = self._get_dotnet_version(process)




@register_profiler(
    "dotnet",
    possible_modes=["dotnet-trace", "disabled"],
    supported_archs=["x86_64", "aarch64"],
    default_mode="dotnet-trace",
)
class DotnetProfiler(ProcessProfilerBase):
    
    RESOURCE_PATH = "dotnet/tools/dotnet-trace"
    MAX_FREQUENCY = 100
    _EXTRA_TIMEOUT = 10 

    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event: Optional[Event],
        storage_dir: str,
        profile_spawned_processes: bool,
        dotnet_mode: str,
    ):
        super().__init__(frequency, duration, stop_event, storage_dir)
        self._process: Optional[subprocess.Popen] = None
        assert dotnet_mode == "dotnet-trace", "Dotnet profiler should not be initialized, wrong dotnet-trace value given"
#        self._metadata = DotnetMetadata(self._stop_event)

    def _make_command(self, pid: int, output_path: str, duration: int) -> List[str]:
        result = run_process( 
            f"ls /proc/{pid}/root/tmp | grep diagnostic ",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            suppress_log=True,
            check=False,
        )
        socket_list = result.stdout.decode("utf-8").split('\n')
        tempdir = f"TMPDIR=/proc/{pid}/root/tmp"
        if int(socket_list[0].split("-")[2]) != 1: # better check for multiple pids/sockets in a docker?
            logger.info("NOT IN DOCKER")
            return [
                "sudo",
                tempdir,
                resource_path(self.RESOURCE_PATH),
                "collect",
                "--format",
                "speedscope",
                "--process-id",
                str(pid),
                "--duration",
                self._parse_duration(duration),
                #           str(self._frequency),
            ]
        else:
            logger.info("IN DOCKER")
            return [
                "sudo",
                tempdir,
                resource_path(self.RESOURCE_PATH),
                "collect",
                "--format",
                "speedscope",
                "--process-id",
                "1",
                "--duration",
                self._parse_duration(duration),
                #           str(self._frequency),
            ]

    def _profile_process(self, process: Process, duration: int, spawned: bool) -> ProfileData:
        logger.info(
            f"Profiling{' spawned' if spawned else ''} process {process.pid} with dotnet-trace",
            cmdline=" ".join(process.cmdline()),
            no_extra_to_server=True,
        )
        comm = process_comm(process)
        appid = None
        local_output_path = os.path.join(self._storage_dir, f"dotnet-trace.{random_prefix()}.{process.pid}.col")
        with removed_path(local_output_path):
            try:
                result = run_process(
                    self._make_command(process.pid, local_output_path, duration),
                    stop_event=self._stop_event,
                    timeout=self._duration + self._EXTRA_TIMEOUT,
                    kill_signal=signal.SIGKILL,
                )
                local_output_path = result.stdout.decode("utf-8").split("\t")[1].split("\n")[0]
            except ProcessStoppedException:
                raise StopEventSetException

            logger.info(f"Finished profiling process {process.pid} with dotnet")
            return ProfileData(load_speedscope_as_collapsed(Path(local_output_path), self._frequency), appid, {"dotnet_version": "LUJ"})

    def _parse_duration(self, duration: int):
        secs = duration
        time_list = []
        hours = 0
        mins = 0
        if secs>=3600:
            hours = (int)(secs/3600)
            secs = secs%3600
        if(secs>=60):
            mins = (int)(secs/60)
            secs = secs%60
        time_list.append(str(hours))
        time_list.append(str(mins))
        time_list.append(str(secs))
        dotnet_duration = "00"
        for period in time_list:
            print(period)
            if len(period)==1:
                dotnet_duration = dotnet_duration + ":0" + period
            else:
                dotnet_duration = dotnet_duration + ":" + period
        return dotnet_duration

    def _select_processes_to_profile(self) -> List[Process]:
        return pgrep_maps(r"(?:^.+/dotnet[^/]*$)")
