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
import atexit
import datetime
import glob
import importlib.resources
import logging
import os
import random
import re
import shutil
import signal
import socket
import string
import subprocess
import sys
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from subprocess import CompletedProcess, Popen, TimeoutExpired
from tempfile import TemporaryDirectory
from threading import Event
from types import FrameType
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union

import psutil
from granulate_utils.exceptions import CouldNotAcquireMutex
from granulate_utils.linux.mutex import try_acquire_mutex
from granulate_utils.linux.ns import is_root, run_in_ns_wrapper
from granulate_utils.linux.process import is_kernel_thread, process_exe
from psutil import Process

from gprofiler.consts import CPU_PROFILING_MODE
from gprofiler.platform import is_linux, is_windows

if is_windows():
    import pythoncom
    import wmi

from gprofiler.exceptions import (
    CalledProcessError,
    CalledProcessTimeoutError,
    ProcessStoppedException,
    ProgramMissingException,
    StopEventSetException,
)
from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)

GPROFILER_DIRECTORY_NAME = "gprofiler_tmp"
TEMPORARY_STORAGE_PATH = (
    f"/tmp/{GPROFILER_DIRECTORY_NAME}"
    if is_linux()
    else os.getenv("USERPROFILE", default=os.getcwd()) + f"\\AppData\\Local\\Temp\\{GPROFILER_DIRECTORY_NAME}"
)

gprofiler_mutex: Optional[socket.socket] = None

# 1 KeyboardInterrupt raised per this many seconds, no matter how many SIGINTs we get.
SIGINT_RATELIMIT = 0.5

_last_signal_ts: Optional[float] = None
_processes: List[Popen] = []


@lru_cache(maxsize=None)
def resource_path(relative_path: str = "") -> str:
    *relative_directory, basename = relative_path.split("/")
    package = ".".join(["gprofiler", "resources"] + relative_directory)
    try:
        with importlib.resources.path(package, basename) as path:
            return str(path)
    except ImportError as e:
        raise Exception(f"Resource {relative_path!r} not found!") from e


def start_process(
    cmd: Union[str, List[str]],
    via_staticx: bool = False,
    tmpdir: Optional[Path] = None,
    **kwargs: Any,
) -> Popen:
    if isinstance(cmd, str):
        cmd = [cmd]

    if kwargs.pop("pdeathsigger", True) and is_linux():
        cmd = [resource_path("pdeathsigger")] + cmd if is_linux() else cmd

    logger.debug("Running command", command=cmd)

    env = kwargs.pop("env", None)
    staticx_dir = get_staticx_dir()
    # are we running under staticx?
    if staticx_dir is not None:
        # if so, if "via_staticx" was requested, then run the binary with the staticx ld.so
        # because it's supposed to be run with it.
        if via_staticx:
            # staticx_dir (from STATICX_BUNDLE_DIR) is where staticx has extracted all of the
            # libraries it had collected earlier.
            # see https://github.com/JonathonReinhart/staticx#run-time-information
            cmd = [f"{staticx_dir}/.staticx.interp", "--library-path", staticx_dir] + cmd
        else:
            env = env if env is not None else os.environ.copy()
            if tmpdir is not None:
                tmpdir.mkdir(exist_ok=True)
                env["TMPDIR"] = tmpdir.as_posix()
            elif "TMPDIR" not in env and "TMPDIR" in os.environ:
                # ensure `TMPDIR` env is propagated to the child processes (used by staticx)
                env["TMPDIR"] = os.environ["TMPDIR"]

            # explicitly remove our directory from LD_LIBRARY_PATH
            env["LD_LIBRARY_PATH"] = ""

    process = Popen(
        cmd,
        stdout=kwargs.pop("stdout", subprocess.PIPE),
        stderr=kwargs.pop("stderr", subprocess.PIPE),
        stdin=subprocess.PIPE,
        start_new_session=is_linux(),  # TODO: change to "process_group" after upgrade to Python 3.11+
        env=env,
        **kwargs,
    )
    _processes.append(process)
    return process


def wait_event(timeout: float, stop_event: Event, condition: Callable[[], bool], interval: float = 0.1) -> None:
    end_time = time.monotonic() + timeout
    while True:
        if condition():
            break

        if stop_event.wait(interval):
            raise StopEventSetException()

        if time.monotonic() > end_time:
            raise TimeoutError()


