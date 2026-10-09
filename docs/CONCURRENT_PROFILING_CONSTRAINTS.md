# Concurrent Profiling Constraints

This document explains why gProfiler cannot run two profiling commands against the **same profiler types** simultaneously, how the queue-based design safely handles this, and how non-overlapping profiler types can run in parallel.

## Overview

Two profiling commands that enable the **same profiler type** (e.g., both enable perf, or both enable Java async-profiler) cannot run concurrently -- this is a hard constraint from the underlying kernel interfaces and runtime profilers. However, when two commands enable **completely different profiler types** (e.g., one runs only perf, the other runs only Java async-profiler), gProfiler can run them in parallel via a dedicated ad-hoc slot.

## Three Layers of Exclusivity

Concurrent profiling is blocked at three independent levels. Even if the upper layers were removed, the bottom layer (profiler internals) makes true parallelism impossible for the same target processes.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1: gProfiler System Mutex                                    │
│  grab_gprofiler_mutex() — one gProfiler process per host            │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 2: DynamicGProfilerManager — single current_gprofiler slot   │
├─────────────────────────────────────────────────────────────────────┤
│  Layer 3: Profiler Internals — kernel/runtime single-tenant limits  │
│  (THIS IS THE HARD CONSTRAINT)                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Layer 1: System-Wide Mutex

`grab_gprofiler_mutex()` in `gprofiler/utils/__init__.py` uses a Unix domain socket in the abstract namespace of the init network namespace as a system-wide lock:

```python
def grab_gprofiler_mutex() -> bool:
    """
    Implements a basic, system-wide mutex for gProfiler, to make sure
    we don't run 2 instances simultaneously.
    """
    GPROFILER_LOCK = "\x00gprofiler_lock"
    try:
        run_in_ns_wrapper(["net"], lambda: try_acquire_mutex(GPROFILER_LOCK))
    except CouldNotAcquireMutex:
        return False
    else:
        return True
```

A second gProfiler process on the same host will fail to acquire this lock and exit immediately. This provides automatic cleanup when the process goes down (no stale lock files). You can check who holds the lock with:

```bash
sudo netstat -xp | grep gprofiler
```

**Could this be bypassed?** Technically yes, but it exists to prevent the exact resource conflicts described in Layer 3.

### Layer 2: Two-Slot Manager Design (Primary + Parallel Ad-hoc)

`DynamicGProfilerManager` in `gprofiler/heartbeat.py` maintains a **primary slot** for the main profiling command and a **parallel ad-hoc slot** for non-overlapping ad-hoc commands:

```python
class DynamicGProfilerManager:
    def __init__(self, base_args, heartbeat_client):
        # Primary slot
        self.current_gprofiler: Optional['GProfiler'] = None
        self.current_thread: Optional[threading.Thread] = None
        self.current_command: Optional[ProfilingCommand] = None
        self.current_profiler_types: set = set()
        
        # Parallel ad-hoc slot (non-overlapping types only)
        self.adhoc_gprofiler: Optional['GProfiler'] = None
        self.adhoc_thread: Optional[threading.Thread] = None
        self.adhoc_command: Optional[ProfilingCommand] = None
        self.adhoc_profiler_types: set = set()
```

When an ad-hoc command arrives while another profiler is running, the manager checks for profiler type overlap:

```python
if self.current_gprofiler is None:
    self._start_new_profiler(...)            # Nothing running -> start
elif self._can_run_in_parallel(next_cmd):
    self._start_adhoc_profiler(...)          # Non-overlapping -> parallel
elif self._can_be_paused():
    self._stop_current_profiler()
    self._start_new_profiler(...)            # Overlapping -> time-slice
```

This is safe because each `GProfiler` instance creates its own `ProfilerState` with a separate `stop_event` and unique temporary directory. They share no mutable state.

### Layer 3: Profiler Internals (The Hard Constraint)

This is the fundamental blocker. The underlying profiling tools and kernel interfaces are **single-tenant per target process**. Two profiler instances targeting the same process will collide regardless of how the management layer is designed.

#### Java async-profiler: One Agent Per JVM

async-profiler is loaded as a native JVMTI agent into the target JVM via `jattach`. The JVM allows only one active async-profiler session at a time. A second `start` command returns an error:

```
[ERROR] Profiler already started
```

This is handled in `gprofiler/profilers/java.py`:

