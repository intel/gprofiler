---
name: debug
description: Debug profiler issues and analyze errors. Use when the user encounters errors, crashes, or unexpected behavior in gProfiler.
allowed-tools: Bash(python3 *) Bash(strace *) Bash(sudo *) Bash(dmesg *) Bash(journalctl *) Read Grep Glob
---

## gProfiler Debugging Guide

### System Context

```!
uname -a
python3 --version
cat /etc/os-release 2>/dev/null | head -5
```

### Common Debug Commands

#### Check Profiler Dependencies
```bash
# Verify perf is available
which perf && perf --version

# Check kernel capabilities
cat /proc/sys/kernel/perf_event_paranoid
cat /proc/sys/kernel/kptr_restrict

# Check CAP_SYS_ADMIN capability
capsh --print 2>/dev/null | grep -i sys_admin
```

#### Debug Running gProfiler
```bash
# Run with verbose logging
sudo python3 -m gprofiler -v --output-dir /tmp/gprofiler-debug

# Trace system calls
sudo strace -f -o /tmp/gprofiler.strace python3 -m gprofiler ...

# Check for permission issues
sudo dmesg | tail -50 | grep -i "permission\|denied\|perf"
```

#### Profiler-Specific Debugging

| Profiler | Debug Flag | Log Location |
|----------|------------|--------------|
| perf | `--perf-mode=fp` | stderr |
| Java | `--java-async-profiler-mode=cpu` | `/tmp/async-profiler.log` |
| Python | `--python-mode=pyperf` | stderr |
| Ruby | `--ruby-mode=rbspy` | stderr |

### Common Issues

1. **"perf_event_open failed"** - Check `/proc/sys/kernel/perf_event_paranoid` (should be ≤1)
2. **"Permission denied"** - Run with sudo or check capabilities
3. **"No samples collected"** - Process may be idle or profiler incompatible
4. **Container issues** - Check `--privileged` or `SYS_ADMIN` capability

### Instructions

1. Gather error messages and stack traces
2. Check system requirements above
3. Identify which profiler is failing
4. Review relevant profiler code in `gprofiler/profilers/`
5. Suggest fixes or workarounds

---

## TODO: Skill Content to Add

- [ ] **Add error message catalog** - Common errors with solutions
- [ ] **Add kernel version requirements** - Minimum kernel for each feature
- [ ] **Add container runtime matrix** - Docker/containerd/cri-o compatibility
- [ ] **Add perf_event_paranoid guide** - All levels and their effects
- [ ] **Add capability requirements** - Required Linux capabilities per profiler
- [ ] **Add log file locations** - Where to find all log files
- [ ] **Add strace interpretation** - How to read strace output for gProfiler
- [ ] **Add profiler-specific debug flags** - Debug options per profiler