def poll_process(process: Popen, timeout: float, stop_event: Event) -> None:
    try:
        wait_event(timeout, stop_event, lambda: process.poll() is not None)
    except StopEventSetException:
        process.kill()
        raise


def remove_files_by_prefix(prefix: str) -> None:
    for f in glob.glob(f"{prefix}*"):
        os.unlink(f)


def wait_for_file_by_prefix(prefix: str, timeout: float, stop_event: Event) -> Path:
    glob_pattern = f"{prefix}*"
    wait_event(timeout, stop_event, lambda: len(glob.glob(glob_pattern)) > 0)

    output_files = glob.glob(glob_pattern)
    # All the snapshot samples should be in one file
    if len(output_files) != 1:
        # this can happen if:
        # * the profiler generating those files is erroneous
        # * the profiler received many signals (and it generated files based on signals)
        # * errors in gProfiler led to previous output fails remain not removed
        # in any case, we remove all old files, and assume the last one (after sorting by timestamp)
        # is the one we want.
        logger.warning(
            f"One output file expected, but found {len(output_files)}."
            f" Removing all and using the last one. {output_files}"
        )
        # timestamp format guarantees alphabetical order == chronological order.
        output_files.sort()
        for f in output_files[:-1]:
            os.unlink(f)
        output_files = output_files[-1:]

    return Path(output_files[0])


def reap_process(process: Popen) -> Tuple[int, bytes, bytes]:
    """
    Safely reap a process. This function expects the process to be exited or exiting.
    It uses communicate() instead of wait() to avoid the possible deadlock in wait()
    (see https://docs.python.org/3/library/subprocess.html#subprocess.Popen.wait, and see
    ticket https://github.com/intel/gprofiler/issues/744).
    """
    stdout, stderr = process.communicate()
    returncode = process.poll()
    assert returncode is not None  # only None if child has not terminated
    return returncode, stdout, stderr


def _kill_and_reap_process(process: Popen, kill_signal: signal.Signals) -> Tuple[int, bytes, bytes]:
    process.send_signal(kill_signal)
    logger.debug(
        f"({process.args!r}) was killed by us with signal {kill_signal} due to timeout or stop request, reaping it"
    )
    return reap_process(process)


def run_process(
    cmd: Union[str, List[str]],
    *,
    stop_event: Event = None,
    suppress_log: bool = False,
    via_staticx: bool = False,
    check: bool = True,
    timeout: int = None,
    kill_signal: signal.Signals = signal.SIGTERM if is_windows() else signal.SIGKILL,
    stdin: bytes = None,
    **kwargs: Any,
) -> "CompletedProcess[bytes]":
    stdout: bytes
    stderr: bytes

    reraise_exc: Optional[BaseException] = None
    with start_process(cmd, via_staticx, **kwargs) as process:
        assert isinstance(process.args, str) or (
            isinstance(process.args, list) and all(isinstance(s, str) for s in process.args)
        ), process.args  # mypy

        try:
            if stdin is not None:
                assert process.stdin is not None
                process.stdin.write(stdin)
            if stop_event is None:
                assert timeout is None, f"expected no timeout, got {timeout!r}"
                # wait for stderr & stdout to be closed
                stdout, stderr = process.communicate()
            else:
                end_time = (time.monotonic() + timeout) if timeout is not None else None
                while True:
                    try:
                        stdout, stderr = process.communicate(timeout=1)
                        break
                    except TimeoutExpired:
                        if stop_event.is_set():
                            raise ProcessStoppedException from None
                        if end_time is not None and time.monotonic() > end_time:
                            assert timeout is not None
                            raise
        except TimeoutExpired:
            returncode, stdout, stderr = _kill_and_reap_process(process, kill_signal)
            assert timeout is not None
            reraise_exc = CalledProcessTimeoutError(
                timeout, returncode, cmd, stdout.decode("latin-1"), stderr.decode("latin-1")
            )
        except BaseException as e:  # noqa
            returncode, stdout, stderr = _kill_and_reap_process(process, kill_signal)
            reraise_exc = e
        retcode = process.poll()
        assert retcode is not None  # only None if child has not terminated

    result: CompletedProcess[bytes] = CompletedProcess(process.args, retcode, stdout, stderr)

    # decoding stdout/stderr as latin-1 which should never raise UnicodeDecodeError.
    extra: Dict[str, Any] = {"exit_code": result.returncode}
    if not suppress_log:
        if result.stdout:
            extra["stdout"] = result.stdout.decode("latin-1")
        if result.stderr:
            extra["stderr"] = result.stderr.decode("latin-1")
    logger.debug("Command exited", command=process.args, **extra)
    if reraise_exc is not None:
        raise reraise_exc
    elif check and retcode != 0:
        raise CalledProcessError(
            retcode, process.args, output=stdout.decode("latin-1"), stderr=stderr.decode("latin-1")
        )
    return result


