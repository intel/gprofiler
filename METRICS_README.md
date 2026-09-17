# gProfiler Metrics Implementation

## Overview

gProfiler metrics system provides comprehensive error monitoring and observability by sending structured metrics to Pinterest's MetricAgent. 

## Architecture

### Core Components

```
MetricsHandler (Singleton)
├── decorate_metric_name()     # Hierarchical naming
├── build_enriched_tags()      # System + user tags  
├── format_metric_message()    # Goku protocol formatting
└── send_metric()              # TCP transmission
```

### Design Principles

- **Single Responsibility**: Each method has one clear purpose
- **Resource Efficiency**: Singleton pattern ensures one TCP connection
- **Clean API**: Simple, intuitive method names
- **Testability**: Pure functions with clear inputs/outputs
- **Robustness**: Never crashes main application

## Error Types & Purpose

### 🔍 **Error Segregation Strategy**

| Error Type | Purpose | When Used |
|------------|---------|-----------|
| `process_profiler_failure` | **Individual process profiler crashes** | Java/Python/Native profiler dies |
| `perf_failure` | **System perf command failures** | `perf record` command fails |
| `profiling_run_failure` | **Entire profiling cycle crashes** | Complete profiling session fails |
| `upload_error` | **Profile upload errors** | Network/server upload errors |
| `api_error` | **HTTP 4xx/5xx from API server** | Server returns error status |
| `request_exception` | **Network connection failures** | TCP/DNS/connection errors |

**Why segregate?** Different error types require different:
- **Operational responses** (restart profiler vs fix network)  
- **Alert routing** (infra team vs app team)
- **SLA tracking** (profiler reliability vs upload reliability)

## Usage

### Basic Usage

```python
from gprofiler.metrics_publisher import (
    MetricsHandler, 
    ERROR_TYPE_PROCESS_PROFILER_FAILURE,
    COMPONENT_SYSTEM_PROFILER,
    SEVERITY_ERROR,
    get_current_method_name
)

# Singleton - same instance everywhere
handler = MetricsHandler('tcp://localhost:18126', 'gprofiler')

# Send error metric
handler.send_error_metric(
    error_type=ERROR_TYPE_PROCESS_PROFILER_FAILURE,
    error_message="Java profiler crashed during heap analysis", 
    category=COMPONENT_SYSTEM_PROFILER,
    severity=SEVERITY_ERROR,
    extra_tags={
        'method_name': get_current_method_name(),
        'profiler_name': 'java',
        'failure_reason': 'out_of_memory'
    }
)
```

### Configuration

```python
# CLI Arguments
parser.add_argument('--enable-publish-metrics', action='store_true')
parser.add_argument('--metrics-server-url', default='tcp://localhost:18126')
parser.add_argument('--service-name', default='gprofiler')

# Initialization
if args.enable_publish_metrics:
    handler = MetricsHandler(args.metrics_server_url, args.service_name)
else:
    handler = NoopMetricsHandler()  # Safe no-op when disabled
```

## Metric Format

### Hierarchical Naming

```
gprofiler.{category}.{error_type}.error
```

**Examples:**
- `gprofiler.system_profiler.process_profiler_failure.error`
- `gprofiler.api_client.upload_error.error`
- `gprofiler.gprofiler_main.profiling_run_failure.error`

### Tags (Metadata)

**System Tags (automatic):**
```json
{
  "service": "gprofiler",
  "hostname": "prod-server-01", 
  "component": "system_profiler",
  "severity": "error",
  "os_type": "linux",
  "python_version": "3.8"
}
```

**Runtime Tags (when gProfiler is active):**
```json
{
  "run_id": "gprofiler-1761058503",
  "cycle_id": "42"
}
```
*Note: run_id and cycle_id are only present when gProfiler is actively profiling*

**User Tags (custom):**
```json
{
  "method_name": "GProfiler._snapshot",
  "profiler_name": "java",
  "failure_reason": "timeout",
  "duration_ms": "5000"
}
```

### Goku Protocol Message

```
put gprofiler.system_profiler.process_profiler_failure.error 1761094217 1 service=gprofiler hostname=hostname component=system_profiler severity=error os_type=linux python_version=3.8 method_name=GProfiler._snapshot profiler_name=java
```

*Note: Actual message includes all system tags (service, hostname, component, severity, os_type, python_version) plus any user tags. Runtime tags (run_id, cycle_id) are added when gProfiler is actively profiling.*
