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
import socket
import threading
from typing import Dict, Any, Optional

import configargparse
import requests

from gprofiler.dynamic_profiling_management.ad_hoc import AdhocProfilerSlot
from gprofiler.dynamic_profiling_management.command_control import CommandManager, ProfilingCommand
from gprofiler.dynamic_profiling_management.continuous import ContinuousProfilerSlot
from gprofiler.metadata.system_metadata import get_hostname
from gprofiler.metrics_publisher import (
    MetricsPublisher,
    RESPONSE_TYPE_SUCCESS,
    RESPONSE_TYPE_FAILURE,
)
from gprofiler.profilers.pmu_manager import get_pmu_manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HeartbeatClient — HTTP communication with the profiling server
# ---------------------------------------------------------------------------


class HeartbeatClient:
    """Client for sending heartbeats to the server and receiving profiling commands."""

    def __init__(
        self,
        api_server: str,
        service_name: str,
        server_token: str,
        verify: bool = True,
        tls_client_cert: Optional[str] = None,
        tls_client_key: Optional[str] = None,
        tls_ca_bundle: Optional[str] = None,
        tls_cert_refresh_enabled: bool = False,
        tls_cert_refresh_interval: int = 21600,
    ):
        self.api_server = api_server.rstrip("/")
        self.service_name = service_name
        self.server_token = server_token
        self.verify = verify
        self.tls_client_cert = tls_client_cert
        self.tls_client_key = tls_client_key
        self.tls_ca_bundle = tls_ca_bundle
        self.tls_cert_refresh_enabled = tls_cert_refresh_enabled
        self.tls_cert_refresh_interval = tls_cert_refresh_interval
        self.hostname = get_hostname()
        self.ip_address = self._get_local_ip()
        self.last_command_id: Optional[str] = None
        self.received_command_ids: set = set()
        self.executed_command_ids: set = set()
        self.max_command_history = 1000
        self._refresh_thread: Optional[threading.Thread] = None
        self._refresh_stop_event = threading.Event()

        self._init_session()
        self.pmu_manager = get_pmu_manager()

        if self.server_token:
            self.session.headers.update(
                {"Authorization": f"Bearer {self.server_token}", "Content-Type": "application/json"}
            )

        if self.tls_cert_refresh_enabled and (self.tls_client_cert or self.tls_ca_bundle):
            self._start_cert_refresh_thread()

    # --- TLS session management ---

    def _init_session(self) -> None:
        self.session = requests.Session()
        if self.tls_ca_bundle:
            self.session.verify = self.tls_ca_bundle
        else:
            self.session.verify = self.verify
        if self.tls_client_cert and self.tls_client_key:
            self.session.cert = (self.tls_client_cert, self.tls_client_key)
            logger.debug(f"HeartbeatClient: mTLS enabled with client cert: {self.tls_client_cert}")
        elif self.tls_client_cert or self.tls_client_key:
            logger.warning(
                "HeartbeatClient: Both --tls-client-cert and --tls-client-key must be provided for mTLS. "
                "Ignoring partial configuration."
            )

    def _refresh_session(self) -> None:
        old_session = self.session
        try:
            logger.debug("HeartbeatClient: Refreshing TLS session to reload certificates")
            self._init_session()
            if self.server_token:
                self.session.headers.update(
                    {"Authorization": f"Bearer {self.server_token}", "Content-Type": "application/json"}
                )
            old_session.close()
            logger.info("HeartbeatClient: TLS session refreshed successfully")
        except Exception as e:
            self.session = old_session
            logger.error(f"HeartbeatClient: Failed to refresh TLS session: {e}. Will retry on next interval.")

    def _cert_refresh_loop(self) -> None:
        logger.info(
            f"HeartbeatClient: Certificate refresh thread started (interval: {self.tls_cert_refresh_interval}s)"
        )
        while not self._refresh_stop_event.wait(self.tls_cert_refresh_interval):
            self._refresh_session()
        logger.debug("HeartbeatClient: Certificate refresh thread stopped")

    def _start_cert_refresh_thread(self) -> None:
        if self._refresh_thread is None or not self._refresh_thread.is_alive():
            self._refresh_thread = threading.Thread(
                target=self._cert_refresh_loop, daemon=True, name="HeartbeatClient-CertRefresh"
            )
            self._refresh_thread.start()

    def stop_cert_refresh(self) -> None:
        if self._refresh_thread and self._refresh_thread.is_alive():
            logger.debug("HeartbeatClient: Stopping certificate refresh thread")
            self._refresh_stop_event.set()
            self._refresh_thread.join(timeout=5)
            if self._refresh_thread.is_alive():
                logger.warning("HeartbeatClient: Certificate refresh thread did not stop gracefully")

    # --- Networking helpers ---

    @staticmethod
    def _get_local_ip() -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    # --- Heartbeat & command lifecycle ---

    def send_heartbeat(self) -> Optional[Dict[str, Any]]:
        try:
            perf_supported_events = self.pmu_manager.get_supported_events()
            heartbeat_data = {
                "ip_address": self.ip_address,
                "hostname": self.hostname,
                "service_name": self.service_name,
                "last_command_id": self.last_command_id,
                "status": "active",
                "timestamp": datetime.datetime.now().isoformat(),
                "received_command_ids": list(self.received_command_ids),
                "executed_command_ids": list(self.executed_command_ids),
                "perf_supported_events": perf_supported_events,
            }
            url = f"{self.api_server}/api/metrics/heartbeat"
            response = self.session.post(url, json=heartbeat_data, timeout=30)

            if response.status_code == 200:
                MetricsPublisher.get_instance().send_sli_metric(
                    response_type=RESPONSE_TYPE_SUCCESS, method_name="send_heartbeat"
                )
                result = response.json()
                if result.get("success") and result.get("profiling_command"):
                    logger.info(f"Received profiling command from server: {result.get('command_id')}")
                    return result
                logger.debug("Heartbeat successful, no pending commands")
                return None
            else:
                logger.warning(f"Heartbeat failed with status {response.status_code}: {response.text}")
                MetricsPublisher.get_instance().send_sli_metric(
                    response_type=RESPONSE_TYPE_FAILURE,
                    method_name="send_heartbeat",
                    extra_tags={"status_code": response.status_code},
                )
                return None
        except Exception as e:
            logger.error(f"Failed to send heartbeat: {e}")
            MetricsPublisher.get_instance().send_sli_metric(
                response_type=RESPONSE_TYPE_FAILURE,
                method_name="send_heartbeat",
                extra_tags={"error": str(e)},
            )
            return None

    def send_command_completion(
        self,
        command_id: str,
        status: str,
        execution_time: Optional[int] = None,
        error_message: Optional[str] = None,
        results_path: Optional[str] = None,
    ) -> bool:
        try:
            completion_data = {
                "command_id": command_id,
                "hostname": self.hostname,
                "status": status,
                "execution_time": execution_time,
                "error_message": error_message,
                "results_path": results_path,
            }
            url = f"{self.api_server}/api/metrics/command_completion"
            response = self.session.post(url, json=completion_data, timeout=30)
            if response.status_code == 200:
                logger.info(f"Reported command completion for {command_id} (status={status})")
                return True
            logger.error(
                f"Failed to report command completion for {command_id}. "
                f"Status: {response.status_code}, Response: {response.text}"
            )
            return False
        except Exception as e:
            logger.error(f"Failed to send command completion for {command_id}: {e}")
            return False

    # --- Idempotency tracking ---

    def mark_command_received(self, command_id: str) -> None:
        self.received_command_ids.add(command_id)
        if len(self.received_command_ids) > self.max_command_history:
            self._trim_set(self.received_command_ids)
        logger.debug(f"Marked command ID {command_id} as received")

    def mark_command_executed(self, command_id: str) -> None:
        self.executed_command_ids.add(command_id)
        if len(self.executed_command_ids) > self.max_command_history:
            self._trim_set(self.executed_command_ids)
        logger.debug(f"Marked command ID {command_id} as executed")

    def _trim_set(self, id_set: set) -> None:
        try:
            items = list(id_set)
            to_keep = items[-self.max_command_history :]
            id_set.clear()
            id_set.update(to_keep)
            logger.info(f"Trimmed command ID set to {len(id_set)} entries")
        except Exception as e:
            logger.warning(f"Failed to trim command ID set: {e}")


