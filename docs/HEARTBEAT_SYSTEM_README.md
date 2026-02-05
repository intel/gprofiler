# Profiling Control System with Heartbeat Protocol

This document describes the implementation of a centralized profiling control system where a Performance Studio backend can dynamically issue profiling commands (start/stop) to gProfiler agents via a heartbeat protocol.

## System Overview

```
┌─────────────────────┐    Heartbeat     ┌──────────────────────┐
│                     │ ◄──────────────► │                      │
│  Performance Studio │                  │   gProfiler Agent    │
│     Backend         │   Commands       │                      │
│                     │ ────────────────► │                      │
└─────────────────────┘                  └──────────────────────┘
```

### Key Components

1. **Performance Studio Backend** - Central control server that:
   - Receives profiling requests via REST API
   - Manages profiling commands for hosts/services  
   - Responds to agent heartbeats with pending commands
   - Tracks command execution status

2. **gProfiler Agent** - Profiling agent that:
   - Sends periodic heartbeats to the backend
   - Receives and executes profiling commands
   - Ensures idempotent command execution
   - Reports command completion status

## Features

### ✅ Backend Features
- **REST API** for submitting profiling requests
- **Heartbeat endpoint** for agent communication
- **Command merging** for multiple requests targeting same host
- **Process-level and host-level** stop commands
- **Idempotent command execution** using unique command IDs
- **Command completion tracking**
- **PerfSpect integration** for hardware metrics collection

### ✅ Agent Features  
- **Heartbeat communication** with configurable intervals
- **Dynamic profiling** based on server commands
- **Command-driven execution** (start/stop profiling)
- **Idempotency** to prevent duplicate command execution
- **Persistent command tracking** across agent restarts
- **Graceful error handling** and retry logic
- **PerfSpect auto-installation** for hardware metrics collection
- **Hardware metrics integration** with CPU profiling data

## API Endpoints

### 1. Submit Profiling Request

```http
POST /api/metrics/profile_request
```

**Request Body:**
```json
{
  "service_name": "my-service",
  "command_type": "start",  // "start" or "stop"
  "duration": 60,
  "frequency": 11,
  "profiling_mode": "cpu",
  "target_hostnames": ["host1", "host2"],
  "pids": [1234, 5678],  // Optional: specific PIDs
  "stop_level": "process",  // "process" or "host" (for stop commands)
  "additional_args": {
    "enable_perfspect": true  // Optional: enable hardware metrics collection
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
  "available_pids" : [java:{}, python:{}],
  "namespaces" : [{namespace: kube_system, pods : [{pod_name: gprofiler, containers : {{pid:123, name: metrics-exporter},{pid:123, name: metrics-exporter}},{pod_name: webapp, containers : {{pid:123, name: metrics-exporter},{pid:123, name: metrics-exporter}}]}],
  "status": "active",
  "timestamp": "2025-01-08T11:00:00Z"
}
"containers" -> "host" Table -> {container_name, array_of_hosts}
"pod" -> "host" Table -> {pod_name, array_of_hosts}
"namespace" -> "host" Table -> {namespace, array_of_hosts}

1. add k8s namespace hierarchy info as part of heartbeat 
2. save k8s information in hostheartbeats table and create de-normalized table for containersToHosts, podsToHost and namespaceToHosts, 
3. perform profiling : support profiling request by namespaces, pods and containers ( 5 )
4. test e2e ( 3 )
```

**Response:**
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
      "pids": "" 
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
  "status": "completed",  // "completed" or "failed"
  "execution_time": 65,
  "error_message": null,
  "results_path": "s3://bucket/path/to/results"
}
```

## PerfSpect Hardware Metrics Integration

The heartbeat system supports Intel PerfSpect integration for collecting hardware performance metrics alongside CPU profiling data. This feature enables comprehensive performance analysis by combining software-level profiling with hardware-level metrics.

### Overview

When `enable_perfspect: true` is included in the `additional_args` of a profiling request, the gProfiler agent will:

1. **Auto-install PerfSpect**: Downloads and extracts the latest PerfSpect binary from GitHub releases
2. **Configure hardware collection**: Enables `--enable-hw-metrics-collection` flag
3. **Set PerfSpect path**: Configures `--perfspect-path` to the auto-installed binary
4. **Collect metrics**: Runs PerfSpect alongside CPU profiling to gather hardware metrics

### Agent Behavior

#### Command Processing
When the agent receives a heartbeat response with `enable_perfspect: true` in the `combined_config`:

```python
# Agent processes the configuration
if combined_config.get("enable_perfspect", False):
    new_args.collect_hw_metrics = True
    
    # Auto-install PerfSpect
    from gprofiler.perfspect_installer import get_or_install_perfspect
    perfspect_path = get_or_install_perfspect()
    if perfspect_path:
        new_args.tool_perfspect_path = str(perfspect_path)
        logger.info(f"PerfSpect auto-installed at: {perfspect_path}")
