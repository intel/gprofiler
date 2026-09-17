# Profiling Control System with Heartbeat Protocol

This document describes the implementation of a centralized profiling control system where a backend can dynamically issue profiling commands (start/stop) to gProfiler agents via a heartbeat protocol.

## System Overview

```
┌─────────────────────┐    Heartbeat     ┌──────────────────────┐
│                     │ ◄──────────────► │                      │
│   Profiling Backend │                  │   gProfiler Agent    │
│     (REST API)      │   Commands       │                      │
│                     │ ────────────────► │                      │
└─────────────────────┘                  └──────────────────────┘
```

### Key Components

1. **Backend** — central control server that receives profiling requests via REST API, manages commands per host/service, responds to agent heartbeats with pending commands, and tracks execution status.

2. **gProfiler Agent** — profiling agent that sends periodic heartbeats, receives and executes commands, provides idempotent execution, and reports command completion.

---

## Package Structure

All dynamic-profiling orchestration lives in **`gprofiler/dynamic_profiling_management/`**:

```
gprofiler/
└── dynamic_profiling_management/
    ├── __init__.py          # ProfilerSlotBase class + shared helpers
    ├── heartbeat.py         # HeartbeatClient + DynamicGProfilerManager
    ├── command_control.py   # CommandManager + ProfilingCommand
    ├── continuous.py        # ContinuousProfilerSlot
    └── ad_hoc.py            # AdhocProfilerSlot
```

| File | Responsibility |
|---|---|
| `__init__.py` | `ProfilerSlotBase` (shared slot lifecycle), helper functions (`create_profiler_args`, `create_gprofiler_instance`, `get_enabled_profiler_types`) |
| `heartbeat.py` | `HeartbeatClient` (HTTP/TLS heartbeat communication) and `DynamicGProfilerManager` (thin orchestrator that delegates to the two slots) |
| `command_control.py` | `CommandManager` (priority queue: stop > ad-hoc > continuous) and `ProfilingCommand` dataclass |
| `continuous.py` | `ContinuousProfilerSlot(ProfilerSlotBase)` — primary slot for continuous or single-run profiling |
| `ad_hoc.py` | `AdhocProfilerSlot(ProfilerSlotBase)` — parallel slot for non-overlapping ad-hoc profiling |

### Import Graph (no circular dependencies)

```
__init__.py  ──►  (no package submodule imports)
      ▲
      │
      ├── continuous.py   imports __init__ + command_control
      ├── ad_hoc.py       imports __init__ + command_control
      │
      └── heartbeat.py    imports continuous + ad_hoc + command_control
                                   ▲
                                   │
                          main.py imports heartbeat
```

---

## Architecture: Two-Slot Profiler Manager

`DynamicGProfilerManager` uses two execution slots so that non-overlapping profiler types can run in parallel while overlapping types fall back to time-slicing:

```
DynamicGProfilerManager
├── primary: ContinuousProfilerSlot   (main continuous/single-run profiler)
├── adhoc:   AdhocProfilerSlot        (parallel ad-hoc profiler)
└── command_manager: CommandManager    (priority queue)
```

### Decision Flow

When a new `start` command arrives:

```
┌──────────────────────────────────────┐
│        New "start" command           │
└──────────────┬───────────────────────┘
               ▼
     ┌─────────────────────┐   YES
     │ Primary slot empty? ├────────► Start in primary slot
     └────────┬────────────┘
              │ NO
              ▼
     ┌─────────────────────────────┐   YES
     │ Adhoc slot free AND         ├────────► Start in ad-hoc slot (parallel)
     │ profiler types don't overlap│
     └────────┬────────────────────┘
              │ NO
              ▼
     ┌─────────────────────┐   YES
     │ Primary is continuous├────────► Pause primary → Start new in primary
     │ (can be paused)?     │          (time-slice fallback)
     └────────┬────────────┘
              │ NO
              ▼
        Command stays queued
```

### Profiler Type Overlap Detection

