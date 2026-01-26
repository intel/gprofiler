import os
import signal
import time
from pathlib import Path
from subprocess import PIPE, Popen
from threading import Event
from typing import Iterator, List, Optional

from psutil import Process

from gprofiler.log import get_logger_adapter
from gprofiler.utils import (
    cleanup_process_reference,
    reap_process,
    remove_files_by_prefix,
    remove_path,
    resource_path,
    run_process,
    start_process,
    wait_event,
    wait_for_file_by_prefix,
)

logger = get_logger_adapter(__name__)


def perf_path() -> str:
    return resource_path("perf")


# TODO: automatically disable this profiler if can_i_use_perf_events() returns False?
class PerfProcess:
    _DUMP_TIMEOUT_S = 5  # timeout for waiting perf to write outputs after signaling (or right after starting)
    _RESTART_AFTER_S = 3600
    _PERF_MEMORY_USAGE_THRESHOLD = 512 * 1024 * 1024
    # default number of pages used by "perf record" when perf_event_mlock_kb=516
    # we use double for dwarf.
    _MMAP_SIZES = {"fp": 129, "dwarf": 257}

    def __init__(
        self,
        *,
        frequency: int,
        stop_event: Event,
        output_path: str,
        is_dwarf: bool,
        inject_jit: bool,
        extra_args: List[str],
        processes_to_profile: Optional[List[Process]],
        switch_timeout_s: int,
        use_cgroups: bool = False,
        max_cgroups: int = 50,
        max_docker_containers: int = 0,
    ):
        self._start_time = 0.0
        self._frequency = frequency
        self._stop_event = stop_event
        self._output_path = output_path
        self._type = "dwarf" if is_dwarf else "fp"
        self._inject_jit = inject_jit
        self._use_cgroups = use_cgroups
        self._max_cgroups = max_cgroups
        self._pid_args = []
        self._cgroup_args = []

        # Determine profiling strategy
        if use_cgroups:
            from gprofiler.utils.cgroup_utils import (
                get_top_cgroup_names_for_perf,
                is_cgroup_available,
                validate_perf_cgroup_support,
            )

            # Use cgroup-based profiling for better reliability
            if is_cgroup_available() and validate_perf_cgroup_support():
                try:
                    top_cgroups = get_top_cgroup_names_for_perf(max_cgroups, max_docker_containers)
                    if top_cgroups:
                        # Cgroup monitoring requires system-wide mode (-a)
                        self._pid_args.append("-a")
                        self._cgroup_args.extend(["-G", ",".join(top_cgroups)])
                        logger.info(
                            f"Using cgroup-based profiling with {len(top_cgroups)} top cgroups: "
                            f"{top_cgroups[:3]}{'...' if len(top_cgroups) > 3 else ''}"
                        )
                    else:
                        # Never fall back to system-wide profiling when cgroups are explicitly requested
                        from gprofiler.exceptions import PerfNoSupportedEvent

                        if max_docker_containers > 0:
                            logger.error(
                                f"No Docker containers found for profiling despite "
                                f"--perf-max-docker-containers={max_docker_containers}. "
                                "This could indicate cgroup v2 compatibility issues or no running containers. "
                                "Perf profiler will be disabled to prevent system-wide profiling."
                            )
                            raise PerfNoSupportedEvent(
                                "Docker container profiling requested but no containers available"
                            )
                        elif max_cgroups > 0:
                            logger.error(
                                f"No cgroups found for profiling despite --perf-max-cgroups={max_cgroups}. "
                                "This could indicate cgroup compatibility issues or no active cgroups. "
                                "Perf profiler will be disabled to prevent system-wide profiling."
                            )
                            raise PerfNoSupportedEvent("Cgroup profiling requested but no cgroups available")
                        else:
                            logger.error(
                                "Cgroup profiling was requested (--perf-use-cgroups) but no specific limits were set. "
                                "Perf profiler will be disabled to prevent system-wide profiling."
                            )
                            raise PerfNoSupportedEvent(
                                "Cgroup profiling requested but no containers or cgroups specified"
                            )
                except Exception as e:
                    # Never fall back to system-wide profiling when cgroups are explicitly requested
                    from gprofiler.exceptions import PerfNoSupportedEvent

                    logger.error(
                        f"Failed to get cgroups for profiling: {e}. "
                        "Perf profiler will be disabled to prevent system-wide profiling."
                    )
                    raise PerfNoSupportedEvent(f"Cgroup profiling failed: {e}")
        elif processes_to_profile is not None:
            # Traditional PID-based profiling
            self._pid_args.append("--pid")
            self._pid_args.append(",".join([str(process.pid) for process in processes_to_profile]))
        else:
            # System-wide profiling
            self._pid_args.append("-a")

        self._extra_args = extra_args
        self._switch_timeout_s = switch_timeout_s
        self._process: Optional[Popen] = None

    @property
    def _log_name(self) -> str:
        return f"perf ({self._type} mode)"

    def _get_perf_cmd(self) -> List[str]:
        # When using cgroups, perf requires events to be specified before cgroups.
        # If no explicit events are provided but cgroups are used, add default event.
        # For multiple cgroups, perf requires one event per cgroup.
        extra_args = self._extra_args
        
        # Separate extra_args into perf options and application command
        # The "--" separator marks the boundary between perf args and the app command
        perf_extra_args = []
        app_command = []
        separator_found = False
        
        for arg in extra_args:
            if arg == "--":
                separator_found = True
                app_command.append(arg)
            elif separator_found:
                app_command.append(arg)
            else:
                perf_extra_args.append(arg)
        
        if self._cgroup_args and not perf_extra_args:
            # Count the number of cgroups (they are comma-separated in -G argument)
            cgroup_arg = None
            for i, arg in enumerate(self._cgroup_args):
                if arg == "-G" and i + 1 < len(self._cgroup_args):
                    cgroup_arg = self._cgroup_args[i + 1]
                    break

            if cgroup_arg:
                num_cgroups = len(cgroup_arg.split(","))
                # Add one event per cgroup (perf requirement)
                perf_extra_args = []
                for _ in range(num_cgroups):
                    perf_extra_args.extend(["-e", "cycles"])
            else:
                # Fallback: single event
                perf_extra_args = ["-e", "cycles"]

        return (
            [
                perf_path(),
                "record",
                "-F",
                str(self._frequency),
                "-g",
                "-o",
                self._output_path,
                f"--switch-output={self._switch_timeout_s}s,signal",
                "--switch-max-files=1",
                # explicitly pass '-m', otherwise perf defaults to deriving this number from perf_event_mlock_kb,
                # and it ends up using it entirely (and we want to spare some for async-profiler)
                # this number scales linearly with the number of active cores (so we don't need to do this calculation
                # here)
                "-m",
                str(self._MMAP_SIZES[self._type]),
            ]
            + perf_extra_args  # Events must come before cgroups
            + self._pid_args
            + self._cgroup_args
            + (["-k", "1"] if self._inject_jit else [])
            + app_command  # Application command (with "--") must be last
        )

    def start(self) -> None:
        logger.info(f"Starting {self._log_name}")
        # remove old files, should they exist from previous runs
        remove_path(self._output_path, missing_ok=True)
        process = start_process(self._get_perf_cmd())
        try:
            wait_event(self._DUMP_TIMEOUT_S, self._stop_event, lambda: os.path.exists(self._output_path))
            self.start_time = time.monotonic()
        except TimeoutError:
            process.kill()
            cleanup_process_reference(process=process)
            assert process.stdout is not None and process.stderr is not None
            logger.critical(
                f"{self._log_name} failed to start", stdout=process.stdout.read(), stderr=process.stderr.read()
            )
            raise
        else:
            self._process = process
            os.set_blocking(self._process.stdout.fileno(), False)  # type: ignore
            os.set_blocking(self._process.stderr.fileno(), False)  # type: ignore
            logger.info(f"Started {self._log_name}")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()  # okay to call even if process is already dead
            exit_code, stdout, stderr = reap_process(self._process)
            cleanup_process_reference(process=self._process)
            self._process = None
            logger.info(f"Stopped {self._log_name}", exit_code=exit_code, stderr=stderr, stdout=stdout)

    def is_running(self) -> bool:
        """
        Is perf running? returns False if perf is stopped OR if process exited since last check
        """
        return self._process is not None and self._process.poll() is None

    def restart(self) -> None:
        self.stop()
        self.start()

    def restart_if_not_running(self) -> None:
        """
        Restarts perf if it was stopped for whatever reason.
        """
        if not self.is_running():
            logger.warning(f"{self._log_name} not running (unexpectedly), restarting...")
            self.restart()

    def restart_if_rss_exceeded(self) -> None:
        """Checks if perf used memory exceeds threshold, and if it does, restarts perf"""
        assert self._process is not None
        perf_rss = Process(self._process.pid).memory_info().rss
        if (
            time.monotonic() - self._start_time >= self._RESTART_AFTER_S
            and perf_rss >= self._PERF_MEMORY_USAGE_THRESHOLD
        ):
            logger.debug(
                f"Restarting {self._log_name} due to memory exceeding limit",
                limit_rss=self._PERF_MEMORY_USAGE_THRESHOLD,
                perf_rss=perf_rss,
            )
            self.restart()

    def switch_output(self) -> None:
        assert self._process is not None, "profiling not started!"
        # clean stale files (can be emitted by perf timing out and switching output file).
        # we clean them here before sending the signal, to be able to tell between the file generated by the signal
        # to files generated by timeouts.
        remove_files_by_prefix(f"{self._output_path}.")
        self._process.send_signal(signal.SIGUSR2)

    def wait_and_script(self) -> Iterator[str]:
        """
        Stream perf script output line by line to avoid loading entire output into memory.
        Yields each sample line as it's processed.
        """
        perf_data = None
        inject_data = None
        perf_script_proc = None

        try:
            perf_data = wait_for_file_by_prefix(f"{self._output_path}.", self._DUMP_TIMEOUT_S, self._stop_event)
        except Exception:
            # Check if process died first
            process_died = self._process is not None and self._process.poll() is not None

            assert self._process is not None and self._process.stdout is not None and self._process.stderr is not None
            logger.critical(
                f"{self._log_name} failed to dump output",
                perf_stdout=self._process.stdout.read(),
                perf_stderr=self._process.stderr.read(),
                perf_running=self.is_running(),
            )

            # Clean up after logging
            if process_died:
                cleanup_process_reference(process=self._process)
                self._process = None
            raise
        finally:
            # always read its stderr
            # using read1() which performs just a single read() call and doesn't read until EOF
            # (unlike Popen.communicate())
            if self._process is not None and self._process.stderr is not None:
                logger.debug(f"{self._log_name} run output", perf_stderr=self._process.stderr.read1())  # type: ignore

        try:
            inject_data = Path(f"{str(perf_data)}.inject")
            if self._inject_jit:
                run_process(
                    [perf_path(), "inject", "--jit", "-o", str(inject_data), "-i", str(perf_data)],
                )
                perf_data.unlink()
                perf_data = inject_data

            perf_script_cmd = [perf_path(), "script", "-F", "+pid", "-i", str(perf_data)]

            # Use Popen directly for streaming instead of run_process
            perf_script_proc = Popen(
                perf_script_cmd, stdout=PIPE, stderr=PIPE, text=True, encoding="utf8", errors="replace"
            )

            # Stream output line by line
            if perf_script_proc.stdout is not None:
                for line in perf_script_proc.stdout:
                    yield line.rstrip("\n")

            # Wait for process to complete and check return code
            perf_script_proc.wait()
            if perf_script_proc.returncode != 0:
                stderr_output = perf_script_proc.stderr.read() if perf_script_proc.stderr is not None else ""
                logger.critical(
                    f"{self._log_name} failed to run perf script",
                    command=" ".join(perf_script_cmd),
                    stderr=stderr_output,
                )

            # Explicit return after successful streaming
            return

        except Exception as e:
            logger.critical(
                f"{self._log_name} failed to run perf script: {str(e)}",
                command=" ".join(perf_script_cmd),
            )
            raise
        finally:
            # Cleanup resources
            if perf_script_proc is not None:
                try:
                    perf_script_proc.terminate()
                    perf_script_proc.wait(timeout=5)
                except (OSError, TimeoutError):
                    pass

            if perf_data is not None:
                remove_path(perf_data, missing_ok=True)
            if self._inject_jit and inject_data is not None:
                remove_path(inject_data, missing_ok=True)
