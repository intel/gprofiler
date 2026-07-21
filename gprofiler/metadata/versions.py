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
from subprocess import CompletedProcess
from threading import Event

from granulate_utils.linux.ns import get_process_nspid, run_in_ns_wrapper
from psutil import NoSuchProcess, Process

from gprofiler.utils import get_pdeathsigger_path, run_process_as_target


def get_exe_version(
    process: Process,
    stop_event: Event,
    get_version_timeout: int,
    version_arg: str = "--version",
    try_stderr: bool = False,
) -> str:
    """
    Runs {process.exe()} --version in the appropriate namespace.

    Security: Executes with the target process's UID/GID to prevent
    privilege escalation if the binary is attacker-controlled.
    """
    exe_path = f"/proc/{get_process_nspid(process.pid)}/exe"

    # Get credentials and pdeathsigger path BEFORE entering namespace
    # (psutil can't resolve host PIDs inside namespace, and resource_path may hang)
    target_uid = process.uids().real
    target_gid = process.gids().real
    pdeathsigger_path = get_pdeathsigger_path()

    def _run_get_version() -> "CompletedProcess[bytes]":
        return run_process_as_target(
            [exe_path, version_arg],
            target_uid=target_uid,
            target_gid=target_gid,
            stop_event=stop_event,
            timeout=get_version_timeout,
            pdeathsigger_path=pdeathsigger_path,
        )

    try:
        cp = run_in_ns_wrapper(["pid", "mnt"], _run_get_version, process.pid)
    except FileNotFoundError as e:
        if not process.is_running():
            raise NoSuchProcess(process.pid)
        else:
            raise e

    stdout = cp.stdout.decode().strip()
    # return stderr if stdout is empty, some apps print their version to stderr.
    if try_stderr and not stdout:
        return cp.stderr.decode().strip()

    return stdout