Each profiling command maps its `profiler_configs` to canonical types:

| Config Key | Canonical Type |
|---|---|
| `perf` | `perf` |
| `async_profiler` | `java` |
| `pyperf` / `pyspy` | `python` |
| `phpspy` | `php` |
| `rbspy` | `ruby` |
| `dotnet_trace` | `dotnet` |
| `nodejs_perf` | `nodejs` |

Two commands **overlap** when `set(types_A) & set(types_B)` is non-empty. If no `profiler_configs` is specified, all types are assumed enabled.

### ProfilerSlotBase

Both `ContinuousProfilerSlot` and `AdhocProfilerSlot` inherit from `ProfilerSlotBase` which provides:

- **State management**: `gprofiler`, `thread`, `command`, `profiler_types`
- **Lifecycle**: `stop()`, `is_running()`, `is_running_command(id)`
- **Shared start/run**: `_start_profiler()`, `_run_profiler()` (thread target)
- **Hook**: `_on_complete()` — override for slot-specific post-run behavior

### ContinuousProfilerSlot

- Primary slot: handles both continuous and single-run profiling.
- `can_be_paused()` — returns `True` only for continuous commands.
- Tracks `command_start_time`.
- On completion, checks if queued commands are waiting.

### AdhocProfilerSlot

- Parallel slot: always non-continuous (`continuous=False`).
- `can_run(next_cmd, current_profiler_types)` — checks slot availability, non-continuous, and type non-overlap.
- `cleanup_if_completed()` — called each heartbeat tick to free the slot when the thread finishes.

---

## Command Queue (CommandManager)

Priority-based queue with three levels:

| Priority | Queue | Max Size | Behavior |
|---|---|---|---|
| 1 (highest) | `stop_queue` | 1 | Immediate termination of all profilers |
| 2 | `adhoc_queue` | 10 | FIFO, single-run commands |
| 3 (lowest) | `continuous_queue` | 1 | Replaced by newer continuous commands |

Key operations: `enqueue_command`, `get_next_command` (peek), `dequeue_command`, `pause_command`.

---

## HeartbeatClient

Handles HTTP/TLS communication with the backend:

- **TLS/mTLS**: Configurable CA bundle, client cert/key.
- **Certificate refresh**: Background thread for periodic TLS session refresh.
- **Idempotency**: Tracks `received_command_ids` and `executed_command_ids` with configurable history limit.
- **PMU events**: Reports supported hardware performance events via `get_pmu_manager()`.

---

## API Endpoints

### 1. Submit Profiling Request

```http
POST /api/metrics/profile_request
```

**Request Body:**
```json
{
  "service_name": "my-service",
  "command_type": "start",
  "duration": 60,
  "frequency": 11,
  "profiling_mode": "cpu",
  "target_hostnames": ["host1", "host2"],
  "pids": [1234, 5678],
  "stop_level": "process",
  "additional_args": {
    "enable_perfspect": true
  }
}
```

**Response:**
```json
{
  "success": true,
  "message": "Start profiling request submitted successfully",
  "request_id": "req-uuid",
  "command_id": "cmd-uuid",
  "estimated_completion_time": "2025-01-08T12:00:00Z"
}
```

### 2. Agent Heartbeat

```http
POST /api/metrics/heartbeat
```

**Request Body:**
```json
{
  "ip_address": "192.168.1.100",
  "hostname": "worker-01",
  "service_name": "my-service",
  "last_command_id": "cmd-uuid",
  "status": "active",
  "timestamp": "2025-01-08T11:00:00Z",
  "received_command_ids": ["cmd-1", "cmd-2"],
  "executed_command_ids": ["cmd-1"],
  "perf_supported_events": ["cycles", "instructions"]
}
```

