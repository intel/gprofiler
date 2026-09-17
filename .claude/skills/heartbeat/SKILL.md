---
name: heartbeat
description: Work with the gProfiler heartbeat system for dynamic profiling control. Use when the user asks about heartbeat mode, Performance Studio integration, or command-driven profiling.
---

## gProfiler Heartbeat System

The heartbeat system enables centralized profiling control where Performance Studio can dynamically issue start/stop commands to gProfiler agents.

### System Architecture

```
┌─────────────────────┐    Heartbeat     ┌──────────────────────┐
│  Performance Studio │ ◄──────────────► │   gProfiler Agent    │
│     Backend         │   Commands       │                      │
└─────────────────────┘ ────────────────► └──────────────────────┘
```

### Running in Heartbeat Mode

**Basic:**
```bash
python gprofiler/main.py \
  --enable-heartbeat-server \
  --upload-results \
  --token "your-token" \
  --service-name "web-service" \
  --api-server "http://performance-studio:8000" \
  --heartbeat-interval 30 \
  --output-dir /tmp/profiles \
  --verbose
```

**Production:**
```bash
export GPROFILER_TOKEN="my_token"
export GPROFILER_SERVICE="your-service-name"
export GPROFILER_SERVER="http://localhost:8080"

/opt/gprofiler/gprofiler \
  --enable-heartbeat-server \
  -u \
  --token=$GPROFILER_TOKEN \
  --service-name=$GPROFILER_SERVICE \
  --api-server $GPROFILER_SERVER \
  --dont-send-logs \
  --server-upload-timeout 10 \
  -c \
  --disable-metrics-collection \
  --java-safemode= \
  --heartbeat-interval 30 \
  -d 60 \
  --java-no-version-check
```

`--server-host` still exists as a deprecated alias, but prefer `--api-server`.

### Required flags

Current `main.py` validation requires heartbeat mode to include:

- `--enable-heartbeat-server`
- `--upload-results`
- `--token`
- `--service-name`

Use the skill to explain or debug this mode only in terms of the current flags above.

### Command Flow

```
1. User submits profiling request to backend
   ↓
2. Backend creates command with unique ID
   ↓
3. Agent sends heartbeat to backend
   ↓
4. Backend responds with pending command
   ↓
5. Agent checks idempotency (skip if already received)
   ↓
6. Agent enqueues command in priority queue
   ↓
7. Agent executes command (start/stop profiling)
   ↓
8. Agent reports completion to backend
```

### Command Priority Queues

| Queue | Purpose | Max Size |
|-------|---------|----------|
| `stop_queue` | Immediate stop commands | 1 |
| `adhoc_queue` | Single-run start commands | 10 |
| `continuous_queue` | Long-running start commands | 1 |

Priority: `stop > adhoc > continuous`

The current implementation lives under `gprofiler/dynamic_profiling_management/`. Do not refer users to `gprofiler/command_control.py`; that path is stale.

### API Endpoints

**Submit Profiling Request:**
```bash
curl -X POST http://localhost:8000/api/metrics/profile_request \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "web-service",
    "command_type": "start",
    "duration": 60,
    "frequency": 11,
    "profiling_mode": "cpu",
    "target_hostnames": ["host1", "host2"]
  }'
```

**Stop Profiling:**
```bash
curl -X POST http://localhost:8000/api/metrics/profile_request \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "web-service",
    "command_type": "stop",
    "stop_level": "host",
    "target_hostnames": ["host1"]
  }'
```

### PerfSpect Hardware Metrics

Enable Intel PerfSpect for hardware metrics:
```bash
curl -X POST http://localhost:8000/api/metrics/profile_request \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "web-service",
    "command_type": "start",
    "duration": 60,
    "additional_args": {
      "enable_perfspect": true
    }
  }'
```

Requirements:
- Linux x86_64 (Intel architecture)
- Root access
- Internet for auto-install

### Key Files

```
gprofiler/main.py                                       # CLI + heartbeat flag validation
gprofiler/dynamic_profiling_management/heartbeat.py     # Polling and command handling
gprofiler/dynamic_profiling_management/command_control.py # Queue logic and priority
gprofiler/dynamic_profiling_management/continuous.py    # Continuous slot
gprofiler/dynamic_profiling_management/ad_hoc.py        # Ad-hoc slot
tests/test_heartbeat_system.py                          # Heartbeat flow validation
docs/HEARTBEAT_SYSTEM_README.md                         # Full documentation
```

### Testing heartbeat changes

Use the smallest useful validation first:

```bash
# Focused heartbeat test
sudo python3 -m pytest -v tests/test_heartbeat_system.py

# Lightweight broader regression
sudo ./tests/test.sh --executable
```

For local end-to-end testing against a backend, the repo docs describe this sequence:

1. Start the Performance Studio backend.
2. Run `python tests/run_heartbeat_agent.py`
3. Submit commands with `python tests/test_heartbeat_system.py --live`

Prefer the existing docs/test scripts over inventing custom heartbeat harnesses.

### Troubleshooting

**Agent not receiving commands:**
- Check network connectivity
- Verify authentication token
- Check service name matching

**Commands not executing:**
- Check agent logs for errors
- Verify command parameters
- Check system permissions

**PerfSpect not working:**
- Verify Linux x86_64 platform
- Check root permissions
- Check `/tmp/gprofiler_perfspect/perfspect/`

### CLI Options Reference

```bash
--enable-heartbeat-server     # Enable heartbeat mode
--heartbeat-interval 30       # Heartbeat frequency (seconds)
--api-server URL              # Backend server URL
--server-host URL             # Deprecated alias for --api-server
--upload-results              # Required for heartbeat mode
--token TOKEN                 # Authentication token
--service-name NAME           # Service identifier
--enable-hw-metrics-collection # Enable PerfSpect
--perfspect-path PATH         # PerfSpect binary path
```

### Review points for heartbeat work

- Preserve queue semantics: `stop > adhoc > continuous`
- Preserve idempotency; do not allow the same command to execute twice
- Avoid moving heartbeat logic into `main.py` if `dynamic_profiling_management/` is sufficient
- Add targeted heartbeat tests before broader regression runs
