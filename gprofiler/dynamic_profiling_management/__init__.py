import datetime
import logging
import os
import threading
from pathlib import Path
from typing import Dict, Any, Optional, TYPE_CHECKING

import configargparse

if TYPE_CHECKING:
    from gprofiler.main import GProfiler

from gprofiler.client import ProfilerAPIClient
from gprofiler.dynamic_profiling_management.command_control import CommandManager, ProfilingCommand
from gprofiler.metadata.enrichment import EnrichmentOptions
from gprofiler.metadata.system_metadata import get_hostname
from gprofiler.profilers.perf_events import validate_and_normalize_events
from gprofiler.state import get_state
from gprofiler.usage_loggers import NoopUsageLogger
from gprofiler.utils import resource_path

logger = logging.getLogger(__name__)

PROFILER_TYPE_MAP = {
    "perf": "perf",
    "async_profiler": "java",
    "pyperf": "python",
    "pyspy": "python",
    "phpspy": "php",
    "rbspy": "ruby",
    "dotnet_trace": "dotnet",
    "nodejs_perf": "nodejs",
}

ALL_PROFILER_TYPES = {"perf", "java", "python", "php", "ruby", "dotnet", "nodejs"}


def get_enabled_profiler_types(profiling_command: Dict[str, Any]) -> set:
    """Extract the set of enabled profiler type names from a profiling command.

    Maps profiler_configs keys to canonical type names. If no profiler_configs
    are specified, assumes all profiler types are enabled (default behavior).
    """
    combined_config = profiling_command.get("combined_config", {})
    profiler_configs = combined_config.get("profiler_configs", {})

    if not profiler_configs:
        return set(ALL_PROFILER_TYPES)

    enabled = set()
    for config_key, canonical_name in PROFILER_TYPE_MAP.items():
        config_value = profiler_configs.get(config_key)
        if config_value is None:
            enabled.add(canonical_name)
            continue

        if isinstance(config_value, dict):
            if config_value.get("enabled") is False or config_value.get("mode") == "disabled":
                continue
        elif config_value == "disabled":
            continue

        enabled.add(canonical_name)

    return enabled


def create_profiler_args(
    base_args: configargparse.Namespace,
    profiling_command: Dict[str, Any],
    hostname: str,
) -> Optional[configargparse.Namespace]:
    """Translate a heartbeat profiling command into a GProfiler args namespace."""
    new_args = configargparse.Namespace(**vars(base_args))

    combined_config = profiling_command.get("combined_config", {})
    if "duration" in combined_config:
        new_args.duration = combined_config["duration"]
    if "frequency" in combined_config:
        new_args.frequency = combined_config["frequency"]
    if "profiling_mode" in combined_config:
        new_args.profiling_mode = combined_config["profiling_mode"]
    if "target_hostnames" in combined_config and combined_config["target_hostnames"]:
        if hostname not in combined_config["target_hostnames"]:
            logger.info(f"Hostname {hostname} not in target list, skipping profiling")
            return None
    if "pids" in combined_config and combined_config["pids"]:
        new_args.pids_to_profile = combined_config["pids"]

    new_args.continuous = combined_config.get("continuous", False)
    new_args.flamegraph = not new_args.continuous

    # PerfSpect
    if combined_config.get("enable_perfspect", False):
        new_args.collect_hw_metrics = True
        perfspect_path = resource_path("perfspect/perfspect")
        if os.path.exists(perfspect_path) and os.access(perfspect_path, os.X_OK):
            new_args.tool_perfspect_path = perfspect_path
            logger.info(f"Using pre-installed PerfSpect at: {perfspect_path}")
        else:
            logger.error(f"PerfSpect not found at {perfspect_path}, hardware metrics disabled")
            new_args.collect_hw_metrics = False

    max_processes = combined_config.get("max_processes", 10)
    new_args.max_processes_per_profiler = max_processes

    profiler_configs = combined_config.get("profiler_configs", {})
    if profiler_configs:
        _apply_profiler_configs(new_args, profiler_configs)

    return new_args


