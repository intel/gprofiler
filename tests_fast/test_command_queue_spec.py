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

"""
Fast acceptance tests for the agent command queue (command control plane).

WORKLOAD_LEVEL_PROFILING_SPEC.md keeps the *command-execution model unchanged*
(AT-A6/AT-A7/AT-A8): the agent still receives commands and runs them through the
existing prioritized queue. This suite locks that queue behavior down:

* stop > adhoc > continuous priority
* continuous is a singleton slot (a new continuous command replaces the old one)
* dequeue is position- and id-checked (idempotent completion reporting, AT-A8)
* stop/adhoc cannot be paused; only continuous can

``command_control`` is pure stdlib, so we load it directly by path to avoid
importing the dynamic_profiling_management package (whose __init__ pulls the full
profiler dependency stack). This keeps the suite dependency-free and instant.
"""

import datetime
import importlib.util
from pathlib import Path

import pytest

_CC_PATH = (
    Path(__file__).resolve().parents[1]
    / "gprofiler"
    / "dynamic_profiling_management"
    / "command_control.py"
)

_spec = importlib.util.spec_from_file_location("command_control_under_test", _CC_PATH)
command_control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(command_control)

CommandManager = command_control.CommandManager
ProfilingCommand = command_control.ProfilingCommand
ADHOC_QUEUE_MAX_SIZE = command_control.ADHOC_QUEUE_MAX_SIZE
STOP_QUEUE_MAX_SIZE = command_control.STOP_QUEUE_MAX_SIZE


def _cmd(command_id, command_type="start", is_continuous=False, profiling_command=None):
    return ProfilingCommand(
        command_id=command_id,
        command_type=command_type,
        profiling_command=profiling_command or {},
        is_continuous=is_continuous,
        timestamp=datetime.datetime(2024, 1, 1),
    )


@pytest.fixture
def manager():
    return CommandManager()


class TestQueuePrioritySpec:
    def test_empty_manager_has_no_next_command(self, manager):
        assert manager.get_next_command() is None
        assert manager.has_queued_commands() is False

    def test_stop_beats_adhoc_and_continuous(self, manager):
        manager.enqueue_command(_cmd("cont", is_continuous=True))
        manager.enqueue_command(_cmd("adhoc"))
        manager.enqueue_command(_cmd("stop", command_type="stop"))
        assert manager.get_next_command().command_id == "stop"

    def test_adhoc_beats_continuous(self, manager):
        manager.enqueue_command(_cmd("cont", is_continuous=True))
        manager.enqueue_command(_cmd("adhoc"))
        assert manager.get_next_command().command_id == "adhoc"

    def test_adhoc_is_fifo(self, manager):
        manager.enqueue_command(_cmd("a1"))
        manager.enqueue_command(_cmd("a2"))
        assert manager.get_next_command().command_id == "a1"

    def test_get_next_is_a_peek_not_a_pop(self, manager):
        manager.enqueue_command(_cmd("adhoc"))
        assert manager.get_next_command().command_id == "adhoc"
        # Peeking twice returns the same command (not removed).
        assert manager.get_next_command().command_id == "adhoc"


class TestContinuousSingletonSpec:
    def test_new_continuous_replaces_previous(self, manager):
        manager.enqueue_command(_cmd("cont-1", is_continuous=True))
        manager.enqueue_command(_cmd("cont-2", is_continuous=True))
        assert len(manager.continuous_queue) == 1
        assert manager.get_next_command().command_id == "cont-2"


class TestQueueBoundsSpec:
    def test_adhoc_queue_rejects_when_full(self, manager):
        for i in range(ADHOC_QUEUE_MAX_SIZE):
            assert manager.enqueue_command(_cmd(f"a{i}")) is True
        assert manager.enqueue_command(_cmd("overflow")) is False
        assert len(manager.adhoc_queue) == ADHOC_QUEUE_MAX_SIZE
        # FIFO order preserved; the rejected command is not in the queue.
        assert manager.get_next_command().command_id == "a0"
        assert all(c.command_id != "overflow" for c in manager.adhoc_queue)

    def test_adhoc_queue_accepts_again_after_dequeue(self, manager):
        for i in range(ADHOC_QUEUE_MAX_SIZE):
            manager.enqueue_command(_cmd(f"a{i}"))
        assert manager.dequeue_command("a0") is True
        assert manager.enqueue_command(_cmd("a-new")) is True

    def test_new_stop_replaces_queued_stop(self, manager):
        assert manager.enqueue_command(_cmd("stop-1", command_type="stop")) is True
        assert manager.enqueue_command(_cmd("stop-2", command_type="stop")) is True
        assert len(manager.stop_queue) == STOP_QUEUE_MAX_SIZE
        assert manager.get_next_command().command_id == "stop-2"

    def test_continuous_enqueue_reports_success(self, manager):
        assert manager.enqueue_command(_cmd("cont", is_continuous=True)) is True


class TestDequeueSpec:
    def test_dequeue_removes_head_by_id(self, manager):
        manager.enqueue_command(_cmd("adhoc"))
        assert manager.dequeue_command("adhoc") is True
        assert manager.has_queued_commands() is False

    def test_dequeue_wrong_id_is_noop(self, manager):
        manager.enqueue_command(_cmd("adhoc"))
        assert manager.dequeue_command("nope") is False
        assert manager.get_next_command().command_id == "adhoc"

    def test_dequeue_is_idempotent(self, manager):
        # AT-A8: repeated completion reports for the same command must not error
        # or remove a different command.
        manager.enqueue_command(_cmd("adhoc"))
        assert manager.dequeue_command("adhoc") is True
        assert manager.dequeue_command("adhoc") is False

    def test_stop_has_priority_for_dequeue(self, manager):
        manager.enqueue_command(_cmd("adhoc"))
        manager.enqueue_command(_cmd("stop", command_type="stop"))
        # adhoc is not at the head of any queue that outranks stop, so removing it
        # directly still works because it is the head of the adhoc queue...
        assert manager.dequeue_command("stop") is True
        assert manager.get_next_command().command_id == "adhoc"


class TestPauseSpec:
    def test_continuous_can_be_paused_and_blocks_dequeue(self, manager):
        manager.enqueue_command(_cmd("cont", is_continuous=True))
        assert manager.pause_command("cont") is True
        # A paused continuous command must not be dequeued out from under a pause.
        assert manager.dequeue_command("cont") is False

    def test_stop_and_adhoc_cannot_be_paused(self, manager):
        manager.enqueue_command(_cmd("stop", command_type="stop"))
        manager.enqueue_command(_cmd("adhoc"))
        assert manager.pause_command("stop") is False
        assert manager.pause_command("adhoc") is False


class TestLifecycleSpec:
    def test_clear_queues_empties_everything(self, manager):
        manager.enqueue_command(_cmd("stop", command_type="stop"))
        manager.enqueue_command(_cmd("adhoc"))
        manager.enqueue_command(_cmd("cont", is_continuous=True))
        manager.clear_queues()
        assert manager.has_queued_commands() is False
        assert manager.get_next_command() is None

    def test_pids_travel_in_the_command_payload(self, manager):
        # AT-A7: backend-resolved PIDs ride along in the command payload the queue
        # carries; the queue itself is targeting-agnostic.
        payload = {"pids": [1234, 5678], "duration": 60}
        manager.enqueue_command(_cmd("adhoc", profiling_command=payload))
        assert manager.get_next_command().profiling_command["pids"] == [1234, 5678]
