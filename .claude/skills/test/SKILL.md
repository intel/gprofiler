---
name: test
description: Run gProfiler test suite. Use when the user wants to run tests, check if tests pass, or test specific functionality.
allowed-tools: Bash(sudo *) Bash(pytest *) Bash(python3 -m pytest *) Bash(./tests/test.sh *) Bash(cd *) Read
---

## gProfiler Test Suite

**Important:** Tests require root privileges (sudo) for profiling system resources.

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

```bash
# Install dev dependencies
pip3 install -r dev-requirements.txt

# Ensure root has same packages
sudo pip3 install -r dev-requirements.txt

# Copy resources (if testing from source)
./scripts/copy_resources_from_image.sh
```

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

---

## TODO: Skill Content to Add

- [ ] **Add test fixture documentation** - Explain each conftest.py fixture in detail
- [ ] **Add example test patterns** - Copy-paste templates for new profiler tests
- [ ] **Add Docker test image list** - Complete list of runtime test images
- [ ] **Add test environment variables** - Document all test-related env vars
- [ ] **Add local test setup guide** - Step-by-step for first-time test runners
- [ ] **Add test output interpretation** - How to read test results and logs
- [ ] **Add flaky test retry patterns** - Document retry decorator usage
- [ ] **Add CI test matrix details** - What tests run on which platforms
