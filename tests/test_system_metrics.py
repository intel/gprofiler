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

import time
from threading import Event, Thread
from typing import List, Optional

from gprofiler.system_metrics import SystemMetricsMonitor


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