```

#### Installation Process
1. **Download**: Fetches `perfspect.tgz` from `https://github.com/intel/PerfSpect/releases/latest/download/perfspect.tgz`
2. **Extract**: Unpacks to `/tmp/gprofiler_perfspect/perfspect/`
3. **Verify**: Checks binary exists and is executable
4. **Configure**: Sets path for gProfiler to use

#### Data Collection
PerfSpect runs with the following command:
```bash
/tmp/gprofiler_perfspect/perfspect/perfspect metrics \
  --duration 60 \
  --output /tmp/perfspect_data
```

### Output Files

When PerfSpect is enabled, additional files are generated:

- **Hardware Metrics CSV**: `/tmp/perfspect_data/{hostname}_metrics.csv`
- **Hardware Summary CSV**: `/tmp/perfspect_data/{hostname}_metrics_summary.csv`
- **Hardware HTML Report**: `/tmp/perfspect_data/{hostname}_metrics_summary.html`
- **Latest Metrics**: `/tmp/perfspect_data/{hostname}_metrics_summary_latest.csv`
- **Latest HTML**: `/tmp/perfspect_data/{hostname}_metrics_summary_latest.html`

### Example Request with PerfSpect

```bash
curl -X POST http://localhost:8000/api/metrics/profile_request \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "web-service",
    "command_type": "start",
    "duration": 60,
    "frequency": 11,
    "profiling_mode": "cpu",
    "target_hostnames": ["worker-01", "worker-02"],
    "additional_args": {
      "enable_perfspect": true
    }
  }'
```

### Combined Config Example

The agent receives the following `combined_config` in heartbeat responses:

```json
{
  "duration": 60,
  "frequency": 11,
  "continuous": true,
  "command_type": "start",
  "profiling_mode": "cpu",
  "enable_perfspect": true
}
```

### Requirements

- **Platform**: Linux x86_64 (PerfSpect requirement)
- **Permissions**: Root access for hardware performance counter access
- **Network**: Internet access to download PerfSpect binary
- **Storage**: ~50MB for PerfSpect installation and data files

### Troubleshooting

#### Common Issues

1. **Permission Denied**: Ensure agent runs with sufficient privileges
   ```bash
   sudo ./gprofiler --enable-heartbeat-server ...
   ```

2. **Download Failures**: Check network connectivity and GitHub access
   ```bash
   curl -I https://github.com/intel/PerfSpect/releases/latest/download/perfspect.tgz
   ```

3. **Binary Not Found**: Verify installation directory permissions
   ```bash
   ls -la /tmp/gprofiler_perfspect/perfspect/
   ```

#### Debug Logging

Enable verbose logging to see PerfSpect installation and execution details:
```bash
./gprofiler --enable-heartbeat-server --verbose
```

Look for log messages:
- `PerfSpect auto-installed at: /path/to/binary`
- `Using perfspect path: /path/to/binary`
- `Failed to auto-install PerfSpect, hardware metrics disabled`

## Usage Examples

### Backend - Submit Start Command

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
    "containers" : [],
    "pods" : [],
    "namespaces" : [],
  }'
```

### Backend - Submit Stop Command

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

### Agent - Run in Heartbeat Mode

**Basic heartbeat mode:**
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

**Production deployment with all optimizations:**
```bash
# Set environment variables first
export GPROFILER_TOKEN="my_token"
export GPROFILER_SERVICE="your-service-name"  
export GPROFILER_SERVER="http://localhost:8080"

# Production command (can also source /opt/gprofiler/envs.sh for variables)
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

## Implementation Details

### Backend Logic

