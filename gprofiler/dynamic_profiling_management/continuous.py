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

import datetime
import logging
from typing import Any, Dict, Optional

from gprofiler.dynamic_profiling_management import ProfilerSlotBase

logger = logging.getLogger(__name__)


class ContinuousProfilerSlot(ProfilerSlotBase):
    """Manages the primary profiler slot (continuous or single-run).

    This slot handles the main profiling workload.  When running a continuous
    profiler it can be *paused* (stopped and re-queued) so that a higher-priority
    ad-hoc command can take its place.
    """

    SLOT_NAME = "continuous"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.command_start_time: Optional[datetime.datetime] = None

    def start(self, profiling_command: Dict[str, Any], command_id: str) -> None:
        """Start a profiler in the primary slot."""
        try:
            combined_config = profiling_command.get("combined_config", {})
            continuous = combined_config.get("continuous", False)
            self._start_profiler(profiling_command, command_id, continuous)
            self.command_start_time = datetime.datetime.now()
        except Exception as e:
            logger.error(f"Failed to start primary profiler: {e}", exc_info=True)
            self._heartbeat_client.send_command_completion(
                command_id=command_id,
                status="failed",
                execution_time=0,
                error_message=str(e),
                results_path=None,
            )
            self._clear_state()

    def can_be_paused(self) -> bool:
        """A primary profiler can be paused only if it is running in continuous mode."""
        return self.command is not None and self.command.is_continuous

    def _clear_state(self) -> None:
        super()._clear_state()
        self.command_start_time = None

    def _on_complete(self, command_id: str) -> None:
        if self._command_manager.has_queued_commands() and not self._stop_event.is_set():
            logger.info("Primary profiler completed, checking for next queued command...")
