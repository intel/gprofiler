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

import logging
from typing import Any, Dict

from gprofiler.dynamic_profiling_management import ProfilerSlotBase, get_enabled_profiler_types
from gprofiler.dynamic_profiling_management.command_control import ProfilingCommand

logger = logging.getLogger(__name__)


class AdhocProfilerSlot(ProfilerSlotBase):
    """Manages the parallel ad-hoc profiler slot.

    This slot is used *only* when the incoming ad-hoc command enables a set of
    profiler types that does **not** overlap with the profiler types already
    running in the primary (continuous) slot.  If there is overlap the manager
    falls back to time-slicing (pause primary, run ad-hoc, resume primary).
    """

    SLOT_NAME = "ad-hoc"

    def start(self, profiling_command: Dict[str, Any], command_id: str) -> None:
        """Start an ad-hoc profiler in the parallel slot (always non-continuous)."""
        try:
            self._start_profiler(profiling_command, command_id, continuous=False)
        except Exception as e:
            logger.error(f"Failed to start parallel ad-hoc profiler: {e}", exc_info=True)
            self._heartbeat_client.send_command_completion(
                command_id=command_id,
                status="failed",
                execution_time=0,
                error_message=str(e),
                results_path=None,
            )
            self._clear_state()

    def cleanup_if_completed(self) -> None:
        """If the ad-hoc thread has finished, clear the slot so it can be reused."""
        if self.thread and not self.thread.is_alive():
            self.gprofiler = None
            self.thread = None
            self._clear_state()

    def can_run(self, next_cmd: ProfilingCommand, current_profiler_types: set) -> bool:
        """Return True if *next_cmd* can safely run in the ad-hoc slot in parallel.

        Conditions:
        1. Ad-hoc slot is currently free.
        2. The incoming command is **not** continuous.
        3. The profiler types enabled by the incoming command do **not** overlap
           with *current_profiler_types* (what the primary slot is running).
        """
        if self.gprofiler is not None:
            return False
        if next_cmd.is_continuous:
            return False
        next_types = get_enabled_profiler_types(next_cmd.profiling_command)
        return not bool(next_types & current_profiler_types)

    def _on_complete(self, command_id: str) -> None:
        logger.info(f"Parallel ad-hoc profiler completed for command ID: {command_id}")