if is_windows():

    def pgrep_exe(match: str) -> List[Process]:
        """psutil doesn't return all running python processes on Windows"""
        pythoncom.CoInitialize()
        w = wmi.WMI()
        return [
            Process(pid=p.ProcessId)
            for p in w.Win32_Process()
            if match in p.Name.lower() and p.ProcessId != os.getpid()
        ]

else:

    def pgrep_exe(match: str) -> List[Process]:
        pattern = re.compile(match)
        procs = []
        for process in psutil.process_iter():
            try:
                if not is_kernel_thread(process) and pattern.match(process_exe(process)):
                    procs.append(process)
            except psutil.NoSuchProcess:  # process might have died meanwhile
                continue
        return procs


def pgrep_maps(match: str) -> List[Process]:
    # this is much faster than iterating over processes' maps with psutil.
    # We use flag -E in grep to support systems where grep is not PCRE
    result = run_process(
        f"grep -lE '{match}' /proc/*/maps",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        suppress_log=True,
        check=False,
        pdeathsigger=False,
    )
    # 0 - found
    # 1 - not found
    # 2 - error (which we might get for a missing /proc/pid/maps file of a process which just exited)
    # so this ensures grep wasn't killed by a signal
    assert result.returncode in (
        0,
        1,
        2,
    ), f"unexpected 'grep' exit code: {result.returncode}, stdout {result.stdout!r} stderr {result.stderr!r}"

    error_lines = []
    for line in result.stderr.splitlines():
        if not (
            line.startswith(b"grep: /proc/")
            and (
                line.endswith(b"/maps: No such file or directory")
                or line.endswith(b"/maps: No such process")
                or (not is_root() and b"/maps: Permission denied" in line)
            )
        ):
            error_lines.append(line)
    if error_lines:
        logger.error(f"Unexpected 'grep' error output (first 10 lines): {error_lines[:10]}")

    processes: List[Process] = []
    for line in result.stdout.splitlines():
        assert line.startswith(b"/proc/") and line.endswith(b"/maps"), f"unexpected 'grep' line: {line!r}"
        pid = int(line[len(b"/proc/") : -len(b"/maps")])
        try:
            processes.append(Process(pid))
        except psutil.NoSuchProcess:
            continue  # process might have died meanwhile

    return processes


def get_iso8601_format_time_from_epoch_time(time: float) -> str:
    return get_iso8601_format_time(datetime.datetime.utcfromtimestamp(time))


def get_iso8601_format_time(time: datetime.datetime) -> str:
    return time.replace(microsecond=0).isoformat()


def remove_prefix(s: str, prefix: str) -> str:
    # like str.removeprefix of Python 3.9, but this also ensures the prefix exists.
    assert s.startswith(prefix), f"{s} doesn't start with {prefix}"
    return s[len(prefix) :]


def touch_path(path: str, mode: int) -> None:
    Path(path).touch()
    # chmod() afterwards (can't use 'mode' in touch(), because it's affected by umask)
    os.chmod(path, mode)


def remove_path(path: Union[str, Path], missing_ok: bool = False) -> None:
    Path(path).unlink(missing_ok=missing_ok)


@contextmanager
def removed_path(path: str) -> Iterator[None]:
    try:
        yield
    finally:
        remove_path(path, missing_ok=True)


_INSTALLED_PROGRAMS_CACHE: List[str] = []


def assert_program_installed(program: str) -> None:
    if program in _INSTALLED_PROGRAMS_CACHE:
        return

    if shutil.which(program) is not None:
        _INSTALLED_PROGRAMS_CACHE.append(program)
    else:
        raise ProgramMissingException(program)


def grab_gprofiler_mutex() -> bool:
    """
    Implements a basic, system-wide mutex for gProfiler, to make sure we don't run 2 instances simultaneously.
    The mutex is implemented by a Unix domain socket bound to an address in the abstract namespace of the init
    network namespace. This provides automatic cleanup when the process goes down, and does not make any assumption
    on filesystem structure (as happens with file-based locks).
    In order to see who's holding the lock now, you can run "sudo netstat -xp | grep gprofiler".
    """
    GPROFILER_LOCK = "\x00gprofiler_lock"

    try:
        run_in_ns_wrapper(["net"], lambda: try_acquire_mutex(GPROFILER_LOCK))
    except CouldNotAcquireMutex:
        print(
            "Could not acquire gProfiler's lock. Is it already running?"
            " Try 'sudo netstat -xp | grep gprofiler' to see which process holds the lock.",
            file=sys.stderr,
        )
        return False
    else:
        # success
        return True