**Response (with command):**
```json
{
  "success": true,
  "message": "Heartbeat received. New profiling command available.",
  "profiling_command": {
    "command_type": "start",
    "combined_config": {
      "duration": 60,
      "frequency": 11,
      "profiling_mode": "cpu",
      "continuous": false,
      "profiler_configs": {
        "async_profiler": {"enabled": true, "time": "cpu"},
        "perf": {"mode": "enabled_restricted", "events": ["cycles"]}
      }
    }
  },
  "command_id": "cmd-uuid"
}
```

### 3. Report Command Completion

```http
POST /api/metrics/command_completion
```

**Request Body:**
```json
{
  "command_id": "cmd-uuid",
  "hostname": "worker-01",
  "status": "completed",
  "execution_time": 65,
  "error_message": null,
  "results_path": "/path/to/results"
}
```

---

## Profiler Configuration Reference

The `profiler_configs` object in `combined_config` controls which profilers are enabled:

```json
{
  "profiler_configs": {
    "perf": {"mode": "enabled_restricted", "events": ["cycles", "cache-misses"]},
    "async_profiler": {"enabled": true, "time": "cpu"},
    "pyperf": "enabled",
    "pyspy": "enabled_fallback",
    "phpspy": "enabled",
    "rbspy": "enabled",
    "dotnet_trace": "enabled",
    "nodejs_perf": "enabled"
  }
}
```

### Perf Modes

| Mode | `max_system_processes` | `max_docker_containers` |
|---|---|---|
| `enabled_restricted` | 600 | 2 |
| `enabled_aggressive` | 1500 | 50 |
| `disabled` | — | — |

### Java Async Profiler

The `async_profiler` key accepts a configuration dict or the shorthand string `"disabled"`.

**Dict format:**

