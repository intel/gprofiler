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
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger(__name__)

# Command queue size limits
STOP_QUEUE_MAX_SIZE = 1  # Maximum stop commands to queue
ADHOC_QUEUE_MAX_SIZE = 10  # Maximum ad-hoc commands to queue
CONTINUOUS_QUEUE_MAX_SIZE = 1  # Maximum continuous commands to queue


@dataclass
class ProfilingCommand:
    """Represents a profiling command with metadata"""

    command_id: str
    command_type: str  # 'start' or 'stop'
    profiling_command: Dict[str, Any]
    is_continuous: bool
    timestamp: datetime.datetime
    is_paused: bool = False


class CommandManager:
    """Manager for profiling command queues with priority-based execution"""

    def __init__(self) -> None:
        # Command queues
        self.stop_queue: Deque[ProfilingCommand] = deque()  # For stop commands (highest priority)
        self.adhoc_queue: Deque[ProfilingCommand] = deque()  # For single-run commands (continuous=False)
        self.continuous_queue: Deque[ProfilingCommand] = deque()  # For continuous commands (continuous=True)
        self.queue_lock = threading.Lock()  # Thread-safe queue operations

    def enqueue_command(self, command: ProfilingCommand) -> ProfilingCommand:
        """Enqueue a command to the appropriate queue

        Args:
            command: ProfilingCommand object to enqueue

        Returns:
            ProfilingCommand object that was enqueued
        """
        # Add to appropriate queue
        with self.queue_lock:
            if command.command_type == "stop":
                # Warn if stop queue exceeds limit
                if len(self.stop_queue) >= STOP_QUEUE_MAX_SIZE:
                    logger.warning(
                        f"Stop queue exceeds limit (max: {STOP_QUEUE_MAX_SIZE}, current: {len(self.stop_queue)}), "
                        f"but adding command {command.command_id} anyway"
                    )

                self.stop_queue.append(command)
                logger.info(f"Enqueued stop command {command.command_id} (queue size: {len(self.stop_queue)})")
            elif command.is_continuous:
                # No need for warnings. The queue is always cleared before adding a new continuous command.
                # Clear continuous queue before adding new continuous command
                if self.continuous_queue:
                    logger.info(
                        f"Clearing {len(self.continuous_queue)} existing continuous commands "
                        f"before adding new command {command.command_id}"
                    )
                    self.continuous_queue.clear()

                self.continuous_queue.append(command)
                logger.info(
                    f"Enqueued continuous command {command.command_id} (queue size: {len(self.continuous_queue)})"
                )
            else:
                # Warn if ad-hoc queue exceeds limit
                if len(self.adhoc_queue) >= ADHOC_QUEUE_MAX_SIZE:
                    logger.warning(
                        f"Ad-hoc queue exceeds limit (max: {ADHOC_QUEUE_MAX_SIZE}, current: {len(self.adhoc_queue)}), "
                        f"but adding command {command.command_id} anyway"
                    )

                self.adhoc_queue.append(command)
                logger.info(f"Enqueued ad-hoc command {command.command_id} (queue size: {len(self.adhoc_queue)})")

        return command

    def get_next_command(self) -> Optional[ProfilingCommand]:
        """Peek at the next command to execute based on priority logic without removing it.

        Priority strategy:
        1. Stop commands have highest priority (immediate termination)
        2. Ad-hoc commands have higher priority than continuous commands
        3. Within each queue, FIFO order is maintained
        4. If no ad-hoc commands exist, fetch from continuous queue

        Returns:
            ProfilingCommand if available, None otherwise
        """
        with self.queue_lock:
            # Priority 1: Stop commands (highest priority)
            if self.stop_queue:
                cmd = self.stop_queue[0]
                logger.debug(f"Peeking at stop command {cmd.command_id} from queue (size: {len(self.stop_queue)})")
                return cmd

            # Priority 2: Ad-hoc commands (single-run, immediate execution)
            if self.adhoc_queue:
                cmd = self.adhoc_queue[0]
                logger.debug(f"Peeking at ad-hoc command {cmd.command_id} from queue (size: {len(self.adhoc_queue)})")
                return cmd

            # Priority 3: Continuous commands (long-running)
            if self.continuous_queue:
                cmd = self.continuous_queue[0]
                logger.debug(
                    f"Peeking at continuous command {cmd.command_id} from queue (size: {len(self.continuous_queue)})"
                )
                return cmd

            logger.debug("No commands in queues")
            return None

    def dequeue_command(self, command_id: str) -> bool:
        """Remove a command from the queue by command_id if it's at the first position.

        Checks queues in priority order (stop > adhoc > continuous) and removes
        the command only if it's at the first position of its queue.

        Args:
            command_id: The ID of the command to remove

        Returns:
            True if command was found and removed, False otherwise
        """
        with self.queue_lock:
            # Check stop queue first (highest priority)
            if self.stop_queue and self.stop_queue[0].command_id == command_id:
                # No need to check for pause on stop commands
                self.stop_queue.popleft()
                logger.info(f"Dequeued stop command {command_id} from queue (remaining: {len(self.stop_queue)})")
                return True

            # Check ad-hoc queue
            if self.adhoc_queue and self.adhoc_queue[0].command_id == command_id:
                # No need to check for pause on ad-hoc commands
                self.adhoc_queue.popleft()
                logger.info(f"Dequeued ad-hoc command {command_id} from queue (remaining: {len(self.adhoc_queue)})")
                return True

            # Check continuous queue
            if self.continuous_queue and self.continuous_queue[0].command_id == command_id:
                if self.continuous_queue[0].is_paused:
                    logger.info(f"Cannot dequeue continuous command {command_id} because it is paused")
                    return False
                self.continuous_queue.popleft()
                logger.info(
                    f"Dequeued continuous command {command_id} from queue (remaining: {len(self.continuous_queue)})"
                )
                return True

            logger.debug(f"Command {command_id} not found at first position in any queue. Possibly already dequeued.")
            return False

    def pause_command(self, command_id: str) -> bool:
        """Pause a command by setting its is_paused attribute to True.

        Checks the first element of each queue and if the command_id matches,
        sets the is_paused attribute to True.

        Args:
            command_id: The ID of the command to pause

        Returns:
            True if command was found and paused, False otherwise
        """
        with self.queue_lock:
            # Check stop queue first (highest priority)
            if self.stop_queue and self.stop_queue[0].command_id == command_id:
                logger.warning(f"Stop commands cannot be paused. Command {command_id} remains active.")
                return False

            # Check ad-hoc queue
            if self.adhoc_queue and self.adhoc_queue[0].command_id == command_id:
                logger.warning(f"Ad-hoc commands cannot be paused. Command {command_id} remains active.")
                return False

            # Check continuous queue
            if self.continuous_queue and self.continuous_queue[0].command_id == command_id:
                self.continuous_queue[0].is_paused = True
                logger.info(f"Paused continuous command {command_id}")
                return True

            logger.debug(f"Command {command_id} not found at first position in any queue")
            return False

    def has_queued_commands(self) -> bool:
        """Check if there are any commands in the queues

        Returns:
            True if any queue has pending commands, False otherwise
        """
        with self.queue_lock:
            return len(self.stop_queue) > 0 or len(self.adhoc_queue) > 0 or len(self.continuous_queue) > 0

    def clear_queues(self) -> None:
        """Clear all queued commands (used during shutdown)"""
        with self.queue_lock:
            stop_count = len(self.stop_queue)
            adhoc_count = len(self.adhoc_queue)
            continuous_count = len(self.continuous_queue)
            self.stop_queue.clear()
            self.adhoc_queue.clear()
            self.continuous_queue.clear()
            if stop_count > 0 or adhoc_count > 0 or continuous_count > 0:
                logger.info(
                    f"Cleared {stop_count} stop, {adhoc_count} ad-hoc and "
                    f"{continuous_count} continuous commands from queues"
                )
