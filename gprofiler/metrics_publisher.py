"""
Clean, simple metrics publisher for gProfiler error reporting.

Sends error metrics to Pinterest's MetricAgent (Goku) in the standard format:
`put metric.name epoch value tag=value tag=value`
"""

import socket
import time
import logging
import platform
import threading
from typing import Dict, Any, Optional

# Import with fallbacks for better compatibility
try:
    from gprofiler.metadata.system_metadata import get_hostname_or_none as _get_hostname_or_none
    def get_hostname_or_none():
        try:
            return _get_hostname_or_none()
        except Exception:
            import socket
            try:
                return socket.gethostname()
            except Exception:
                return None
except ImportError:
    def get_hostname_or_none():
        import socket
        try:
            return socket.gethostname()
        except Exception:
            return None

try:
    from gprofiler.state import get_state
except ImportError:
    def get_state():
        return None

# Metric configuration
METRIC_BASE_NAME = "gprofiler"
METRIC_VALUE = 1  # Counter increment

# Error type constants
ERROR_TYPE_PROCESS_PROFILER_FAILURE = "process_profiler_failure"
ERROR_TYPE_PERF_FAILURE = "perf_failure" 
ERROR_TYPE_PROFILING_RUN_FAILURE = "profiling_run_failure"
ERROR_TYPE_UPLOAD_ERROR = "upload_error"  # Consolidated for all upload-related errors

# Component constants
COMPONENT_SYSTEM_PROFILER = "system_profiler"
COMPONENT_API_CLIENT = "api_client"
COMPONENT_GPROFILER_MAIN = "gprofiler_main"

# Severity constants
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"  
SEVERITY_CRITICAL = "critical"

# Message constants
ERROR_MSG_PROCESS_PROFILER_FAILURE = "process profiler crashed or failed"
ERROR_MSG_PERF_FAILURE = "perf command failed"
ERROR_MSG_PROFILING_RUN_FAILURE = "profiling cycle failed"
ERROR_MSG_UPLOAD_ERROR = "profile upload failed"

# Error category constants for granular upload error classification
ERROR_CATEGORY_UPLOAD_TIMEOUT = "upload_timeout"
ERROR_CATEGORY_UPLOAD_API_ERROR = "upload_api_error"
ERROR_CATEGORY_UPLOAD_REQUEST_EXCEPTION = "upload_request_exception"

# Error budget metric constants (for CustomSR formula)
ERROR_BUDGET_METRIC_NAME = "error-budget.counters"
ERROR_BUDGET_UUID = "b8200070-42b8-46c8-8725-b68989952131"  # UUID for error-budget metrics
RESPONSE_TYPE_SUCCESS = "success"
RESPONSE_TYPE_FAILURE = "failure"
RESPONSE_TYPE_IGNORED_FAILURE = "ignored_failure"

# Export all constants for external use
__all__ = [
    "MetricsPublisher", "NoopMetricsPublisher", "get_current_method_name",
    "METRIC_BASE_NAME", "ERROR_TYPE_PROCESS_PROFILER_FAILURE", "ERROR_TYPE_PERF_FAILURE",
    "ERROR_TYPE_PROFILING_RUN_FAILURE", "ERROR_TYPE_UPLOAD_ERROR",
    "COMPONENT_SYSTEM_PROFILER", "COMPONENT_API_CLIENT", "COMPONENT_GPROFILER_MAIN",
    "SEVERITY_ERROR", "SEVERITY_WARNING", "SEVERITY_CRITICAL",
    "ERROR_MSG_PROCESS_PROFILER_FAILURE", "ERROR_MSG_PERF_FAILURE", "ERROR_MSG_PROFILING_RUN_FAILURE",
    "ERROR_MSG_UPLOAD_ERROR",
    "ERROR_CATEGORY_UPLOAD_TIMEOUT", "ERROR_CATEGORY_UPLOAD_API_ERROR", "ERROR_CATEGORY_UPLOAD_REQUEST_EXCEPTION",
    "ERROR_BUDGET_METRIC_NAME", "ERROR_BUDGET_UUID", "RESPONSE_TYPE_SUCCESS", "RESPONSE_TYPE_FAILURE", "RESPONSE_TYPE_IGNORED_FAILURE"
]


def get_current_method_name() -> str:
    """Get the name of the calling method for better error context."""
    import inspect
    
    try:
        current_frame = inspect.currentframe()
        if current_frame is None:
            return "unknown_method"
            
        caller_frame = current_frame.f_back.f_back if current_frame.f_back else None
        if caller_frame is None:
            return "unknown_method"
            
        method_name = caller_frame.f_code.co_name
        
        if "self" in caller_frame.f_locals:
            class_name = caller_frame.f_locals["self"].__class__.__name__
            return f"{class_name}.{method_name}"
            
        return method_name
        
    except Exception:
        return "unknown_method"
    finally:
        del current_frame


