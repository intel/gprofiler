#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import pytest  # type: ignore
from glob import glob
from pathlib import Path
from threading import Event
from typing import Optional, Callable, Mapping

from docker import DockerClient
from docker.models.images import Image

from gprofiler.merge import parse_one_collapsed
from gprofiler.java import JavaProfiler
from gprofiler.python import PySpyProfiler, PythonEbpfProfiler
from tests.util import run_privileged_container


@pytest.mark.parametrize("runtime", ["java"])
def test_java_from_host(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: Callable[[Optional[Mapping[str, int]]], None],
) -> None:
    profiler = JavaProfiler(1000, 1, True, Event(), str(tmp_path))
    process_collapsed = profiler.snapshot()
    assert_collapsed(process_collapsed.get(application_pid))


@pytest.mark.parametrize("runtime", ["python"])
def test_pyspy(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: Callable[[Optional[Mapping[str, int]]], None],
) -> None:
    profiler = PySpyProfiler(1000, 1, Event(), str(tmp_path))
    process_collapsed = profiler.snapshot()
    assert_collapsed(process_collapsed.get(application_pid))


@pytest.mark.parametrize('runtime', ['python'])
def test_python_ebpf(
    tmp_path,
    application_pid,
    assert_collapsed,
):
    with PythonEbpfProfiler(1000, 1, Event(), str(tmp_path)) as profiler:
        process_collapsed = profiler.snapshot()
        assert_collapsed(process_collapsed.get(application_pid))


@pytest.mark.parametrize("runtime", ["java", "python"])
def test_from_container(
    docker_client: DockerClient,
    application_pid: int,
    gprofiler_docker_image: Image,
    output_directory: Path,
    assert_collapsed: Callable[[Mapping[str, int]], None],
) -> None:
    _ = application_pid  # Fixture only used for running the application.
    inner_output_directory = "/tmp/gpofiler"
    volumes = {
        "/usr/src": {"bind": "/usr/src", "mode": "ro"},
        "/lib/modules": {"bind": "/lib/modules", "mode": "ro"},
        str(output_directory): {"bind": inner_output_directory, "mode": "rw"}
    }
    run_privileged_container(
        docker_client, gprofiler_docker_image, ["-d", "1", "-o", inner_output_directory], volumes=volumes
    )
    output = glob(str(output_directory / "*.col"))
    assert len(output) == 1
    collapsed_path = output[0]
    collapsed = parse_one_collapsed(Path(collapsed_path).read_text())
    assert_collapsed(collapsed)
