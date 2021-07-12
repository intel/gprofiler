#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import concurrent.futures
import functools
import os
from tempfile import NamedTemporaryFile
from threading import Event
from typing import Optional, Tuple

import psutil

from gprofiler.log import get_logger_adapter
from gprofiler.merge import ProcessToStackSampleCounters, merge_global_perfs
from gprofiler.profiler_base import ProfilerBase
from gprofiler.utils import TEMPORARY_STORAGE_PATH, resource_path, run_process

logger = get_logger_adapter(__name__)

PERF_BUILDID_DIR = os.path.join(TEMPORARY_STORAGE_PATH, "perf-buildids")


@functools.lru_cache(maxsize=1)
def perf_path() -> str:
    return resource_path("perf")


class SystemProfiler(ProfilerBase):
    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event: Event,
        storage_dir: str,
        perf_mode: str,
        inject_jit: bool,
        dwarf_stack_size: int,
    ):
        super().__init__(frequency, duration, stop_event, storage_dir)
        self._fp_perf = perf_mode in ("fp", "smart")
        self._dwarf_perf = perf_mode in ("dwarf", "smart")
        self._dwarf_stack_size = dwarf_stack_size
        assert self._fp_perf or not inject_jit
        self._inject_jit = inject_jit

    def _run_perf(self, dwarf: bool = False) -> str:
        buildid_args = ["--buildid-dir", PERF_BUILDID_DIR]

        with NamedTemporaryFile(dir=self._storage_dir) as record_file, NamedTemporaryFile(
            dir=self._storage_dir
        ) as inject_file:
            inject = not dwarf and self._inject_jit

            args = ["-F", str(self._frequency), "-a", "-g", "-o", record_file.name]
            if inject:
                args += ["-k", "1"]

            if dwarf:
                args += ["--call-graph", f"dwarf,{self._dwarf_stack_size}"]
            run_process(
                [perf_path()] + buildid_args + ["record"] + args + ["--", "sleep", str(self._duration)],
                stop_event=self._stop_event,
            )

            if inject:
                run_process(
                    [perf_path()] + buildid_args + ["inject", "--jit", "-o", inject_file.name, "-i", record_file.name],
                )
                script_input = inject_file.name
            else:
                script_input = record_file.name

            perf_script_result = run_process(
                [perf_path()] + buildid_args + ["script", "-F", "+pid", "-i", script_input],
                suppress_log=True,
            )

            return perf_script_result.stdout.decode('utf8')

    def snapshot(self) -> ProcessToStackSampleCounters:
        free_disk = psutil.disk_usage(self._storage_dir).free
        if free_disk < 4 * 1024 * 1024:
            raise Exception(f"Free disk space: {free_disk}kb. Skipping perf!")

        logger.info("Running global perf...")
        perf_result = self._get_global_perf_result()
        logger.info("Finished running global perf")
        return perf_result

    def _get_global_perf_result(self) -> ProcessToStackSampleCounters:
        fp_perf: Optional[str] = None
        dwarf_perf: Optional[str] = None
        if not self._fp_perf:
            dwarf_perf = self._run_perf(dwarf=True)
        elif not self._dwarf_perf:
            fp_perf = self._run_perf(dwarf=False)
        else:
            dwarf_perf, fp_perf = self._run_fp_and_dwarf_concurrent_perfs()
        return merge_global_perfs(fp_perf, dwarf_perf)

    def _run_fp_and_dwarf_concurrent_perfs(self) -> Tuple[str, str]:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            # We are running 2 perfs in parallel - one with DWARF and one with FP, and then we merge their results.
            # This improves the results from software that is compiled without frame pointers,
            # like some native software. DWARF by itself is not good enough, as it has issues with unwinding some
            # versions of Go processes.
            fp_future = executor.submit(self._run_perf, False)
            dwarf_future = executor.submit(self._run_perf, True)
        return dwarf_future.result(), fp_future.result()
