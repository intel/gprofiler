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

logger = logging.getLogger(__name__)


class MemoryManager:
    """Centralized memory management for gProfiler with configurable options."""

    def __init__(self):
        """Initialize memory manager."""
        self._cleanup_count = 0

    def _cleanup_subprocess_objects(self) -> dict:
        """Clean up completed subprocess objects to prevent pdeathsigger memory leaks.

        This is the main fix for the pdeathsigger subprocess memory leak - completed
        subprocess.Popen objects accumulate in the global _processes list and never
        get removed, keeping references to hundreds of completed pdeathsigger processes.

        Returns:
            dict: Statistics about subprocess cleanup
        """

        try:
            # Import here to avoid circular imports
            from gprofiler.utils import cleanup_completed_processes

            # Perform the actual cleanup
            cleanup_result = cleanup_completed_processes()

            # Log results if significant cleanup occurred
            if cleanup_result["processes_cleaned"] > 0:
                logger.info(
                    f"Subprocess cleanup: removed {cleanup_result['processes_cleaned']} "
                    f"completed processes, {cleanup_result['running_processes']} still running"
                )

            return cleanup_result

        except Exception as e:
            logger.warning(f"Subprocess cleanup failed: {e}")
            return {
                "total_processes": 0,
                "completed_processes": 0,
                "running_processes": 0,
                "processes_cleaned": 0,
                "error": str(e),
            }