def atomically_symlink(target: str, link_node: str) -> None:
    """
    Create a symlink file at 'link_node' pointing to 'target'.
    If a file already exists at 'link_node', it is replaced atomically.
    Would be obsoloted by https://bugs.python.org/issue36656, which covers this as well.
    """
    tmp_path = link_node + ".tmp"
    os.symlink(target, tmp_path)
    os.rename(tmp_path, link_node)


class TemporaryDirectoryWithMode(TemporaryDirectory):
    def __init__(self, *args: Any, mode: int = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        if mode is not None:
            os.chmod(self.name, mode)


def reset_umask() -> None:
    """
    Resets our umask back to a sane value.
    """
    os.umask(0o022)


def limit_frequency(
    limit: Optional[int],
    requested: int,
    msg_header: str,
    runtime_logger: logging.LoggerAdapter,
    profiling_mode: str,
) -> int:
    if profiling_mode != CPU_PROFILING_MODE:
        return requested

    if limit is not None and requested > limit:
        runtime_logger.warning(
            f"{msg_header}: Requested frequency ({requested}) is higher than the limit {limit}, "
            f"limiting the frequency to the limit ({limit})"
        )
        return limit

    return requested


def random_prefix() -> str:
    return "".join(random.choice(string.ascii_letters) for _ in range(16))


PERF_EVENT_MLOCK_KB = "/proc/sys/kernel/perf_event_mlock_kb"


def read_perf_event_mlock_kb() -> int:
    return int(Path(PERF_EVENT_MLOCK_KB).read_text())


def write_perf_event_mlock_kb(value: int) -> None:
    Path(PERF_EVENT_MLOCK_KB).write_text(str(value))


def is_pyinstaller() -> bool:
    """
    Are we running in PyInstaller?
    """
    # https://pyinstaller.readthedocs.io/en/stable/runtime-information.html#run-time-information
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_staticx_dir() -> Optional[str]:
    return os.getenv("STATICX_BUNDLE_DIR")


def add_permission_dir(path: str, permission_for_file: int, permission_for_dir: int) -> None:
    os.chmod(path, os.stat(path).st_mode | permission_for_dir)
    for subpath in os.listdir(path):
        absolute_subpath = os.path.join(path, subpath)
        if os.path.isdir(absolute_subpath):
            add_permission_dir(absolute_subpath, permission_for_file, permission_for_dir)
        else:
            os.chmod(absolute_subpath, os.stat(absolute_subpath).st_mode | permission_for_file)


def merge_dicts(source: Dict[str, Any], dest: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in source.items():
        # in case value is a dict itself
        if isinstance(value, dict):
            node = dest.setdefault(key, {})
            merge_dicts(value, node)
        else:
            dest[key] = value
    return dest


def is_profiler_disabled(profile_mode: str) -> bool:
    return profile_mode in ("none", "disabled")


def _exit_handler() -> None:
    for process in _processes:
        process.kill()


def _sigint_handler(sig: int, frame: Optional[FrameType]) -> None:
    global _last_signal_ts
    ts = time.monotonic()
    # no need for atomicity here: we can't get another SIGINT before this one returns.
    # https://www.gnu.org/software/libc/manual/html_node/Signals-in-Handler.html#Signals-in-Handler
    if _last_signal_ts is None or ts > _last_signal_ts + SIGINT_RATELIMIT:
        _last_signal_ts = ts
        raise KeyboardInterrupt


def setup_signals() -> None:
    atexit.register(_exit_handler)
    # When we run under staticx & PyInstaller, both of them forward (some of the) signals to gProfiler.
    # We catch SIGINTs and ratelimit them, to avoid being interrupted again during the handling of the
    # first INT.
    # See my commit message for more information.
    signal.signal(signal.SIGINT, _sigint_handler)
    # handle SIGTERM in the same manner - gracefully stop gProfiler.
    # SIGTERM is also forwarded by staticx & PyInstaller, so we need to ratelimit it.
    signal.signal(signal.SIGTERM, _sigint_handler)