```python
def start_async_profiler(self, interval, second_try=False, ap_timeout=0):
    try:
        self._run_async_profiler(start_cmd)
        return True
    except JattachException as e:
        if e.is_ap_loaded:
            if (e.returncode == 200  # AP's COMMAND_ERROR
                and "[ERROR] Profiler already started\n" in e.get_ap_log()):
                return False  # profiler was already running
        raise
```

**Why this limitation exists:** async-profiler uses a single signal handler (for `perf_event` or `SIGPROF`) per JVM process. Two profiler sessions would need to share or fight over this single handler, which is not supported.

#### perf: Shared Hardware PMU Counters

The Linux `perf` subsystem uses hardware Performance Monitoring Unit (PMU) counters. These are finite physical resources on the CPU (typically 4-8 general-purpose counters per core). Two independent `perf record` sessions targeting the same processes or running system-wide would:

1. **Compete for PMU counters** -- the kernel multiplexes when counters are exhausted, reducing accuracy for both sessions
2. **Double the sampling overhead** -- each session generates its own interrupts and context switches
3. **Produce interleaved output** -- `perf record` writes to ring buffers; concurrent sessions can miss events during buffer processing
4. **Corrupt timing data** -- sampling rate regulation assumes a single consumer of the PMU events

The `PerfProcess` class in `gprofiler/utils/perf_process.py` configures `perf record` with specific mmap buffer sizes that assume exclusive access:

```python
class PerfProcess:
    _MMAP_SIZES = {"fp": 129, "dwarf": 257}  # pages, assumes exclusive access
```

#### Python py-spy / rbspy / phpspy: `ptrace` Exclusivity

py-spy, rbspy, and phpspy all use `ptrace()` to attach to their target processes. Linux enforces a strict **one tracer per process** rule:

```
ptrace(PTRACE_ATTACH, target_pid, ...) → EPERM if another tracer is attached
```

This is a kernel-level constraint (`kernel/ptrace.c`). A process can have at most one ptracer at any time. A second profiler attempting to `ptrace_attach` to an already-traced process will receive `EPERM`.

#### Python PyPerf (eBPF): Shared Kernel Resources

PyPerf loads eBPF programs into the kernel and attaches uprobes to Python interpreter functions. While the eBPF subsystem can theoretically support multiple programs, two PyPerf instances would:

- Attach duplicate uprobes to the same functions, doubling instrumentation overhead
- Write to separate eBPF maps, producing duplicate/divergent data
- Potentially conflict on perf event file descriptors

## Summary: Constraint Matrix

| Profiler | Attachment Method | Concurrency Limit | Enforcement Level |
|---|---|---|---|
| **async-profiler** (Java) | JVMTI agent via jattach | 1 per JVM process | JVM runtime (`[ERROR] Profiler already started`) |
| **perf** (system-wide) | `perf_event_open` syscall | Shared PMU counters (4-8 per core) | CPU hardware + kernel multiplexing |
| **py-spy** (Python) | `ptrace()` attach | 1 tracer per process | Kernel (`EPERM` on second attach) |
| **PyPerf** (Python eBPF) | eBPF uprobes | Shared kernel probes | Kernel (duplicate probe overhead) |
| **rbspy** (Ruby) | `ptrace()` attach | 1 tracer per process | Kernel (`EPERM`) |
| **phpspy** (PHP) | `ptrace()` attach | 1 tracer per process | Kernel (`EPERM`) |
| **dotnet-trace** (.NET) | EventPipe API | 1 session per process | .NET runtime (session exclusivity) |

## How the Queue System Handles This

The `CommandManager` in `gprofiler/command_control.py` implements a priority queue that safely serializes profiling commands:

```
Priority Order:
  1. Stop commands   (highest — immediate termination)
  2. Ad-hoc commands (single-run, higher than continuous)
  3. Continuous commands (long-running, lowest priority)
```

### Typical Flow: Ad-hoc Interrupts Continuous

```
Time ──────────────────────────────────────────────────────────►

 ┌──────────────────────┐
 │  Continuous profiling │  (running)
 │  command_id: cmd-001  │
 └──────────┬───────────┘
            │
            │  Ad-hoc command arrives (cmd-002)
            │  ┌──────────────────────────────────┐
            ▼  │                                  │
 1. Pause continuous (cmd-001 marked is_paused)   │
 2. Stop current profiler                         │
 3. Start ad-hoc profiler (cmd-002)               │
            │                                     │
            │  ┌──────────────┐                   │
            │  │  Ad-hoc run  │  (runs to completion)
            │  │  cmd-002     │                   │
            │  └──────┬───────┘                   │
            │         │                           │
            │  Ad-hoc completes, dequeued         │
            │  Heartbeat loop picks up next       │
            │  command from continuous queue       │
            │         │                           │
            │  ┌──────▼───────────────────┐       │
            │  │  Continuous resumes      │       │
            │  │  (new command or re-queue)│       │
            │  └──────────────────────────┘       │
            └─────────────────────────────────────┘
```