| Field | Type | Required | Description |
|---|---|---|---|
| `enabled` | bool | no (default `true`) | Set to `false` to disable Java profiling entirely |
| `time` | string | no (default `"cpu"`) | Profiling mode — see table below |
| `alloc_interval` | string | no (default `"2MB"`) | Allocation sampling interval; only used when `time` is `"alloc"`. Uses [bitmath](https://pypi.org/project/bitmath/) notation (e.g. `"512KiB"`, `"2MB"`) |

**`time` mode values:**

| Value | async-profiler mode | Description |
|---|---|---|
| `"cpu"` | `cpu` | CPU time via `perf_events` |
| `"itimer"` | `itimer` | CPU time via `SIGPROF` (fallback when perf events are unavailable) |
| `"wall"` | `wall` | Wall-clock time (includes threads waiting on I/O or sleeping) |
| `"alloc"` | `alloc` | Allocation profiling; sets `profiling_mode` to `"allocation"`. Interval is controlled by `alloc_interval` (e.g. `"512KiB"`, `"2MB"`) |
| `"auto"` | `cpu` or `itimer` | Selects `cpu` if perf events are available on the host; falls back to `itimer` |

**Examples:**

```json
{"enabled": true, "time": "cpu"}
{"enabled": true, "time": "itimer"}
{"enabled": true, "time": "wall"}
{"enabled": true, "time": "alloc", "alloc_interval": "512KiB"}
{"enabled": true, "time": "auto"}
{"enabled": false}
```

**String shorthand:** `"disabled"` is equivalent to `{"enabled": false}` and is supported for backward compatibility.

### Python

- `pyperf: "enabled"` — eBPF-based PyPerf
- `pyspy: "enabled_fallback"` — py-spy as fallback (auto mode)
- Both `"disabled"` — Python profiling off

---

## PerfSpect Hardware Metrics Integration

When `enable_perfspect: true` is set in `combined_config`, the agent:

1. Locates the pre-installed PerfSpect binary via `resource_path("perfspect/perfspect")`
2. Enables `collect_hw_metrics`
3. Runs PerfSpect alongside CPU profiling

### Output Files

- `{hostname}_metrics.csv` — raw hardware metrics
- `{hostname}_metrics_summary.csv` — summary CSV
- `{hostname}_metrics_summary.html` — summary HTML report

### Requirements

- Linux x86_64
- Root access for hardware performance counters
- PerfSpect binary pre-installed as a resource

---

## Command Flow

```
1. User submits profiling request to backend
   ↓
2. Backend creates command with unique ID
   ↓
3. Agent sends heartbeat to backend
   ↓
4. Backend responds with pending command
   ↓
5. Agent enqueues command in CommandManager
   ↓
6. DynamicGProfilerManager routes to primary or ad-hoc slot
   ↓
7. Profiler runs in a daemon thread
   ↓
8. On completion, command is dequeued and completion reported
```

---

## Usage

### Run Agent in Heartbeat Mode

```bash
sudo ./gprofiler \
  --enable-heartbeat-server \
  --upload-results \
  --token "$TOKEN" \
  --service-name "my-service" \
  --api-server "http://backend:8000" \
  --heartbeat-interval 30 \
  --output-dir /tmp/profiles \
  --verbose
```

### Submit Start Command

```bash
curl -X POST http://localhost:8000/api/metrics/profile_request \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "web-service",
    "command_type": "start",
    "duration": 120,
    "frequency": 11,
    "profiling_mode": "cpu",
    "target_hostnames": ["web-01", "web-02"]
  }'
```

### Submit Stop Command

```bash
curl -X POST http://localhost:8000/api/metrics/profile_request \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "web-service",
    "command_type": "stop",
    "stop_level": "host",
    "target_hostnames": ["web-01"]
  }'
```

---

## CLI Options

```
--enable-heartbeat-server         Enable heartbeat communication
--heartbeat-interval SECONDS      Heartbeat frequency (default: 30)
--api-server URL                  Backend server URL
--upload-results, -u              Upload results to backend
--token TOKEN                     Authentication token
--service-name NAME               Service identifier
--output-dir, -o PATH             Local output directory
--continuous, -c                  Continuous profiling mode
--duration, -d SECONDS            Profiling duration
--verbose                         Enable verbose logging
--enable-hw-metrics-collection    Enable PerfSpect hardware metrics
--perfspect-path PATH             Path to PerfSpect binary
--perfspect-duration SECONDS      PerfSpect collection duration (default: 60)
--tls-client-cert PATH            Client certificate for mTLS
--tls-client-key PATH             Client key for mTLS
--tls-ca-bundle PATH              Custom CA bundle
--tls-cert-refresh-enabled        Enable periodic TLS certificate refresh
--tls-cert-refresh-interval SECS  Certificate refresh interval (default: 21600)
```

---

## Test Cases

The following manual test cases verify the parallel/time-slicing behavior of the two-slot manager. These should be automated as unit/integration tests in a future iteration.

### TC1 — Parallel: Non-Overlapping Profiler Types

**Setup:** Continuous profiler running Java async-profiler. Ad-hoc command arrives enabling Python (pyperf + py-spy) only.

**Expected:** Non-overlapping types (`java` vs `python`) → ad-hoc runs in the **parallel ad-hoc slot** without disturbing the continuous profiler.

**Observed:** Agent logged `"Starting parallel ad-hoc profiler … (non-overlapping profiler types)"`.

**Result:** PASS

---

### TC2 — Time-Slice: Overlapping Profiler Types

**Setup:** Continuous profiler running Java async-profiler. Ad-hoc command arrives also enabling Java async-profiler.

**Expected:** Overlapping type (`java`) → time-slice behavior: pause/stop continuous, run ad-hoc, then resume continuous via the queue.

**Observed:** Agent logged `"Pausing current profiler … (overlapping types)"` and the continuous profiler was stopped before the ad-hoc started.

**Known Issue:** During the stop path, an error `'NoopProfiler' object has no attribute 'name'` was emitted. This is a pre-existing issue unrelated to the parallel slot feature — `NoopProfiler` should either provide a `name` attribute or the stop/logging code should guard against its absence.

**Result:** PASS (with pre-existing warning)

---

### TC3 — Continuous Replacement

**Setup:** Continuous profiler running. A new continuous command arrives with different configuration.

**Expected:** The latest continuous config replaces the prior one. Only one continuous command should be active/queued at any time.

**Observed:** New continuous start replaced the prior continuous.

**Result:** PASS

---

### TC4 — Time-Slice: Partial Overlap

**Setup:** Continuous profiler with Python profiling enabled. Ad-hoc command arrives enabling pyperf only.

**Expected:** Overlapping type (`python`) → time-slice (continuous yields to ad-hoc).

**Observed:** Agent identified overlap and paused/stopped continuous to run ad-hoc.

**Result:** PASS

---

### TC5 — Ad-Hoc Queue Serialization

**Setup:** Two ad-hoc commands submitted in rapid succession while primary slot is occupied.

**Expected:** First ad-hoc runs in the ad-hoc slot (or primary after time-slice). Second ad-hoc waits in the queue until the first completes.

**Observed:** Second ad-hoc waited until the first completed before executing.

**Result:** PASS

---

### Test Summary

| TC | Scenario | Slot Used | Result |
|---|---|---|---|
| TC1 | Non-overlapping (Java + Python) | Parallel ad-hoc | PASS |
| TC2 | Overlapping (Java + Java) | Time-slice | PASS (known warning) |
| TC3 | Continuous replacement | Primary | PASS |
| TC4 | Partial overlap (Python + pyperf) | Time-slice | PASS |
| TC5 | Queued ad-hoc serialization | Queue | PASS |

### Future: Automating These Tests

These test cases can be converted to unit tests by mocking `HeartbeatClient.send_heartbeat()` to return scripted command sequences and asserting on:

- Which slot each command was routed to (`primary.is_running()`, `adhoc.is_running()`)
- Profiler type sets on each slot (`primary.profiler_types`, `adhoc.profiler_types`)
- Queue state after each step (`command_manager.has_queued_commands()`)
- Log messages emitted (using `caplog` fixture in pytest)

---

## Error Handling

### Agent
- Retries failed heartbeats with backoff (heartbeat loop continues on errors)
- Graceful profiler shutdown via `stop()` + `maybe_cleanup_subprocesses()`
- Command dequeue in `finally` block ensures no orphaned queue entries
- Failed commands are reported to backend via `send_command_completion(status="failed")`

### Backend
- Validates profiling request parameters
- Returns appropriate HTTP status codes
- Responds to heartbeats with pending commands or empty acknowledgements

---

## Security

- **Authentication**: Token-based (`Authorization: Bearer`) for agent-backend communication
- **mTLS**: Optional mutual TLS with client cert/key and custom CA bundle
- **Certificate Refresh**: Background thread for periodic TLS session refresh (configurable interval)
- **Command Validation**: All command parameters validated before execution
- **Idempotency**: Duplicate commands rejected via received/executed ID tracking

---

## Building and Running Locally

### Prerequisites
- Linux (x86_64 or Aarch64)
- Python 3.10+
- Docker (for containerized builds)
- 16GB+ RAM for full builds
- Root access for profiling

### Build

```bash
cd gprofiler

# Full build
./scripts/build_x86_64_executable.sh

# Fast build (development)
./scripts/build_x86_64_executable.sh --fast
```

### Run from Source

```bash
pip3 install -r requirements.txt
./scripts/copy_resources_from_image.sh
sudo python3 -m gprofiler [options]
```

### Quick Test

```bash
sudo ./build/x86_64/gprofiler -o /tmp/profiles -d 30
```

Open `/tmp/profiles/last_flamegraph.html` to view results.

---

## Troubleshooting

| Problem | Diagnosis |
|---|---|
| Agent not receiving commands | Check network, token, service name |
| Commands not executing | Check agent logs, command parameters, system permissions |
| Duplicate commands | Verify idempotency tracking, heartbeat timing |
| PerfSpect not working | Ensure x86_64, root, PerfSpect binary exists |
| Ad-hoc not running in parallel | Check profiler type overlap — overlapping types fall back to time-slice |

Enable verbose logging with `--verbose` for detailed diagnostics.
