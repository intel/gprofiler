#
# Copyright (C) 2026 Intel Corporation
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

"""
Tests for the logic from gprofiler/system_metrics.py
"""

import errno
import logging
import math
import time
from pathlib import Path
from threading import Event, Thread
from typing import List, Optional

import pytest
from pytest import LogCaptureFixture, MonkeyPatch

from gprofiler.system_metrics import (
    MAX_EXT_METRICS,
    PSI_RESOURCES,
    NoopSystemMetricsMonitor,
    PressureStallMonitor,
    SystemMetricsMonitor,
    sanitize_ext_metrics,
)

SECOND_NS = 1_000_000_000
CYCLE_NS = 60 * SECOND_NS


def test_cpu_is_primed_on_the_profiling_thread() -> None:
    # Heartbeat mode builds the monitor on one thread and takes snapshots on another.
    # psutil keeps cpu_percent() state per thread, so without priming in start()
    # the first snapshot reports 0.0.
    stop_event = Event()
    monitor = SystemMetricsMonitor(stop_event)  # main thread
    cpu: List[Optional[float]] = []

    def profiling_thread() -> None:
        monitor.start()
        try:
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:  # burn CPU so the system-wide value is > 0
                pass
            cpu.append(monitor.get_metrics().cpu_avg)
        finally:
            stop_event.set()
            monitor.stop()

    thread = Thread(target=profiling_thread)
    thread.start()
    thread.join()
    assert len(cpu) == 1, "profiling thread failed before reading metrics"
    assert cpu[0] is not None and cpu[0] > 0


class FakeClock:
    def __init__(self) -> None:
        self.ns = 0

    def __call__(self) -> int:
        return self.ns


def write_psi(root: Path, resource: str, some_total_us: int, with_full_line: bool = True) -> None:
    lines = [f"some avg10=0.00 avg60=0.00 avg300=0.00 total={some_total_us}"]
    if with_full_line:
        lines.append("full avg10=0.00 avg60=0.00 avg300=0.00 total=0")
    (root / resource).write_text("\n".join(lines) + "\n")


def write_all_psi(root: Path, some_total_us: int) -> None:
    for resource in PSI_RESOURCES:
        write_psi(root, resource, some_total_us)


def psi_logs(caplog: LogCaptureFixture) -> List[str]:
    return [r.getMessage() for r in caplog.records if r.name == "gprofiler.system_metrics" and "PSI" in r.getMessage()]


def test_psi_reports_percent_of_wall_time_stalled(tmp_path: Path) -> None:
    clock = FakeClock()
    psi = PressureStallMonitor(root=tmp_path, clock=clock)
    write_all_psi(tmp_path, 1_000_000)
    assert psi.collect() == {}, "the first reading has no previous value to diff against"

    clock.ns += CYCLE_NS
    write_psi(tmp_path, "cpu", 1_000_000 + 6_000_000)  # 6 s of 60 s
    write_psi(tmp_path, "memory", 1_000_000 + 1_234_567)  # 2.0576...%, rounded to 2 decimal places
    write_psi(tmp_path, "io", 1_000_000)  # no stall
    assert psi.collect() == {"psi.cpu.some": 10.0, "psi.memory.some": 2.06, "psi.io.some": 0.0}


def test_psi_cpu_file_without_full_line(tmp_path: Path) -> None:
    # Kernels before 5.13 have no "full" line in /proc/pressure/cpu
    clock = FakeClock()
    psi = PressureStallMonitor(root=tmp_path, clock=clock)
    write_all_psi(tmp_path, 0)
    write_psi(tmp_path, "cpu", 0, with_full_line=False)
    psi.collect()

    clock.ns += CYCLE_NS
    write_psi(tmp_path, "cpu", 30_000_000, with_full_line=False)
    assert psi.collect()["psi.cpu.some"] == 50.0


def test_psi_missing_root_logs_once(tmp_path: Path, caplog: LogCaptureFixture) -> None:
    # Kernels 5.6+ booted with psi=0 (and RHEL 8) have no /proc/pressure at all
    caplog.set_level(logging.INFO)
    clock = FakeClock()
    psi = PressureStallMonitor(root=tmp_path / "pressure", clock=clock)
    assert psi.collect() == {}
    clock.ns += CYCLE_NS
    assert psi.collect() == {}
    assert len(psi_logs(caplog)) == 1, psi_logs(caplog)