### Queue Sizing

```python
STOP_QUEUE_MAX_SIZE = 1       # Only one stop needed
ADHOC_QUEUE_MAX_SIZE = 10     # Buffer multiple ad-hoc requests
CONTINUOUS_QUEUE_MAX_SIZE = 1  # Only latest continuous config matters
```

When a new continuous command arrives, the continuous queue is cleared first (only the latest continuous configuration is relevant). Ad-hoc commands accumulate and are processed FIFO between heartbeat intervals.

## Execution Strategy: Parallel vs Time-Slicing

When a new ad-hoc command arrives while a profiler is already running, the `DynamicGProfilerManager` chooses the strategy automatically:

### Decision Flow

```
Ad-hoc command arrives
         │
         ▼
  Nothing running?  ──yes──►  Start in primary slot
         │ no
         ▼
  Profiler types     ──yes──►  Start in parallel ad-hoc slot
  don't overlap?               (both run simultaneously)
         │ no
         ▼
  Current is         ──yes──►  Pause current, run ad-hoc in primary,
  continuous?                  continuous re-queued (time-slicing)
         │ no
         ▼
  Wait for current ad-hoc to complete
```

### Profiler Type Extraction

`_get_enabled_profiler_types()` extracts the set of enabled profiler types from a command's `profiler_configs`. The canonical types are: `perf`, `java`, `python`, `php`, `ruby`, `dotnet`, `nodejs`.

If no `profiler_configs` are specified, all types are assumed enabled (the default). Overlap is a simple set intersection:

```python
def _can_run_in_parallel(self, next_cmd):
    if self.adhoc_gprofiler is not None:
        return False        # Adhoc slot already occupied
    if next_cmd.is_continuous:
        return False        # Don't run two continuous in parallel
    next_types = self._get_enabled_profiler_types(next_cmd.profiling_command)
    return not bool(next_types & self.current_profiler_types)
```

### Example: Non-Overlapping (Parallel)

Continuous command enables: `{"perf": "enabled_restricted", "async_profiler": "disabled", "pyperf": "disabled", ...}`
Ad-hoc command enables: `{"perf": "disabled", "async_profiler": {"enabled": true}, "pyperf": "disabled", ...}`

- Continuous types: `{"perf"}`
- Ad-hoc types: `{"java"}`
- Intersection: `{}` (empty) -> **run in parallel**

Both profilers run simultaneously in separate threads with separate `GProfiler` instances and separate `ProfilerState` objects.

### Example: Overlapping (Time-Slice)

Continuous command enables: `{"perf": "enabled_restricted", "async_profiler": {"enabled": true}}`
Ad-hoc command enables: `{"perf": "enabled_aggressive", "async_profiler": {"enabled": true}}`

- Continuous types: `{"perf", "java"}`
- Ad-hoc types: `{"perf", "java"}`
- Intersection: `{"perf", "java"}` -> **fallback to time-slicing**

Continuous is paused, ad-hoc runs to completion, continuous can resume.

### Example: Default Configs (Time-Slice)

If either command has no `profiler_configs`, all profiler types are assumed enabled. Two commands with all defaults will always overlap -> time-slicing.

## Other Approaches

### Separate Hosts

Run continuous profiling on one set of replicas and send ad-hoc commands to a different set. No resource contention since different hosts have independent PMU counters, ptrace namespaces, and JVM processes.

### Sampling Rate Adjustment

Run a single continuous profiler at a lower sampling rate, and temporarily increase the rate for "ad-hoc" snapshots. This stays within the single-profiler constraint while approximating the effect of two profilers.

## Key Takeaways

1. **Same profiler type = cannot run concurrently.** This is enforced by the kernel (ptrace, PMU counters) and runtimes (async-profiler, EventPipe).

2. **Different profiler types = can run in parallel.** The manager detects non-overlapping types and uses a dedicated ad-hoc slot.

3. **Overlapping types fall back to time-slicing.** Continuous is paused for ad-hoc, then resumed. This is safe and correct.

4. **The system-wide mutex (`grab_gprofiler_mutex`) prevents two gProfiler processes.** Within a single process, the `DynamicGProfilerManager` handles parallelism via the two-slot design.
