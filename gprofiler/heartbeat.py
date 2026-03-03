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
import os
import socket
import threading
from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING

import configargparse
import requests

# Use TYPE_CHECKING to avoid circular imports
if TYPE_CHECKING:
    from gprofiler.main import GProfiler

from gprofiler.client import ProfilerAPIClient
from gprofiler.command_control import CommandManager, ProfilingCommand
from gprofiler.metadata.enrichment import EnrichmentOptions
from gprofiler.metadata.system_metadata import get_hostname
from gprofiler.state import get_state
from gprofiler.usage_loggers import NoopUsageLogger
from gprofiler.utils import TEMPORARY_STORAGE_PATH, resource_path


logger = logging.getLogger(__name__)


class HeartbeatClient:
    """Client for sending heartbeats to the server and receiving profiling commands"""

    def __init__(self, api_server: str, service_name: str, server_token: str, verify: bool = True):
        self.api_server = api_server.rstrip('/')
        self.service_name = service_name
        self.server_token = server_token
        self.verify = verify
        self.hostname = get_hostname()
        self.ip_address = self._get_local_ip()
        self.last_command_id: Optional[str] = None
        self.received_command_ids: set = set()  # Track received command IDs for idempotency (prevent duplicate processing)
        self.executed_command_ids: set = set()  # Track executed command IDs (commands that were actually processed)
        self.max_command_history = 1000  # Limit command history to prevent memory growth
        self.session = requests.Session()

        # Set up authentication headers
        if self.server_token:
            self.session.headers.update({
                'Authorization': f'Bearer {self.server_token}',
                'Content-Type': 'application/json'
            })

    def _get_local_ip(self) -> str:
        """Get the local IP address"""
        try:
            # Connect to a remote address to determine local IP
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    def send_heartbeat(self) -> Optional[Dict[str, Any]]:
        """Send heartbeat to server and return any profiling commands"""
        try:
            heartbeat_data = {
                "ip_address": self.ip_address,
                "hostname": self.hostname,
                "service_name": self.service_name,
                "last_command_id": self.last_command_id,
                "status": "active",
                "timestamp": datetime.datetime.now().isoformat(),
                "received_command_ids": list(self.received_command_ids),
                "executed_command_ids": list(self.executed_command_ids)
            }

            url = f"{self.api_server}/api/metrics/heartbeat"
            response = self.session.post(
                url,
                json=heartbeat_data,
                verify=self.verify,
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()

                if result.get("success") and result.get("profiling_command"):
                    logger.info(f"Received profiling command from server: {result.get('command_id')}")
                    return result
                else:
                    logger.debug("Heartbeat successful, no pending commands")
                    return None
            else:
                logger.warning(f"Heartbeat failed with status {response.status_code}: {response.text}")
                return None

        except Exception as e:
            logger.error(f"Failed to send heartbeat: {e}")
            return None

    def send_command_completion(
        self,
        command_id: str,
        status: str,
        execution_time: Optional[int] = None,
        error_message: Optional[str] = None,
        results_path: Optional[str] = None,
    ) -> bool:
        """
        Send command completion status to the server.

        Args:
            command_id: The ID of the completed command
            status: 'completed' or 'failed'
            execution_time: Duration of execution in seconds
            error_message: Error message if status is 'failed'
            results_path: Path to profiling results if available

        Returns:
            bool: True if completion was successfully reported, False otherwise
        """
        try:
            completion_data = {
                "command_id": command_id,
                "hostname": self.hostname,
                "status": status,
                "execution_time": execution_time,
                "error_message": error_message,
                "results_path": results_path
            }

            url = f"{self.api_server}/api/metrics/command_completion"
            response = self.session.post(
                url,
                json=completion_data,
                verify=self.verify,
                timeout=30
            )

            if response.status_code == 200:
                logger.info(f"Successfully reported command completion for {command_id} with status: {status}")
                return True
            else:
                logger.error(f"Failed to report command completion for {command_id}. Status: {response.status_code}, Response: {response.text}")
                return False

        except Exception as e:
            logger.error(f"Failed to send command completion for {command_id}: {e}")
            return False

    def mark_command_received(self, command_id: str):
        """Mark a command as received for idempotency (prevents duplicate processing of the same command)"""
        self.received_command_ids.add(command_id)

        # Cleanup old command IDs if we exceed the limit
        if len(self.received_command_ids) > self.max_command_history:
            self._cleanup_old_command_ids()

        logger.debug(f"Marked command ID {command_id} as received")

    def mark_command_executed(self, command_id: str):
        """Mark a command as executed (command was actually processed/acted upon)"""
        self.executed_command_ids.add(command_id)

        # Cleanup old command IDs if we exceed the limit
        if len(self.executed_command_ids) > self.max_command_history:
            self._cleanup_old_executed_command_ids()

        logger.debug(f"Marked command ID {command_id} as executed")

    def _cleanup_old_command_ids(self):
        """Remove old command IDs to prevent memory growth"""
        try:
            # Keep only the most recent commands (this is a simple approach)
            # In production, you might want to implement time-based cleanup
            if len(self.received_command_ids) > self.max_command_history:
                # Convert to list, sort, and keep the last max_command_history items
                command_list = list(self.received_command_ids)
                # Since UUIDs don't sort chronologically, we'll just remove some arbitrary ones
                # In a real implementation, you'd want to track timestamps
                commands_to_keep = command_list[-self.max_command_history:]
                self.received_command_ids = set(commands_to_keep)
                logger.info(f"Cleaned up command ID history in memory, keeping {len(self.received_command_ids)} entries")
        except Exception as e:
            logger.warning(f"Failed to cleanup old command IDs: {e}")

    def _cleanup_old_executed_command_ids(self):
        """Remove old executed command IDs to prevent memory growth"""
        try:
            if len(self.executed_command_ids) > self.max_command_history:
                command_list = list(self.executed_command_ids)
                commands_to_keep = command_list[-self.max_command_history:]
                self.executed_command_ids = set(commands_to_keep)
                logger.info(f"Cleaned up executed command ID history in memory, keeping {len(self.executed_command_ids)} entries")
        except Exception as e:
            logger.warning(f"Failed to cleanup old executed command IDs: {e}")


class DynamicGProfilerManager:
    """Manager for dynamically starting/stopping gProfiler instances based on server commands"""

    def __init__(self, base_args: configargparse.Namespace, heartbeat_client: HeartbeatClient):
        self.base_args = base_args
        self.heartbeat_client = heartbeat_client
        self.current_gprofiler: Optional['GProfiler'] = None
        self.current_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.heartbeat_interval = 30  # seconds

        # Command manager for queue operations
        self.command_manager = CommandManager()

        # Current command tracking
        self.current_command: Optional[ProfilingCommand] = None
        self.current_command_start_time: Optional[datetime.datetime] = None

    def start_heartbeat_loop(self):
        """Start the main heartbeat loop"""
        logger.info("Starting heartbeat loop...")

        while not self.stop_event.is_set():
            try:
                # Send heartbeat and check for commands
                command_response = self.heartbeat_client.send_heartbeat()

                # Step 1: Enqueue any received command
                if command_response and command_response.get("profiling_command"):
                    command_id = command_response["command_id"]
                    profiling_command = command_response["profiling_command"]

                    logger.info(f"Received profiling command: {profiling_command}")

                    # Check for idempotency - skip if command already received (prevents duplicate processing)
                    if command_id not in self.heartbeat_client.received_command_ids:
                        # Mark command as received for idempotency
                        self.heartbeat_client.mark_command_received(command_id)
                        self.heartbeat_client.last_command_id = command_id

                        # Convert to ProfilingCommand before enqueueing
                        command_type = profiling_command.get("command_type", "start")
                        combined_config = profiling_command.get("combined_config", {})
                        is_continuous = combined_config.get("continuous", False)

                        cmd = ProfilingCommand(
                            command_id=command_id,
                            command_type=command_type,
                            profiling_command=profiling_command,
                            is_continuous=is_continuous,
                            timestamp=datetime.datetime.now(),
                            is_paused=False
                        )

                        # Enqueue command regardless of type
                        self.command_manager.enqueue_command(cmd)
                    else:
                        logger.info(f"Command ID {command_id} already received and processed, skipping...")

                # Step 2: Process next command in queue if ready
                next_cmd = self.command_manager.get_next_command()
                if self._should_process_next_command(next_cmd):
                    if next_cmd.command_type == "stop":
                        logger.info(f"Processing STOP command {next_cmd.command_id} from queue")
                        self._stop_current_profiler()
                        # Clear all queues on stop command
                        self.command_manager.clear_queues()
                        # Mark as executed
                        self.heartbeat_client.mark_command_executed(next_cmd.command_id)
                        # Report completion for stop command
                        self.heartbeat_client.send_command_completion(
                            command_id=next_cmd.command_id,
                            status="completed",
                            execution_time=0,
                            error_message=None,
                            results_path=None
                        )
                    elif next_cmd.command_type == "start":
                        # Check if current profiler can be paused
                        if self._can_be_paused():
                            logger.info("Pausing current profiler for queued command %s", next_cmd.command_id)
                            self.command_manager.pause_command(self.current_command.command_id)
                            self._stop_current_profiler()

                        logger.info("Starting profiler for queued command %s", next_cmd.command_id)
                        self._start_new_profiler(next_cmd.profiling_command, next_cmd.command_id)
                        # Mark as executed
                        self.heartbeat_client.mark_command_executed(next_cmd.command_id)
                        # Report command completion to the server
                        try:
                            self.heartbeat_client.send_command_completion(
                                command_id=next_cmd.command_id,
                                status="completed",
                                execution_time=0,
                                error_message=None,
                                results_path=None
                            )
                        except Exception as e:
                            logger.error(f"Failed to report command completion for {next_cmd.command_id}: {e}")
                    else:
                        logger.warning(f"Unknown command type: {next_cmd.command_type}")

                        # Report completion for unknown command type
                        self.heartbeat_client.send_command_completion(
                            command_id=next_cmd.command_id,
                            status="failed",
                            execution_time=0,
                            error_message=f"Unknown command type: {next_cmd.command_type}",
                            results_path=None
                        )

                # Wait for next heartbeat
                self.stop_event.wait(self.heartbeat_interval)

            except Exception as e:
                logger.error(f"Error in heartbeat loop: {e}", exc_info=True)
                self.stop_event.wait(self.heartbeat_interval)

    def _stop_current_profiler(self):
        """Stop the currently running profiler"""
        if self.current_gprofiler:
            logger.info("STOPPING current gProfiler instance...")
            try:
                self.current_gprofiler.stop()  # This sets the stop_event!
                logger.info("Successfully called gprofiler.stop()")
            except Exception as e:
                # TODO: This is a huge leak, report it
                logger.error(f"Error stopping gProfiler: {e}")

            # ALWAYS cleanup subprocesses regardless of stop() success/failure
            try:
                logger.info("Starting comprehensive cleanup after heartbeat stop...")
                # Note: maybe_cleanup_subprocesses() doesn't exist in GProfiler
                # This is a known issue in the dynamic_profiling branch
                if hasattr(self.current_gprofiler, "maybe_cleanup_subprocesses"):
                    self.current_gprofiler.maybe_cleanup_subprocesses()
                logger.info("Comprehensive cleanup completed")
            except Exception as cleanup_error:
                # Cleanup errors are non-fatal - log and continue
                logger.info(f"Cleanup completed with minor errors (expected during stop): {cleanup_error}")

            # Always clear the reference
            self.current_gprofiler = None

        if self.current_thread and self.current_thread.is_alive():
            # No need to actively kill the thread, the self.current_gprofiler.stop() already handles it using events
            logger.info("Waiting for profiler thread to finish...")
            self.current_thread.join(timeout=10)
            self.current_thread = None

    def _start_new_profiler(self, profiling_command: Dict[str, Any], command_id: str):
        """Start a new profiler with the given configuration"""
        try:
            # Import here to avoid circular imports
            from gprofiler.main import DEFAULT_PROFILING_DURATION

            # Create modified args for the new profiler
            new_args = self._create_profiler_args(profiling_command)

            # Create new GProfiler instance
            self.current_gprofiler = self._create_gprofiler_instance(new_args)

            # Track current command
            combined_config = profiling_command.get("combined_config", {})
            is_continuous = combined_config.get("continuous", False)
            self.current_command = ProfilingCommand(
                command_id=command_id,
                command_type="start",
                profiling_command=profiling_command,
                is_continuous=is_continuous,
                timestamp=datetime.datetime.now(),
                is_paused=False
            )
            self.current_command_start_time = datetime.datetime.now()

            # Start profiler in a separate thread
            self.current_thread = threading.Thread(
                target=self._run_profiler,
                args=(
                    self.current_gprofiler,
                    new_args.continuous,
                    getattr(new_args, "duration", DEFAULT_PROFILING_DURATION),
                    command_id,
                ),
                daemon=True
            )
            self.current_thread.start()

            logger.info(f"Started new gProfiler instance with command ID: {command_id} (continuous={is_continuous})")

        except Exception as e:
            logger.error(f"Failed to start new profiler: {e}", exc_info=True)
            # Report failure to the server
            self.heartbeat_client.send_command_completion(
                command_id=command_id,
                status="failed",
                execution_time=0,
                error_message=str(e),
                results_path=None
            )
            # Clear current command tracking on failure
            self.current_command = None
            self.current_command_start_time = None

    def _create_profiler_args(self, profiling_command: Dict[str, Any]) -> configargparse.Namespace:
        """Create modified args based on profiling command"""
        # Copy base args
        new_args = configargparse.Namespace(**vars(self.base_args))

        # Update with profiling command parameters from combined_config
        combined_config = profiling_command.get("combined_config", {})
        if "duration" in combined_config:
            new_args.duration = combined_config["duration"]
        if "frequency" in combined_config:
            new_args.frequency = combined_config["frequency"]
        if "profiling_mode" in combined_config:
            new_args.profiling_mode = combined_config["profiling_mode"]
        if "target_hostnames" in combined_config and combined_config["target_hostnames"]:
            # Only profile if this hostname is in the target list or no specific targets
            if self.heartbeat_client.hostname not in combined_config["target_hostnames"]:
                logger.info(f"Hostname {self.heartbeat_client.hostname} not in target list, skipping profiling")
                return None
        if "pids" in combined_config and combined_config["pids"]:
            new_args.pids_to_profile = combined_config["pids"]

        # Set continuous mode
        new_args.continuous = combined_config.get("continuous", False)

        # Set output directory to temporary storage path
        # This ensures consistent output location for dynamically started profilers
        new_args.output_dir = TEMPORARY_STORAGE_PATH

        # Set flamegraph generation based on continuous mode
        # In continuous mode, disable flamegraph generation to reduce overhead
        # In single-run mode, enable flamegraph for better visualization
        new_args.flamegraph = False if new_args.continuous else True

        # Handle PerfSpect configuration
        enable_perfspect = combined_config.get("enable_perfspect", False)
        if enable_perfspect:
            new_args.collect_hw_metrics = True

            # Assume PerfSpect is pre-installed as a resource
            perfspect_path = resource_path("perfspect/perfspect")

            # Check if PerfSpect binary exists
            if os.path.exists(perfspect_path) and os.access(perfspect_path, os.X_OK):
                new_args.tool_perfspect_path = perfspect_path
                logger.info(f"Using pre-installed PerfSpect at: {perfspect_path}")
            else:
                logger.error(f"PerfSpect not found at {perfspect_path}, hardware metrics disabled")
                new_args.collect_hw_metrics = False

        # Handle max_processes configuration
        max_processes = combined_config.get("max_processes", 10)
        new_args.max_processes_per_profiler = max_processes
        logger.info(f"Setting max processes per profiler: {max_processes}")

        # Handle Profiler Configurations
        profiler_configs = combined_config.get("profiler_configs", {})
        if profiler_configs:
            logger.info(f"Applying profiler configurations: {profiler_configs}")

            # Handle Perf Profiler configuration
            perf_config = profiler_configs.get("perf", "enabled_restricted")
            if perf_config == "enabled_restricted":
                new_args.max_system_processes_for_system_profilers = 600
                new_args.perf_max_docker_containers = 2
                logger.info("Perf profiler: enabled restricted mode")
            elif perf_config == "enabled_aggressive":
                new_args.max_system_processes_for_system_profilers = 1500
                new_args.perf_max_docker_containers = 50
                logger.info("Perf profiler: enabled aggressive mode")
            elif perf_config == "disabled":
                new_args.perf_mode = "disabled"
                logger.info("Perf profiler: disabled")

            # Handle Pyperf configuration
            pyperf_config = profiler_configs.get("pyperf", "enabled")
            if pyperf_config == "enabled":
                new_args.python_skip_pyperf_profiler_above = 1500
                new_args.python_mode = "pyperf"
                logger.info("Pyperf profiler: enabled")
            elif pyperf_config == "disabled":
                new_args.python_mode = "disabled"
                logger.info("Pyperf profiler: disabled, using pyspy")

            # Handle Pyspy configuration
            pyspy_config = profiler_configs.get("pyspy", "enabled_fallback")
            if pyspy_config == "enabled_fallback":
                new_args.python_mode = "auto"
                logger.info("Pyspy profiler: enabled as fallback")
            elif pyspy_config == "enabled":
                new_args.python_mode = "pyspy"
                logger.info("Pyspy profiler: enabled")
            elif pyspy_config == "disabled" and pyperf_config == "disabled":
                new_args.python_mode = "disabled"
                logger.info("Pyspy profiler: disabled")

            # Handle Java Async Profiler configuration
            async_profiler_config = profiler_configs.get("async_profiler", {"enabled": True, "time": "cpu"})

            # Handle both nested object and legacy string formats
            if isinstance(async_profiler_config, dict):
                # New nested object format: {"enabled": true/false, "time": "cpu"/"wall"}
                is_enabled = async_profiler_config.get("enabled", True)
                time_mode = async_profiler_config.get("time", "cpu")

                if not is_enabled:
                    new_args.java_mode = "disabled"
                    logger.info("Java async profiler: disabled")
                else:
                    if time_mode == "wall":
                        new_args.java_async_profiler_mode = "wall"
                        logger.info("Java async profiler: enabled with wall time (mode=wall)")
                    else:  # Default to CPU time
                        new_args.java_async_profiler_mode = "cpu"
                        logger.info("Java async profiler: enabled with CPU time (mode=cpu)")
            else:
                # Legacy string format for backward compatibility
                if async_profiler_config == "disabled":
                    new_args.java_mode = "disabled"
                    logger.info("Java async profiler: disabled (legacy format)")
                elif async_profiler_config == "enabled_wall":
                    new_args.java_async_profiler_mode = "itimer"
                    logger.info("Java async profiler: enabled with wall time (legacy format)")
                else:  # "enabled", "enabled_cpu", or any other value
                    new_args.java_async_profiler_mode = "cpu"
                    logger.info("Java async profiler: enabled with CPU time (legacy format)")

            # Handle PHP configuration
            phpspy_config = profiler_configs.get("phpspy", "enabled")
            if phpspy_config == "disabled":
                new_args.php_mode = "disabled"
                logger.info("PHP profiler: disabled")
            else:
                logger.info("PHP profiler: enabled")

            # Handle Ruby configuration
            rbspy_config = profiler_configs.get("rbspy", "enabled")
            if rbspy_config == "disabled":
                new_args.ruby_mode = "disabled"
                logger.info("Ruby profiler: disabled")
            else:
                logger.info("Ruby profiler: enabled")

            # Handle .NET configuration
            dotnet_config = profiler_configs.get("dotnet_trace", "enabled")
            if dotnet_config == "disabled":
                new_args.dotnet_mode = "disabled"
                logger.info(".NET profiler: disabled")
            else:
                logger.info(".NET profiler: enabled")

            # Handle NodeJS configuration
            nodejs_config = profiler_configs.get("nodejs_perf", "enabled")
            if nodejs_config == "disabled":
                new_args.nodejs_mode = "none"
                logger.info("NodeJS profiler: disabled")
            else:
                logger.info("NodeJS profiler: enabled")

        return new_args

    def _create_gprofiler_instance(self, args: configargparse.Namespace) -> 'GProfiler':
        """Create a new GProfiler instance with the given args"""
        if args is None:
            return None

        # Import here to avoid circular imports
        from gprofiler.main import GProfiler, pids_to_processes

        processes_to_profile = pids_to_processes(args)
        state = get_state()

        # Create profiler API client
        profiler_api_client = None
        if args.upload_results:
            profiler_api_client = ProfilerAPIClient(
                token=args.server_token,
                service_name=args.service_name,
                server_address=args.server_host,
                curlify_requests=getattr(args, 'curlify_requests', False),
                hostname=get_hostname(),
                verify=args.verify,
                upload_timeout=getattr(args, 'server-upload-timeout', 120)  # Default to 120 seconds
            )

        enrichment_options = EnrichmentOptions(
            profile_api_version=args.profile_api_version,
            container_names=args.container_names,
            application_identifiers=args.collect_appids,
            application_identifier_args_filters=args.app_id_args_filters,
            application_metadata=args.application_metadata,
        )

        # Create external metadata path if specified
        external_metadata_path = None
        if hasattr(args, 'external_metadata') and args.external_metadata:
            external_metadata_path = Path(args.external_metadata)

        # Create heartbeat file path if specified
        heartbeat_file_path = None
        if hasattr(args, 'heartbeat_file') and args.heartbeat_file:
            heartbeat_file_path = Path(args.heartbeat_file)

        # Create perfspect path if specified
        perfspect_path = None
        if hasattr(args, "tool_perfspect_path") and args.tool_perfspect_path:
            perfspect_path = Path(args.tool_perfspect_path)

        return GProfiler(
            output_dir=args.output_dir,
            flamegraph=args.flamegraph,
            rotating_output=getattr(args, 'rotating_output', False),
            rootless=getattr(args, 'rootless', False),
            profiler_api_client=profiler_api_client,
            collect_metrics=getattr(args, 'collect_metrics', True),
            collect_metadata=getattr(args, 'collect_metadata', True),
            enrichment_options=enrichment_options,
            state=state,
            usage_logger=NoopUsageLogger(),  # Simplified for dynamic profiling
            user_args=args.__dict__,
            duration=args.duration,
            profile_api_version=args.profile_api_version,
            profiling_mode=args.profiling_mode,
            collect_hw_metrics=getattr(args, "collect_hw_metrics", False),
            profile_spawned_processes=getattr(args, 'profile_spawned_processes', False),
            remote_logs_handler=None,  # Simplified for dynamic profiling
            controller_process=None,
            processes_to_profile=processes_to_profile,
            external_metadata_path=external_metadata_path,
            heartbeat_file_path=heartbeat_file_path,
            perfspect_path=perfspect_path,
            perfspect_duration=getattr(args, "tool_perfspect_duration", 60),
        )

    def _run_profiler(self, gprofiler: 'GProfiler', continuous: bool, duration: int, command_id: str):
        """Run the profiler with specified args"""
        if gprofiler is None:
            return

        start_time = datetime.datetime.now()

        try:
            if continuous:
                logger.info(f"Running continuous profiler for command ID: {command_id}")
                gprofiler.run_continuous()
            else:
                logger.info(f"Running profiler for {duration} seconds (command ID: {command_id})...")
                gprofiler.run_single()

            # After run completes, check if it was stopped or completed
            if gprofiler._profiler_state.stop_event.is_set():
                logger.info(f"Profiler run was stopped before completion for command ID: {command_id}")
            else:
                logger.info(f"Profiler run completed successfully for command ID: {command_id}")

            # Try to get results path if available
            if hasattr(gprofiler, "output_dir") and gprofiler.output_dir:
                _ = str(gprofiler.output_dir)  # Available for future use

        except Exception as e:
            # Internal exceptions can occur during profiling stop
            # Only consider a failure if it was not due to a stop event
            if not gprofiler._profiler_state.stop_event.is_set():
                logger.error(f"Profiler run failed for command ID {command_id}: {e}", exc_info=True)
            else:
                logger.info(f"Profiler run was stopped before completion for command ID: {command_id}")

        finally:
            # Calculate execution time
            end_time = datetime.datetime.now()

            # Dequeue the command now that profiler has finished
            self.command_manager.dequeue_command(command_id)

            # Clear the current profiler reference and command tracking
            if self.current_gprofiler == gprofiler:
                self.current_gprofiler = None
                self.current_command = None
                self.current_command_start_time = None

                # After profiler completes, check if there are more commands in queue
                if self.command_manager.has_queued_commands() and not self.stop_event.is_set():
                    logger.info("Profiler completed, checking for next queued command...")
                    # The heartbeat loop will pick up the next command

    def _can_be_paused(self) -> bool:
        """Check if the current profiler can be paused for queued commands"""
        return self.current_command is not None and self.current_command.is_continuous

    def _ready_for_next_command(self) -> bool:
        """Check if ready to execute the next command"""
        return self.current_gprofiler is None or self._can_be_paused()

    def _should_process_next_command(self, cmd: ProfilingCommand) -> bool:
        """Determine if the next command should be processed based on current state"""
        # If command is None, cannot process
        if cmd is None:
            return False

        # If current command corresponds to the same command ID, do not re-process
        if self.current_command and self.current_command.command_id == cmd.command_id:
            return False

        return self._ready_for_next_command()

    def stop(self):
        """Stop the heartbeat manager"""
        logger.info("Stopping heartbeat manager...")
        self.stop_event.set()
        self._stop_current_profiler()
        self.command_manager.clear_queues()
        logger.info("Heartbeat manager stopped")