def test_psi_unreadable_resource_is_skipped_and_not_retried(
    tmp_path: Path, caplog: LogCaptureFixture, monkeypatch: MonkeyPatch
) -> None:
    # Kernels 4.20-5.5 booted with psi=0 have the files, but reading them fails with EOPNOTSUPP
    caplog.set_level(logging.INFO)
    io_reads = 0
    real_read_text = Path.read_text

    def read_text(self: Path, *args: object, **kwargs: object) -> str:
        nonlocal io_reads
        if self.name == "io":
            io_reads += 1
            raise OSError(errno.EOPNOTSUPP, "Operation not supported")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", read_text)
    clock = FakeClock()
    psi = PressureStallMonitor(root=tmp_path, clock=clock)
    write_all_psi(tmp_path, 0)
    psi.collect()
    for _ in range(2):
        clock.ns += CYCLE_NS
        assert set(psi.collect()) == {"psi.cpu.some", "psi.memory.some"}
    assert io_reads == 1
    assert len(psi_logs(caplog)) == 1, psi_logs(caplog)
    assert "psi.io.some" in psi_logs(caplog)[0]


def test_psi_counter_reset_drops_one_sample(tmp_path: Path) -> None:
    clock = FakeClock()
    psi = PressureStallMonitor(root=tmp_path, clock=clock)
    write_all_psi(tmp_path, 10_000_000)
    psi.collect()

    clock.ns += CYCLE_NS
    write_psi(tmp_path, "cpu", 500_000)  # went down
    values = psi.collect()
    assert "psi.cpu.some" not in values
    assert set(values) == {"psi.memory.some", "psi.io.some"}

    clock.ns += CYCLE_NS
    write_psi(tmp_path, "cpu", 500_000 + 600_000)  # diffed against the post-reset reading
    assert psi.collect()["psi.cpu.some"] == 1.0


def test_psi_no_elapsed_time_drops_sample(tmp_path: Path) -> None:
    clock = FakeClock()
    psi = PressureStallMonitor(root=tmp_path, clock=clock)
    write_all_psi(tmp_path, 0)
    psi.collect()
    write_all_psi(tmp_path, 1_000)
    assert psi.collect() == {}


def test_psi_is_clamped_to_100(tmp_path: Path) -> None:
    # The stall counter and our clock are read at slightly different times, so the ratio can exceed 1
    clock = FakeClock()
    psi = PressureStallMonitor(root=tmp_path, clock=clock)
    write_all_psi(tmp_path, 0)
    psi.collect()
    clock.ns += CYCLE_NS
    write_all_psi(tmp_path, 61_000_000)
    assert psi.collect() == {f"psi.{resource}.some": 100.0 for resource in PSI_RESOURCES}


def test_sanitize_ext_metrics_drops_non_finite_values() -> None:
    # json.dumps writes NaN/Infinity, which Go's json.Unmarshal rejects (the whole header would fail to parse)
    assert sanitize_ext_metrics({"a": 1.0, "b": math.nan, "c": math.inf, "d": -math.inf, "e": 0.0}) == {
        "a": 1.0,
        "e": 0.0,
    }


def test_sanitize_ext_metrics_enforces_the_cap(caplog: LogCaptureFixture) -> None:
    ext = {f"ext.m{i:02d}": float(i) for i in range(MAX_EXT_METRICS + 8)}
    sanitized = sanitize_ext_metrics(ext)
    assert sanitized == {f"ext.m{i:02d}": float(i) for i in range(MAX_EXT_METRICS)}
    assert any("over the cap" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_noop_monitor_has_no_ext_metrics() -> None:
    assert NoopSystemMetricsMonitor().get_metrics().ext == {}


def test_psi_can_be_disabled() -> None:
    assert SystemMetricsMonitor(Event(), collect_psi=False).get_metrics().ext == {}


@pytest.mark.skipif(not Path("/proc/pressure/cpu").exists(), reason="PSI is not enabled on this kernel")
def test_psi_on_this_host() -> None:
    # start() primes PSI, so the first snapshot already has values
    stop_event = Event()
    monitor = SystemMetricsMonitor(stop_event)
    monitor.start()
    try:
        time.sleep(0.2)
        ext = monitor.get_metrics().ext
    finally:
        stop_event.set()
        monitor.stop()
    assert set(ext) == {f"psi.{resource}.some" for resource in PSI_RESOURCES}
    assert all(0.0 <= value <= 100.0 for value in ext.values())