1. **Command Generation**: Each profiling request generates a unique `command_id`
2. **Command Merging**: Multiple requests for the same host are merged into single commands
3. **Stop Handling**: 
   - Process-level stops remove specific PIDs from commands
   - Host-level stops terminate all profiling for the host
4. **Heartbeat Response**: Returns pending commands with `command_type` and configuration

### Agent Logic

1. **Heartbeat Loop**: Sends heartbeats at configured intervals
2. **Command Processing**:
   - `start`: Stop current profiler (if any) and start new one with given config
   - `stop`: Stop current profiler without starting a new one
3. **Idempotency**: Track executed command IDs to prevent duplicates
4. **Persistence**: Save executed command IDs to disk for restart resilience

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
5. Agent executes command (start/stop profiling)
   ↓
6. Agent reports completion to backend
   ↓
7. Backend updates command status
```

## Configuration

### Backend Configuration
- Database connection for command storage
- API endpoints for profiling control
- Command merging and deduplication logic

### Agent Configuration
```bash
--enable-heartbeat-server     # Enable heartbeat mode
--heartbeat-interval 30       # Heartbeat frequency (seconds)
--api-server URL             # Backend server URL
--upload-results             # Required for heartbeat mode
--token TOKEN                # Authentication token
--service-name NAME          # Service identifier
```

## Testing

### Test Scripts

1. **test_heartbeat_system.py** - Test backend API and heartbeat flow
2. **run_heartbeat_agent.py** - Run agent in heartbeat mode for testing

### Test Workflow

1. Start Performance Studio backend
2. Run test agent: `python run_heartbeat_agent.py`
3. Submit test commands: `python test_heartbeat_system.py`  
4. Verify agent receives and executes commands
5. Check idempotency and error handling

## Error Handling

### Backend
- Validates profiling request parameters
- Handles database connection errors
- Returns appropriate HTTP status codes
- Logs all operations for debugging

### Agent  
- Retries failed heartbeats with backoff
- Continues heartbeat loop on command execution errors
- Persists executed command IDs across restarts
- Graceful shutdown on termination signals

## Security Considerations

- **Authentication**: Token-based authentication for agent-backend communication
- **Authorization**: Service-based access control for profiling commands
- **Command Validation**: Validate all command parameters before execution
- **Rate Limiting**: Prevent abuse of profiling requests
- **Audit Logging**: Track all profiling activities for compliance

## Future Enhancements

- **Real-time Status**: WebSocket connection for real-time agent status
- **Command Scheduling**: Schedule profiling commands for future execution  
- **Resource Monitoring**: Check system resources before starting profiling
- **Multi-tenant Support**: Isolation between different services/teams
- **Command Prioritization**: Priority queues for urgent profiling requests
- **Distributed Coordination**: Coordinate profiling across multiple agents

## Troubleshooting

### Common Issues

1. **Agent not receiving commands**
   - Check network connectivity to backend
   - Verify authentication token
   - Check service name matching

2. **Commands not executing**
   - Check agent logs for errors
   - Verify command parameters are valid
   - Check system permissions for profiling

3. **Duplicate commands**
   - Verify idempotency implementation
   - Check command ID persistence
   - Review heartbeat timing

4. **PerfSpect hardware metrics not working**
   - Ensure Linux x86_64 platform (PerfSpect requirement)
   - Verify root/sudo permissions for hardware counters
   - Check internet connectivity for auto-installation
   - Look for "PerfSpect auto-installed" or "Failed to auto-install" log messages
   - Verify `/tmp/gprofiler_perfspect/perfspect/perfspect` binary exists and is executable

### Debugging

- Enable verbose logging: `--verbose`
- Check heartbeat logs: `/tmp/gprofiler-heartbeat.log`
- Monitor backend API logs
- Use test scripts to isolate issues
- For PerfSpect issues:
  - Check PerfSpect installation: `ls -la /tmp/gprofiler_perfspect/perfspect/`
  - Test PerfSpect manually: `/tmp/gprofiler_perfspect/perfspect/perfspect --help`
  - Check PerfSpect data directory: `ls -la /tmp/perfspect_data/`
  - Monitor hardware metrics collection in agent logs

## Building and Running gProfiler Locally

### Prerequisites
- Linux system (x86_64 or Aarch64)
- Python 3.10+ for source builds
- Docker for containerized builds
- 16GB+ RAM for full builds
- Root access for profiling operations

### Build Options

#### 1. Build Executable (Recommended)

```bash
cd gprofiler

