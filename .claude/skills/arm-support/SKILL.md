---
name: arm-support
description: Debug and fix ARM/Aarch64 compatibility issues. Use when the user encounters ARM-specific bugs or needs to add ARM support for a feature.
---

## ARM/Aarch64 Support Guide

**Context from history:** ARM issues are recurring (6+ commits fixing ARM-specific bugs in 3 years).

### Architecture Support Status

| Runtime | x86_64 | Aarch64 |
|---------|--------|---------|
| perf (native, Golang) | ✅ | ✅ |
| Java (async-profiler) | ✅ | ✅ |
| Python (py-spy) | ✅ | ✅ |
| Python (PyPerf eBPF) | ✅ | ❌ |
| Ruby (rbspy) | ✅ | ✅ |
| PHP (phpspy) | ✅ | ✅ (experimental) |
| NodeJS (perf) | ✅ | ✅ |
| .NET (dotnet-trace) | ✅ (exp) | ✅ (exp) |

### Common ARM Issues from History

**1. gProfiler ARM Build Failures**
```
# Commit: [Reliability] Fix gProfiler arm (#988)
# Commit: Fixes #977 gprofiler does not work on aarch64 (#978)
```

**2. Architecture Metadata**
```
# Commit: Fix arch metadata with Aarch64 (#837)
# Issue: Incorrect architecture reporting
```

**3. getaddrinfo() EBUSY**
```
# Commit: Fix getaddrinfo() EBUSY on ARM (#825)
# Issue: Network resolution fails intermittently on ARM
```

**4. Perf Build on ARM**
```
# Commit: Perf: fix v6.7 build on aarch64 (#891)
# Issue: perf tool compilation differences
```

### Checking Architecture

```bash
# Check current architecture
uname -m
# x86_64 or aarch64

# In Python
import platform
platform.machine()
```

### Building for ARM

```bash
# Native ARM build (on ARM host)
./scripts/build_aarch64_container.sh -t gprofiler:arm64
./scripts/build_aarch64_executable.sh

# Cross-build from x86_64 (slow, requires QEMU)
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
docker buildx create --name multiarch --driver docker-container --use
./scripts/build_aarch64_container.sh -t gprofiler:arm64
```

### Testing on ARM

```bash
# Run tests on ARM host
cd tests && sudo python3 -m pytest -v

# Skip x86_64-only tests
cd tests && sudo python3 -m pytest -v -k "not x86_64_only"

# Check for ARM-specific test markers
grep -r "aarch64\|arm64" tests/
```

### ARM-Specific Code Patterns

```python
import platform

def is_aarch64() -> bool:
    return platform.machine() in ("aarch64", "arm64")

# Conditional logic for ARM
if is_aarch64():
    # ARM-specific handling
    pass
```

### Key Files for ARM Support

```
gprofiler/utils/          # Architecture detection utilities
scripts/build_aarch64_*   # ARM build scripts
tests/conftest.py         # Test fixtures with arch detection
```

### Debugging ARM Issues

```bash
# Check kernel version (ARM may have different capabilities)
uname -r

# Check perf support
perf list

# Check CPU info
cat /proc/cpuinfo | head -20

# Check if running in container
cat /proc/1/cgroup
```

### PyPerf/eBPF on ARM

**Note:** PyPerf (eBPF-based Python profiler) is NOT supported on ARM.

```python
# From gprofiler/profilers/python_ebpf.py
# PyPerf requires x86_64
if is_aarch64():
    # Fall back to py-spy
    pass
```

### CI/CD for ARM

`.github/workflows/build-test-deploy.yml`:
- Separate jobs for x86_64 and aarch64
- ARM builds use different runner labels
- Some tests may be skipped on ARM

### Commit Message Pattern

```
Fix <issue> on ARM/Aarch64 (#PR_NUMBER)
[Reliability] Fix gProfiler arm (#PR_NUMBER)
```

---

## TODO: Skill Content to Add

- [ ] **Add complete ARM compatibility table** - All profilers × ARM status
- [ ] **Add ARM kernel requirements** - Minimum kernel versions for features
- [ ] **Add Graviton-specific notes** - AWS Graviton 2/3 specific issues
- [ ] **Add ARM perf event support** - Which perf events work on ARM
- [ ] **Add ARM Docker tips** - Multi-arch build best practices
- [ ] **Add ARM test infrastructure** - How to run ARM tests locally
- [ ] **Add cross-compile troubleshooting** - Common QEMU issues
- [ ] **Add ARM cloud provider notes** - AWS, GCP, Azure ARM instances