def _apply_profiler_configs(new_args: configargparse.Namespace, profiler_configs: dict) -> None:
    """Apply individual profiler enable/disable/mode settings to args."""
    logger.info(f"Applying profiler configurations: {profiler_configs}")

    # --- Perf ---
    perf_config = profiler_configs.get("perf", "enabled_restricted")
    if isinstance(perf_config, dict):
        perf_mode = perf_config.get("mode", "enabled_restricted")
        perf_events = perf_config.get("events", ["cycles"])
        if isinstance(perf_events, str):
            perf_events = [perf_events]
        elif not isinstance(perf_events, list):
            perf_events = ["cycles"]
        perf_events = validate_and_normalize_events(perf_events)

        if perf_mode == "enabled_restricted":
            new_args.max_system_processes_for_system_profilers = 600
            new_args.perf_max_docker_containers = 2
        elif perf_mode == "enabled_aggressive":
            new_args.max_system_processes_for_system_profilers = 1500
            new_args.perf_max_docker_containers = 50
        elif perf_mode == "disabled":
            new_args.perf_mode = "disabled"
        new_args.perf_events = ",".join(perf_events)
    else:
        if perf_config == "enabled_restricted":
            new_args.max_system_processes_for_system_profilers = 600
            new_args.perf_max_docker_containers = 2
        elif perf_config == "enabled_aggressive":
            new_args.max_system_processes_for_system_profilers = 1500
            new_args.perf_max_docker_containers = 50
        elif perf_config == "disabled":
            new_args.perf_mode = "disabled"
        new_args.perf_events = "cycles"

    # --- Python ---
    pyperf_config = profiler_configs.get("pyperf", "enabled")
    if pyperf_config == "enabled":
        new_args.python_skip_pyperf_profiler_above = 1500
        new_args.python_mode = "pyperf"
    elif pyperf_config == "disabled":
        new_args.python_mode = "disabled"

    pyspy_config = profiler_configs.get("pyspy", "enabled_fallback")
    if pyspy_config == "enabled_fallback":
        new_args.python_mode = "auto"
    elif pyspy_config == "enabled":
        new_args.python_mode = "pyspy"
    elif pyspy_config == "disabled" and pyperf_config == "disabled":
        new_args.python_mode = "disabled"

    # --- Java ---
    async_profiler_config = profiler_configs.get("async_profiler", {"enabled": True, "time": "cpu"})
    if isinstance(async_profiler_config, dict):
        if not async_profiler_config.get("enabled", True):
            new_args.java_mode = "disabled"
        else:
            new_args.java_async_profiler_mode = "wall" if async_profiler_config.get("time") == "wall" else "cpu"
    else:
        if async_profiler_config == "disabled":
            new_args.java_mode = "disabled"
        elif async_profiler_config == "enabled_wall":
            new_args.java_async_profiler_mode = "itimer"
        else:
            new_args.java_async_profiler_mode = "cpu"

    # --- PHP / Ruby / .NET / Node ---
    if profiler_configs.get("phpspy") == "disabled":
        new_args.php_mode = "disabled"
    if profiler_configs.get("rbspy") == "disabled":
        new_args.ruby_mode = "disabled"
    if profiler_configs.get("dotnet_trace") == "disabled":
        new_args.dotnet_mode = "disabled"
    if profiler_configs.get("nodejs_perf") == "disabled":
        new_args.nodejs_mode = "none"


def create_gprofiler_instance(args: configargparse.Namespace) -> Optional["GProfiler"]:
    """Create a new GProfiler instance from an args namespace."""
    if args is None:
        return None

    from gprofiler.main import GProfiler, pids_to_processes

    processes_to_profile = pids_to_processes(args)
    state = get_state()

    profiler_api_client = None
    if args.upload_results:
        profiler_api_client = ProfilerAPIClient(
            token=args.server_token,
            service_name=args.service_name,
            server_address=args.server_host,
            curlify_requests=getattr(args, "curlify_requests", False),
            hostname=get_hostname(),
            verify=args.verify,
            upload_timeout=getattr(args, "server-upload-timeout", 120),
            tls_client_cert=getattr(args, "tls_client_cert", None),
            tls_client_key=getattr(args, "tls_client_key", None),
            tls_ca_bundle=getattr(args, "tls_ca_bundle", None),
            tls_cert_refresh_enabled=getattr(args, "tls_cert_refresh_enabled", False),
            tls_cert_refresh_interval=getattr(args, "tls_cert_refresh_interval", 21600),
        )

    enrichment_options = EnrichmentOptions(
        profile_api_version=args.profile_api_version,
        container_names=args.container_names,
        application_identifiers=args.collect_appids,
        application_identifier_args_filters=args.app_id_args_filters,
        application_metadata=args.application_metadata,
    )

    external_metadata_path = None
    if hasattr(args, "external_metadata") and args.external_metadata:
        external_metadata_path = Path(args.external_metadata)

    heartbeat_file_path = None
    if hasattr(args, "heartbeat_file") and args.heartbeat_file:
        heartbeat_file_path = Path(args.heartbeat_file)

    perfspect_path = None
    if hasattr(args, "tool_perfspect_path") and args.tool_perfspect_path:
        perfspect_path = Path(args.tool_perfspect_path)

    return GProfiler(
        output_dir=getattr(args, "output_dir", None),
        flamegraph=args.flamegraph,
        rotating_output=getattr(args, "rotating_output", False),
        rootless=getattr(args, "rootless", False),
        profiler_api_client=profiler_api_client,
        collect_metrics=getattr(args, "collect_metrics", True),
        collect_metadata=getattr(args, "collect_metadata", True),
        enrichment_options=enrichment_options,
        state=state,
        usage_logger=NoopUsageLogger(),
        user_args=args.__dict__,
        duration=args.duration,
        profile_api_version=args.profile_api_version,
        profiling_mode=args.profiling_mode,
        collect_hw_metrics=getattr(args, "collect_hw_metrics", False),
        profile_spawned_processes=getattr(args, "profile_spawned_processes", False),
        remote_logs_handler=None,
        controller_process=None,
        processes_to_profile=processes_to_profile,
        external_metadata_path=external_metadata_path,
        heartbeat_file_path=heartbeat_file_path,
        perfspect_path=perfspect_path,
        perfspect_duration=getattr(args, "tool_perfspect_duration", 60),
    )