class MetricsPublisher:
    """
    Singleton metrics publisher for sending error metrics to MetricAgent.
    
    Ensures only one TCP connection and consistent configuration across
    the entire gProfiler process for maximum resource efficiency.
    """
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls, server_url: str = None, service_name: str = None, sli_metric_uuid: str = None, enabled: bool = True):
        """
        Singleton pattern - ensure only one instance exists.
        
        Args:
            server_url: MetricAgent URL (only used on first instantiation)
            service_name: Service name for tagging (only used on first instantiation)
            sli_metric_uuid: UUID for SLI metrics (only used on first instantiation)
            enabled: Whether metrics publishing is enabled (only used on first instantiation)
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, server_url: str = None, service_name: str = None, sli_metric_uuid: str = None, enabled: bool = True):
        """
        Initialize metrics handler (only once due to singleton pattern).
        
        Args:
            server_url: MetricAgent URL (e.g., 'tcp://localhost:18126') - required if enabled=True
            service_name: Service name for tagging - required if enabled=True
            sli_metric_uuid: UUID for SLI metrics (optional, if not provided SLI metrics are disabled)
            enabled: Whether metrics publishing is enabled (if False, all send methods return early)
        """
        # Only initialize once
        if self._initialized:
            return
        
        self.enabled = enabled  # Controls whether metrics are actually sent
        
        # If metrics are disabled, we don't need valid server_url/service_name
        if enabled:
            if server_url is None or service_name is None:
                raise ValueError("server_url and service_name are required when enabled=True")
        else:
            # Provide defaults when disabled (won't be used anyway)
            server_url = server_url or "tcp://localhost:18126"
            service_name = service_name or "gprofiler"
            
        self.server_url = server_url
        self.service_name = service_name
        self.sli_metric_uuid = sli_metric_uuid  # Can be None - SLI metrics disabled if not set
        self.logger = logging.getLogger(f"{__name__}.MetricsPublisher")
        
        # Parse server URL (only matters if enabled)
        if server_url.startswith('tcp://'):
            url_parts = server_url[6:].split(':')
            self.host = url_parts[0]
            self.port = int(url_parts[1]) if len(url_parts) > 1 else 18126
        else:
            # If disabled, don't raise error for invalid URL
            if enabled:
                raise ValueError(f"Unsupported server URL format: {server_url}")
            else:
                self.host = "localhost"
                self.port = 18126
            
        self._initialized = True
        status = "enabled" if enabled else "disabled"
        self.logger.info(f"MetricsPublisher singleton initialized: {server_url} for service '{service_name}' (metrics {status})")
    
    @classmethod
    def get_instance(cls) -> Optional['MetricsPublisher']:
        """
        Get the singleton instance if it exists.
        
        Returns:
            MetricsPublisher instance if initialized, None otherwise
        """
        return cls._instance
    
    @classmethod
    def is_initialized(cls) -> bool:
        """
        Check if the singleton has been initialized.
        
        Returns:
            True if singleton is initialized, False otherwise
        """
        return cls._instance is not None and cls._instance._initialized
            
    def send_error_metric(
        self,
        error_type: str,
        error_message: str,
        category: str,
        severity: str = SEVERITY_ERROR,
        extra_tags: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Send error metric to MetricAgent with decorated name and enriched tags.
        
        Args:
            error_type: Type of error (e.g., ERROR_TYPE_PROCESS_PROFILER_FAILURE)
            error_message: Human-readable error description
            category: Error category/source (e.g., COMPONENT_API_CLIENT, COMPONENT_SYSTEM_PROFILER)
            severity: Error severity level (e.g., SEVERITY_ERROR)
            extra_tags: Additional tags to include
        """
        # Guard: Skip if metrics publishing is disabled
        if not self.enabled:
            return
        
        try:
            metric_name = self.decorate_metric_name(category, error_type)
            tags = self.build_enriched_tags(severity, category, extra_tags or {})
            message = self.format_metric_message(metric_name, tags)
            self.send_metric(message)
            self.logger.debug(f"Sent: {metric_name}")
        except Exception as e:
            self.logger.warning(f"Metric send failed '{error_type}': {e}")

    def decorate_metric_name(self, category: str, error_type: str) -> str:
        """Decorate metric with hierarchical naming: gprofiler.category.error_type.error"""
        return f"{METRIC_BASE_NAME}.{category}.{error_type}.error"

    def build_enriched_tags(self, severity: str, category: str, user_tags: Dict[str, Any]) -> Dict[str, str]:
        """Build enriched tags with system context + user tags."""
        current_hostname = get_hostname_or_none() or "unknown"
        
        tags = {
            "service": self.service_name,
            "hostname": current_hostname,
            "component": category,
            "severity": severity,
            "metric_type": "counter",
            "os_type": platform.system().lower(),
            "python_version": f"{platform.python_version_tuple()[0]}.{platform.python_version_tuple()[1]}",
        }
        
        # Add gProfiler runtime context
        self._add_runtime_context(tags)
        
        # Add user tags (stringify all values)
        tags.update({k: str(v) for k, v in user_tags.items()})
        return tags

    def format_metric_message(self, metric_name: str, tags: Dict[str, str]) -> str:
        """Format metric in Goku protocol: put metric.name epoch value tag=value tag=value"""
        epoch = int(time.time())
        tag_string = " ".join(f"{k}={v}" for k, v in tags.items())
        return f"put {metric_name} {epoch} {METRIC_VALUE} {tag_string}"

    def send_metric(self, message: str) -> None:
        """Send formatted message to MetricAgent via TCP."""
        with socket.create_connection((self.host, self.port), timeout=5.0) as sock:
            sock.sendall(message.encode('utf-8') + b'\n')
    
    def send_sli_metric(
        self,
        response_type: str,
        method_name: str,
        value: int = 1,
        extra_tags: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Send SLI (Service Level Indicator) metric for error-budget tracking via CustomSR formula.
        
        Requirements:
            1. Metrics must be enabled (--enable-publish-metrics)
            2. SLI metric UUID must be configured (--sli-metric-uuid)
        
        If either requirement is not met, this method silently returns (SLI metrics disabled).
        
        Metric format:
            error-budget.counters.<UUID>{response_type=<type>, method_name=<name>}
        
        Args:
            response_type: Response type - 'success', 'failure', or 'ignored_failure'
                          Use RESPONSE_TYPE_* constants
            method_name: Name of the method being tracked (e.g., 'send_heartbeat')
            value: Metric value (default: 1 for counter increment)
            extra_tags: Additional tags to include (optional)
        
        Example:
            send_sli_metric(
                response_type=RESPONSE_TYPE_SUCCESS,
                method_name='send_heartbeat'
            )
        """
        # Guard: Skip if metrics publishing is disabled
        if not self.enabled:
            return
        
        # Check if SLI metric UUID is configured
        if not self.sli_metric_uuid:
            # SLI metrics disabled - UUID not configured
            return
        
        try:
            # Build tags - response_type and method_name are REQUIRED
            tags = {
                "response_type": response_type,
                "method_name": method_name,
                "metric_type": "counter",
                "service": self.service_name,
                "hostname": get_hostname_or_none() or "unknown",
            }
            
            # Add extra tags if provided
            if extra_tags:
                tags.update({k: str(v) for k, v in extra_tags.items()})
            
            # Build metric name with UUID suffix (configurable per environment)
            metric_name = f"{ERROR_BUDGET_METRIC_NAME}.{self.sli_metric_uuid}"
            
            # Format message in Goku protocol
            epoch = int(time.time())
            tag_string = " ".join(f"{k}={v}" for k, v in tags.items())
            message = f"put {metric_name} {epoch} {value} {tag_string}"
            
            # Send metric
            self.send_metric(message)
            self.logger.debug(f"Sent SLI metric (error-budget): {response_type}/{method_name}")
        except Exception as e:
            self.logger.warning(f"SLI metric send failed: {e}")

    def _add_runtime_context(self, tags: Dict[str, str]) -> None:
        """
        Add gProfiler runtime context to tags for tracking and correlation.
        
        Tags added:
        - run_id: Unique identifier for this gProfiler agent instance (persists across cycles)
        - cycle_id: Unique identifier for the current profiling cycle (changes each cycle)
        - run_mode: Deployment context (k8s/container/standalone_executable/local_python)
        
        These tags enable:
        - Correlating metrics across profiling cycles from the same agent instance
        - Tracking agent lifecycle and troubleshooting agent-specific issues
        - Understanding deployment patterns and environment-specific error rates
        """
        try:
            state = get_state()
            if state:
                # run_id: Identifies this agent instance across its entire lifetime
                tags["run_id"] = state.run_id
                # cycle_id: Identifies the specific profiling cycle when error occurred
                tags["cycle_id"] = str(state.cycle_id) if state.cycle_id else "none"
        except Exception:
            pass  # Continue without runtime context


class NoopMetricsPublisher:
    """No-op metrics publisher when metrics are disabled."""
    
    def send_error_metric(self, error_type: str, error_message: str, category: str, 
                         severity: str = "error", extra_tags: Optional[Dict[str, Any]] = None) -> None:
        """Do nothing - metrics are disabled."""
        pass
    
    def send_sli_metric(self, response_type: str, method_name: str, 
                       value: int = 1, extra_tags: Optional[Dict[str, Any]] = None) -> None:
        """Do nothing - SLI metrics are disabled."""
        pass