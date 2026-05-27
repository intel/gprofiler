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
  -u \
  --token=$GPROFILER_TOKEN \
  --service-name=$GPROFILER_SERVICE \
  --server-host $GPROFILER_SERVER \
  --dont-send-logs \
  --server-upload-timeout 10 \
  -c \
  --disable-metrics-collection \
  --java-safemode= \
  -d 60 \
  --java-no-version-check
```

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
gprofiler/main.py              # Heartbeat integration
gprofiler/command_control.py   # CommandManager class
docs/HEARTBEAT_SYSTEM_README.md # Full documentation
```

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
--upload-results              # Required for heartbeat mode
--token TOKEN                 # Authentication token
--service-name NAME           # Service identifier
--enable-hw-metrics-collection # Enable PerfSpect
--perfspect-path PATH         # PerfSpect binary path
```

---

## TODO: Skill Content to Add

- [ ] **Add complete API reference** - All heartbeat API endpoints with examples
- [ ] **Add command_control.py documentation** - CommandManager class details
- [ ] **Add authentication flow** - Token validation and refresh
- [ ] **Add error response codes** - All possible error responses
- [ ] **Add deployment examples** - K8s, Docker Compose, systemd configs
- [ ] **Add PerfSpect output examples** - Sample hardware metrics output
- [ ] **Add monitoring integration** - How to monitor heartbeat health
- [ ] **Add scaling guidance** - Multi-agent deployment patterns
