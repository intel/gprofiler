#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import time
from pathlib import Path
from subprocess import Popen
from threading import Event
from typing import Callable, List, Mapping, Optional

import psutil
import pytest  # type: ignore
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image
from packaging.version import Version

from gprofiler.merge import parse_one_collapsed
from gprofiler.profilers.java import AsyncProfiledProcess, JavaProfiler
from tests.utils import assert_function_in_collapsed, run_gprofiler_in_container


# adds the "status" command to AsyncProfiledProcess from gProfiler.
class AsyncProfiledProcessForTests(AsyncProfiledProcess):
    def status_async_profiler(self):
        self._run_async_profiler(
            self._get_base_cmd() + [f"status,log={self._log_path_process},file={self._output_path_process}"]
        )


@pytest.fixture
def runtime() -> str:
    return "java"


def test_java_async_profiler_stopped(
    docker_client: DockerClient,
    application_pid: int,
    runtime_specific_args: List[str],
    gprofiler_docker_image: Image,
    output_directory: Path,
    assert_collapsed: Callable[[Mapping[str, int]], None],
    tmp_path: str,
    application_docker_container: Optional[Container],
    application_process: Optional[Popen],
) -> None:
    """
    This test runs gProfiler, targeting a Java application. Then kills gProfiler brutally so profiling doesn't
    stop gracefully and async-profiler remains active.
    Then runs gProfiler again and makes sure we're able to restart async-profiler and get results normally.
    """

    inner_output_directory = "/tmp/gprofiler"
    volumes = {
        str(output_directory): {"bind": inner_output_directory, "mode": "rw"},
    }
    # run Java only (just so initialization is faster w/o others) for 1000 seconds
    args = [
        "-v",
        "-d",
        "1000",
        "-o",
        inner_output_directory,
        "--no-php",
        "--no-python",
        "--no-ruby",
        "--perf-mode=none",
    ] + runtime_specific_args

    container = None
    try:
        container, logs = run_gprofiler_in_container(
            docker_client, gprofiler_docker_image, args, volumes=volumes, auto_remove=False, detach=True
        )
        assert container is not None, "got None container?"

        # and stop after a short while, brutally.
        time.sleep(10)
        container.kill("SIGKILL")
    finally:
        if container is not None:
            print("gProfiler container logs:", container.logs().decode(), sep="\n")
            container.remove(force=True)

    proc = psutil.Process(application_pid)
    assert any("libasyncProfiler.so" in m.path for m in proc.memory_maps())

    # run "status"
    with AsyncProfiledProcessForTests(proc, tmp_path, False, mode="itimer", safemode=0) as ap_proc:
        ap_proc.status_async_profiler()

        # printed the output file, see ACTION_STATUS case in async-profiler/profiler.cpp\
        assert "Profiling is running for " in ap_proc.read_output()

    # then start again, with 1 second
    assert args[2] == "1000"
    args[2] = "1"
    _, logs = run_gprofiler_in_container(docker_client, gprofiler_docker_image, args, volumes=volumes)

    assert "Found async-profiler already started" in logs

    collapsed = parse_one_collapsed(Path(output_directory / "last_profile.col").read_text())
    assert_collapsed(collapsed)


@pytest.mark.parametrize("in_container", [True])
def test_java_async_profiler_cpu_mode(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed,
) -> None:
    """
    Run Java in a container and enable async-profiler in CPU mode, make sure we get kernel stacks.
    """
    with JavaProfiler(
        1000,
        1,
        Event(),
        str(tmp_path),
        False,
        True,
        java_async_profiler_mode="cpu",
        java_async_profiler_safemode=0,
        java_safemode=False,
        java_mode="ap",
    ) as profiler:
        process_collapsed = profiler.snapshot().get(application_pid)
        assert_collapsed(process_collapsed, check_comm=True)
        assert_function_in_collapsed(
            "do_syscall_64_[k]", "java", process_collapsed, True
        )  # ensure kernels stacks exist


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("musl", [True])
def test_java_async_profiler_musl_and_cpu(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed,
) -> None:
    """
    Run Java in an Alpine-based container and enable async-profiler in CPU mode, make sure that musl profiling
    works and that we get kernel stacks.
    """
    with JavaProfiler(
        1000,
        1,
        Event(),
        str(tmp_path),
        False,
        True,
        java_async_profiler_mode="cpu",
        java_async_profiler_safemode=0,
        java_safemode=False,
        java_mode="ap",
    ) as profiler:
        process_collapsed = profiler.snapshot().get(application_pid)
        assert_collapsed(process_collapsed, check_comm=True)
        assert_function_in_collapsed(
            "do_syscall_64_[k]", "java", process_collapsed, True
        )  # ensure kernels stacks exist


def test_java_safemode_parameters(tmp_path) -> None:
    with pytest.raises(AssertionError) as excinfo:
        JavaProfiler(
            1000,
            1,
            Event(),
            str(tmp_path),
            False,
            True,
            java_async_profiler_mode="cpu",
            java_async_profiler_safemode=0,
            java_safemode=True,
            java_mode="ap",
        )
    assert "Async-profiler safemode must be set to 127 in --java-safemode" in str(excinfo.value)

    with pytest.raises(AssertionError) as excinfo:
        JavaProfiler(
            1,
            5,
            Event(),
            str(tmp_path),
            False,
            False,
            java_async_profiler_mode="cpu",
            java_async_profiler_safemode=127,
            java_safemode=True,
            java_mode="ap",
        )
    assert "Java version checks are mandatory in --java-safemode" in str(excinfo.value)


def test_java_safemode_version_check(application_process, tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setitem(JavaProfiler.MINIMAL_SUPPORTED_VERSIONS, 8, (Version("8.999"), 0))

    with JavaProfiler(
        1,
        5,
        Event(),
        str(tmp_path),
        False,
        True,
        java_async_profiler_mode="cpu",
        java_async_profiler_safemode=127,
        java_safemode=True,
        java_mode="ap",
    ) as profiler:
        profiler.snapshot()

    assert len(caplog.records) > 0
    message = caplog.records[0].message
    assert "Unsupported java version 8.275" in message


def test_java_safemode_build_number_check(application_process, tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setitem(JavaProfiler.MINIMAL_SUPPORTED_VERSIONS, 8, (Version("8.275"), 999))

    with JavaProfiler(
        1,
        5,
        Event(),
        str(tmp_path),
        False,
        True,
        java_async_profiler_mode="cpu",
        java_async_profiler_safemode=127,
        java_safemode=True,
        java_mode="ap",
    ) as profiler:
        profiler.snapshot()

    assert len(caplog.records) > 0
    message = caplog.records[0].message
    assert "Unsupported java build number" in message
    assert "for java version 8.275" in message
