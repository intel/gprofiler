#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
from pathlib import Path
from subprocess import Popen
from typing import Callable, List, Mapping

import pytest  # type: ignore
from docker import DockerClient
from docker.models.images import Image

from gprofiler.merge import parse_one_collapsed
from tests.utils import run_gprofiler_in_container


@pytest.mark.parametrize(
    "runtime,profiler_type",
    [
        ("java", "ap"),
        ("python", "py-spy"),
        ("python", "pyperf"),
        ("php", "phpspy"),
        ("ruby", "rbspy"),
        ("nodejs", "perf"),
    ],
)
def test_from_executable(
    gprofiler_exe: Path,
    application_pid: int,
    runtime_specific_args: List[str],
    assert_collapsed: Callable[[Mapping[str, int]], None],
    exec_container_image: Image,
    docker_client: DockerClient,
    output_directory: Path,
    runtime: str,
    profiler_type: str,
) -> None:
    _ = application_pid  # Fixture only used for running the application.

    # Execute only the tested profiler
    flags = ["--no-java", "--no-python", "--no-php", "--no-ruby"]
    flags.remove(f"--no-{runtime}")
    flags.append(f"--{runtime}-mode={profiler_type}")

    if exec_container_image is not None:
        gprofiler_inner_dir = Path("/app")
        inner_output_dir = Path("/app/output")
        cwd = Path(os.getenv("GITHUB_WORKSPACE", os.getcwd()))
        volumes = {
            str(output_directory): {"bind": str(inner_output_dir), "mode": "rw"},
            str(cwd): {"bind": str(gprofiler_inner_dir), "mode": "rw"},
        }

        command = (
            [
                str(gprofiler_inner_dir / gprofiler_exe),
                "-v",
                "--output-dir",
                str(inner_output_dir),
            ]
            + runtime_specific_args
            + flags
        )
        run_gprofiler_in_container(docker_client, exec_container_image, command=command, volumes=volumes)
    else:
        os.mkdir(output_directory)
        command = (
            ["sudo", str(gprofiler_exe), "--output-dir", str(output_directory), "-d", "5"]
            + runtime_specific_args
            + flags
        )
        popen = Popen(command)
        popen.wait()

    collapsed = parse_one_collapsed(Path(output_directory / "last_profile.col").read_text())
    assert_collapsed(collapsed)