# ---------------------------------------------------------------------------
# DynamicGProfilerManager — orchestrates the two profiler slots
# ---------------------------------------------------------------------------


class DynamicGProfilerManager:
    """Orchestrates profiler lifecycle based on commands received via heartbeat.

    Uses two slots:
    * **continuous** (`ContinuousProfilerSlot`) — runs the long-running
      continuous profiler.
    * **adhoc** (`AdhocProfilerSlot`) — runs ad-hoc profilers, either in
      parallel (when profiler types don't overlap with the continuous slot)
      or after time-slicing (pause continuous, run ad-hoc, resume continuous).
    """

    def __init__(self, base_args: configargparse.Namespace, heartbeat_client: HeartbeatClient):
        self.heartbeat_client = heartbeat_client
        self.stop_event = threading.Event()
        self.heartbeat_interval = 30
        self.command_manager = CommandManager()

        self.continuous = ContinuousProfilerSlot(base_args, heartbeat_client, self.command_manager, self.stop_event)
        self.adhoc = AdhocProfilerSlot(base_args, heartbeat_client, self.command_manager, self.stop_event)

    # --- Main loop ---

    def start_heartbeat_loop(self) -> None:
        logger.info("Starting heartbeat loop...")
        while not self.stop_event.is_set():
            try:
                # Step 1: Heartbeat — fetch & enqueue any new command
                response = self.heartbeat_client.send_heartbeat()
                if response and response.get("profiling_command"):
                    self._enqueue_command(response)

                # Step 2: Cleanup completed ad-hoc profiler
                self.adhoc.cleanup_if_completed()

                # Step 3: Process next queued command
                next_cmd = self.command_manager.get_next_command()
                if self._should_process(next_cmd):
                    self._process_command(next_cmd)

                self.stop_event.wait(self.heartbeat_interval)
            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}", exc_info=True)
                self.stop_event.wait(self.heartbeat_interval)

    # --- Command handling ---

    def _enqueue_command(self, command_response: Dict[str, Any]) -> None:
        command_id = command_response["command_id"]
        profiling_command = command_response["profiling_command"]

        if command_id in self.heartbeat_client.received_command_ids:
            logger.info(f"Command ID {command_id} already received, skipping...")
            return

        self.heartbeat_client.mark_command_received(command_id)
        self.heartbeat_client.last_command_id = command_id

        command_type = profiling_command.get("command_type", "start")
        combined_config = profiling_command.get("combined_config", {})
        is_continuous = combined_config.get("continuous", False)

        cmd = ProfilingCommand(
            command_id=command_id,
            command_type=command_type,
            profiling_command=profiling_command,
            is_continuous=is_continuous,
            timestamp=datetime.datetime.now(),
            is_paused=False,
        )
        self.command_manager.enqueue_command(cmd)

    def _process_command(self, cmd: ProfilingCommand) -> None:
        if cmd.command_type == "stop":
            logger.info(f"Processing STOP command {cmd.command_id}")
            self.continuous.stop()
            self.adhoc.stop()
            self.command_manager.clear_queues()
            self.heartbeat_client.mark_command_executed(cmd.command_id)
            self.heartbeat_client.send_command_completion(
                command_id=cmd.command_id, status="completed", execution_time=0
            )
            return

        if cmd.command_type != "start":
            logger.warning(f"Unknown command type: {cmd.command_type}")
            self.heartbeat_client.send_command_completion(
                command_id=cmd.command_id,
                status="failed",
                execution_time=0,
                error_message=f"Unknown command type: {cmd.command_type}",
            )
            return

        started = False

        if cmd.is_continuous:
            # Continuous commands always target the primary slot
            if not self.continuous.is_running():
                logger.info("Starting continuous profiler for command %s", cmd.command_id)
                self.continuous.start(cmd.profiling_command, cmd.command_id)
                started = True
            elif self.continuous.can_be_paused():
                logger.info(
                    "Replacing current continuous profiler with command %s", cmd.command_id
                )
                self.command_manager.pause_command(self.continuous.command.command_id)
                self.continuous.stop()
                self.continuous.start(cmd.profiling_command, cmd.command_id)
                started = True
        else:
            # Ad-hoc commands: always use ad-hoc slot if possible (parallel or time-slice)
            if not self.continuous.is_running():
                # Nothing running at all — use the adhoc slot (only if free)
                if not self.adhoc.is_running():
                    logger.info("Starting ad-hoc profiler for command %s (no active profiler)", cmd.command_id)
                    self.adhoc.start(cmd.profiling_command, cmd.command_id)
                    started = True
                else:
                    logger.debug("Ad-hoc slot busy; deferring command %s", cmd.command_id)
            elif self.adhoc.can_run(cmd, self.continuous.profiler_types):
                # Parallel path: can_run() already verified adhoc slot is free
                logger.info(
                    "Starting parallel ad-hoc profiler for command %s (non-overlapping profiler types)",
                    cmd.command_id,
                )
                self.adhoc.start(cmd.profiling_command, cmd.command_id)
                started = True
            elif self.continuous.can_be_paused() and not self.adhoc.is_running():
                # Time-slice path: pause continuous, run ad-hoc, continuous re-queued
                logger.info(
                    "Pausing continuous profiler for ad-hoc command %s (overlapping types)",
                    cmd.command_id,
                )
                self.command_manager.pause_command(self.continuous.command.command_id)
                self.continuous.stop()
                self.adhoc.start(cmd.profiling_command, cmd.command_id)
                started = True

        if started:
            self.heartbeat_client.mark_command_executed(cmd.command_id)
            try:
                self.heartbeat_client.send_command_completion(
                    command_id=cmd.command_id, status="completed", execution_time=0
                )
            except Exception as e:
                logger.error(f"Failed to report command completion for {cmd.command_id}: {e}")

    # --- Decision helpers ---

    def _should_process(self, cmd: Optional[ProfilingCommand]) -> bool:
        if cmd is None:
            return False

        if self.continuous.is_running_command(cmd.command_id):
            return False
        if self.adhoc.is_running_command(cmd.command_id):
            return False

        if cmd.is_continuous:
            # Continuous → needs primary slot (free or pausable)
            return not self.continuous.is_running() or self.continuous.can_be_paused()
        else:
            # Ad-hoc → needs adhoc slot (free, or primary pausable for time-slice)
            if not self.continuous.is_running():
                # Nothing running — adhoc slot must be free too
                return not self.adhoc.is_running()
            if self.adhoc.can_run(cmd, self.continuous.profiler_types):
                # Parallel path: can_run() already checks adhoc slot is free
                return True
            if self.continuous.can_be_paused() and not self.adhoc.is_running():
                # Time-slice path: need adhoc slot free to accept the paused work
                return True
            return False

    # --- Lifecycle ---

    def stop(self) -> None:
        logger.info("Stopping heartbeat manager...")
        self.stop_event.set()
        self.continuous.stop()
        self.adhoc.stop()
        self.command_manager.clear_queues()
        logger.info("Heartbeat manager stopped")