class ProfilerSlotBase:
    """Base class for profiler execution slots (continuous and ad-hoc).

    Each slot manages one GProfiler instance, its execution thread,
    the associated command metadata, and the set of enabled profiler types.
    """

    SLOT_NAME = "base"

    def __init__(
        self,
        base_args: configargparse.Namespace,
        heartbeat_client,
        command_manager: "CommandManager",
        stop_event: threading.Event,
    ):
        self._base_args = base_args
        self._heartbeat_client = heartbeat_client
        self._command_manager = command_manager
        self._stop_event = stop_event

        self.gprofiler: Optional["GProfiler"] = None
        self.thread: Optional[threading.Thread] = None
        self.command: Optional["ProfilingCommand"] = None
        self.profiler_types: set = set()

    def is_running(self) -> bool:
        return self.gprofiler is not None

    def is_running_command(self, command_id: str) -> bool:
        return self.command is not None and self.command.command_id == command_id

    def stop(self) -> None:
        """Stop the profiler in this slot, join the thread, and clear state."""
        if self.gprofiler:
            logger.info(f"Stopping {self.SLOT_NAME} profiler...")
            try:
                self.gprofiler.stop()
            except Exception as e:
                logger.error(f"Error stopping {self.SLOT_NAME} profiler: {e}")
            try:
                self.gprofiler.maybe_cleanup_subprocesses()
            except Exception as e:
                logger.info(f"{self.SLOT_NAME} cleanup completed with minor errors: {e}")
            self.gprofiler = None

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10)
            self.thread = None

        self._clear_state()

    def _clear_state(self) -> None:
        """Clear slot state. Override in subclasses for extra fields."""
        self.command = None
        self.profiler_types = set()

    # ------------------------------------------------------------------
    # Shared start / run helpers
    # ------------------------------------------------------------------

    def _start_profiler(self, profiling_command: Dict[str, Any], command_id: str, continuous: bool) -> None:
        """Create a GProfiler instance and run it in a daemon thread."""
        from gprofiler.main import DEFAULT_PROFILING_DURATION

        new_args = create_profiler_args(self._base_args, profiling_command, self._heartbeat_client.hostname)
        self.gprofiler = create_gprofiler_instance(new_args)

        self.command = ProfilingCommand(
            command_id=command_id,
            command_type="start",
            profiling_command=profiling_command,
            is_continuous=continuous,
            timestamp=datetime.datetime.now(),
            is_paused=False,
        )
        self.profiler_types = get_enabled_profiler_types(profiling_command)

        self.thread = threading.Thread(
            target=self._run_profiler,
            args=(
                self.gprofiler,
                continuous,
                getattr(new_args, "duration", DEFAULT_PROFILING_DURATION),
                command_id,
            ),
            daemon=True,
        )
        self.thread.start()
        logger.info(f"Started {self.SLOT_NAME} profiler with command ID: {command_id} (continuous={continuous})")

    def _run_profiler(self, gprofiler: "GProfiler", continuous: bool, duration: int, command_id: str) -> None:
        """Thread target: run the profiler until completion or stop."""
        if gprofiler is None:
            return

        try:
            if continuous:
                gprofiler.run_continuous()
            else:
                gprofiler.run_single()

            if gprofiler._profiler_state.stop_event.is_set():
                logger.info(f"Profiler stopped for command ID: {command_id}")
            else:
                logger.info(f"Profiler completed for command ID: {command_id}")
        except Exception as e:
            if not gprofiler._profiler_state.stop_event.is_set():
                logger.error(f"Profiler failed for command ID {command_id}: {e}", exc_info=True)
        finally:
            self._command_manager.dequeue_command(command_id)
            if self.gprofiler == gprofiler:
                self.gprofiler = None
                self.thread = None
                self._clear_state()
                self._on_complete(command_id)

    def _on_complete(self, command_id: str) -> None:
        """Hook called after the profiler finishes. Override in subclasses."""
        pass
