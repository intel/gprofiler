---
name: test
description: Run gProfiler test suite. Use when the user wants to run tests, check if tests pass, or test specific functionality.
allowed-tools: Bash(sudo *) Bash(pytest *) Bash(python3 -m pytest *) Bash(./tests/test.sh *) Bash(cd *) Read
---

## gProfiler Test Suite

**Important:** Tests require root privileges (sudo) for profiling system resources.

### First-time local setup

```bash
git submodule update --init
python3 -m pip install -r requirements.txt -r dev-requirements.txt
sudo python3 -m pip install -r requirements.txt -r dev-requirements.txt
./scripts/copy_resources_from_image.sh
```

Prefer the repo's existing harness over custom setup scripts. `tests/test.sh` already installs missing apt packages unless `NO_APT_INSTALL` is set, checks required resources, and runs pytest with the expected environment.

### Quick Commands

```bash
# Full test suite
sudo ./tests/test.sh

# Executable-only tests (no Docker resources needed)
sudo ./tests/test.sh --executable

# Specific test file
cd tests && sudo python3 -m pytest -v test_perf.py

# Specific test by name
cd tests && sudo python3 -m pytest -v -k "test_java_profiling"

# Run with verbose output
cd tests && sudo python3 -m pytest -v -s test_sanity.py
```

### Staged test strategy

Run the narrowest useful tests first, then widen:

| Change type | First tests | Then |
|-------------|-------------|------|
| Heartbeat / dynamic profiling / queue logic | `sudo python3 -m pytest -v tests/test_heartbeat_system.py` | `sudo ./tests/test.sh --executable`, then `sudo ./tests/test.sh` if shared code changed |
| Specific profiler | Target that profiler's test file | `sudo ./tests/test.sh --executable` or full suite if lifecycle/shared infra changed |
| Merge / metadata / output | `tests/test_merge.py`, `tests/test_app_metadata.py`, `tests/test_appids.py` | Broader regression depending on touched files |
| `main.py`, `tests/conftest.py`, or cross-cutting changes | Most relevant targeted tests first | Always end with `sudo ./tests/test.sh` |

This staged approach reduces regression risk without paying full-suite cost for every small change.

### Test Categories

| Test File | Purpose | Notes |
|-----------|---------|-------|
| `test_sanity.py` | Basic smoke tests | Quick validation |
| `test_java.py` | Java profiler tests | Tests JDK 8-25, largest file (51KB) |
| `test_python.py` | Python profiler tests | py-spy and PyPerf |
| `test_perf.py` | System profiler tests | perf integration |
| `test_merge.py` | Profile merging | Data aggregation |
| `test_appids.py` | App identification | Metadata extraction |
| `test_executable.py` | Binary mode tests | PyInstaller build |
| `test_app_metadata.py` | Metadata collection | Frequently modified |

### Known Flaky Tests (from 3-year history)

**PyPerf/Python eBPF tests:**
- TMPDIR setup issues
- Non-blocking I/O problems
- Solution: Tests use `@pytest.mark.flaky(reruns=3, reruns_delay=2)`

**Java tests:**
- JDK version compatibility
- AsyncProfiler DSO directory permissions
- Temurin vs OpenJDK differences

**Dotnet tests:**
- patchelf installation required
- Timeout increases needed for newer .NET SDK

### Test Infrastructure

**conftest.py fixtures (most modified file - 13 changes in 3 years):**
- Docker container management
- Test application builders
- Resource cleanup
- Architecture detection (x86_64/aarch64)

**pytest plugins:**
- `pytest-rerunfailures` - Retry flaky tests
- `pytest-timeout` - Prevent hanging tests

Treat `tests/conftest.py` as shared infrastructure. Only change it when multiple tests need new common behavior.

### Running Tests for Specific Profilers

```bash
# Java profiler
cd tests && sudo python3 -m pytest -v test_java.py

# Python profiler (py-spy)
cd tests && sudo python3 -m pytest -v test_python.py -k "pyspy"

# Python profiler (PyPerf/eBPF)
cd tests && sudo python3 -m pytest -v test_python.py -k "pyperf"

# System profiler (perf)
cd tests && sudo python3 -m pytest -v test_perf.py

# Sanity tests with latest JVMs
cd tests && sudo python3 -m pytest -v -k "test_sanity_latest_jvms"
```

### ARM/Aarch64 Testing

From history: ARM tests have recurring issues
- `in_container` fixture for container tests
- Platform-specific test skips
- getaddrinfo() EBUSY errors on ARM

```bash
# Check architecture
uname -m

# Run with architecture awareness
cd tests && sudo python3 -m pytest -v --ignore=test_bigdata.py
```

### Test Environment Setup

For most contributors, the setup above plus `sudo ./tests/test.sh --executable` is the fastest way to prove the local test stack is healthy.

### Heartbeat / local e2e testing

For command-driven profiling changes:

```bash
# Focused heartbeat validation
sudo python3 -m pytest -v tests/test_heartbeat_system.py
```

For a live backend flow, the heartbeat docs describe:

1. Start Performance Studio backend
2. Run `python tests/run_heartbeat_agent.py`
3. Submit commands with `python tests/test_heartbeat_system.py --live`

Use the existing scripts above instead of writing a one-off harness.

### Debugging Test Failures

```bash
# Run with full output
cd tests && sudo python3 -m pytest -v -s --tb=long test_name.py

# Run specific test with debugging
cd tests && sudo python3 -m pytest -v -s -k "test_specific_name" --pdb

# Check Docker containers
docker ps -a | grep gprofiler
docker logs <container_id>
```

### CI Test Workflow

`.github/workflows/build-test-deploy.yml`:
1. Build executable (x86_64, aarch64)
2. Run executable tests
3. Build container with profilers
4. Run container tests
5. Deploy on tag push

### Validation checklist

- [ ] Choose targeted tests based on the changed architecture area
- [ ] Use `sudo ./tests/test.sh --executable` as the default broader sanity pass
- [ ] Run full `sudo ./tests/test.sh` after shared-fixture or cross-cutting changes
- [ ] Reuse existing test harness/scripts instead of ad-hoc local setup