# Full build (takes 20-30 minutes, builds all profilers from source)
./scripts/build_x86_64_executable.sh

# Fast build (for development, skips some optimizations)
./scripts/build_x86_64_executable.sh --fast
```

The executable will be created at `build/x86_64/gprofiler`.

#### 2. Build Docker Image

```bash
./scripts/build_x86_64_container.sh -t gprofiler
```

#### 3. Run from Source (Development)

```bash
# Install dependencies
pip3 install -r requirements.txt

# Copy required resources
./scripts/copy_resources_from_image.sh

# Run directly from source (requires root)
sudo python3 -m gprofiler [options]
```

### Running Locally

#### Basic Local Profiling

```bash
# Make executable and run basic profiling
chmod +x build/x86_64/gprofiler
sudo ./build/x86_64/gprofiler -o /tmp/gprofiler-output -d 30
```

#### Production-Style Local Run

```bash
# Set environment variables
export GPROFILER_TOKEN="my_token"
export GPROFILER_SERVICE="your-service-name"
export GPROFILER_SERVER="http://localhost:8080"

# Run with production flags
sudo ./build/x86_64/gprofiler \
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

#### Local Heartbeat Mode Testing

```bash
# Run agent in heartbeat mode for testing
sudo ./build/x86_64/gprofiler \
  --enable-heartbeat-server \
  --upload-results \
  --token=$GPROFILER_TOKEN \
  --service-name=$GPROFILER_SERVICE \
  --api-server $GPROFILER_SERVER \
  --heartbeat-interval 30 \
  --output-dir /tmp/profiles \
  --dont-send-logs \
  --server-upload-timeout 10 \
  --disable-metrics-collection \
  --java-safemode= \
  --java-no-version-check \
  --verbose
```

#### Local PerfSpect Testing (Manual)

```bash
# Test PerfSpect integration manually (Linux x86_64 only)
sudo ./build/x86_64/gprofiler \
  --enable-hw-metrics-collection \
  --perfspect-path /path/to/perfspect \
  --perfspect-duration 60 \
  --output-dir /tmp/profiles \
  --duration 60 \
  --verbose
```

### Command Line Options Explained

```bash
-u, --upload-results              # Upload results to Performance Studio
--token=$GPROFILER_TOKEN          # Authentication token
--service-name=$GPROFILER_SERVICE # Service identifier  
--server-host $GPROFILER_SERVER   # Performance Studio backend URL
--dont-send-logs                  # Disable log transmission
--server-upload-timeout 10        # Upload timeout (seconds)
-c, --continuous                  # Continuous profiling mode
--disable-metrics-collection      # Disable system metrics collection
--java-safemode=                  # Disable Java safe mode (empty value)
-d 60                            # Profiling duration (seconds)
--java-no-version-check          # Skip Java version check
--enable-heartbeat-server         # Enable heartbeat communication
--heartbeat-interval 30           # Heartbeat frequency (seconds)
--api-server URL                  # Heartbeat API server URL
-o, --output-dir PATH            # Local output directory
--verbose                        # Enable verbose logging

# PerfSpect Hardware Metrics Options (Linux x86_64 only)
--enable-hw-metrics-collection    # Enable hardware metrics via PerfSpect
--perfspect-path PATH            # Path to PerfSpect binary (auto-installed in heartbeat mode)
--perfspect-duration SECONDS     # PerfSpect collection duration (default: 60)
```

### Development Workflow

1. **Build**: `./scripts/build_x86_64_executable.sh --fast`
2. **Test locally**: `sudo ./build/x86_64/gprofiler -o /tmp/results -d 30`
3. **View results**: Open `/tmp/results/last_flamegraph.html` in browser
4. **Test heartbeat**: Run with `--enable-heartbeat-server` flag

### Troubleshooting Local Builds

- **Build fails**: Ensure 16GB+ RAM available
- **Permission errors**: Run profiling commands with `sudo`
- **Docker issues**: Ensure Docker daemon is running
- **Missing dependencies**: Install build requirements with package manager
