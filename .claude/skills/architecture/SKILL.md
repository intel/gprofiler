---
name: architecture
description: Understand gProfiler architecture and codebase structure. Use when the user asks how gProfiler works, wants to understand the codebase, or needs architectural guidance.
context: fork
user-invocable: true
---

## gProfiler Architecture Overview

gProfiler now has two important paths:

1. **Classic profiling path**: discover processes -> select profilers -> collect samples -> merge -> output/upload
2. **Dynamic profiling control path**: heartbeat with backend -> receive commands -> queue/prioritize -> run continuous or ad-hoc profiling -> report completion

When answering architecture questions, identify which path the request touches before reading or editing code.

### Main architecture areas

| Area | Primary files | What belongs here |
|------|---------------|-------------------|
| CLI + orchestration | `gprofiler/main.py` (~1546) | Argument parsing, runtime wiring, top-level orchestration. Treat as a hotspot; avoid broad edits if a narrower seam exists. |
| Profiler registration + lifecycle | `gprofiler/profilers/registry.py`, `gprofiler/profilers/profiler_base.py` | Registration, mode selection, `start()` / `snapshot()` / `stop()` lifecycle. |
| Runtime profilers | `gprofiler/profilers/*.py` | Runtime/tool-specific logic: process discovery, sampling, stack parsing, cleanup. |
| Merge/output | `gprofiler/merge.py` (~330) | Stack aggregation, symbol handling, output shaping. |
| Metadata enrichment | `gprofiler/metadata/` | Application identifiers and host/system metadata. |
| Dynamic profiling command control | `gprofiler/dynamic_profiling_management/heartbeat.py` (~354), `command_control.py` (~233), `continuous.py` (~68), `ad_hoc.py` (~76) | Heartbeat polling, command parsing, queue priority, pause/resume, execution routing. |
| Shared test infrastructure | `tests/conftest.py` (~708) | Docker fixtures, runtime builders, cleanup. Shared and regression-sensitive. |

### Runtime profilers

| Profiler | Backend tool | Notes |
|----------|--------------|-------|
| `perf.py` | Linux perf | System-wide profiling, kernel/user stacks |
| `java.py` | async-profiler | JVM attach, allocation profiling, large hotspot (~1555 lines) |
| `python.py` | py-spy | Python sampling without instrumentation |
| `python_ebpf.py` | PyPerf | eBPF-based Python profiling |
| `ruby.py` | rbspy | Ruby VM sampling |
| `php.py` | phpspy | PHP process profiling |
| `dotnet.py` | dotnet-trace | .NET support |
| `node.py` | perf | Node/V8 profiling |

### Classic profiling data flow

```text
1. Discover target processes
2. Select profilers / modes
3. Each profiler samples independently
4. merge.py aggregates results
5. Output collapsed stacks, flamegraph data, or upload results
```

### Dynamic profiling control flow

```text
1. Backend receives profiling request
2. Agent heartbeat polls for work
3. heartbeat.py parses command payload
4. command_control.py enqueues by priority
5. continuous.py or ad_hoc.py executes the command
6. Agent reports command completion
```

Priority is `stop > adhoc > continuous`. Do not describe or implement a parallel control path unless the existing heartbeat/queue system is truly insufficient.

### Where to make changes

- **Add a new profiler**: new file under `gprofiler/profilers/`, register via `@register_profiler`, add targeted tests.
- **Change profiler behavior**: edit the specific profiler first; avoid `main.py` unless CLI/wiring must change.
- **Change merge or output**: start in `gprofiler/merge.py`.
- **Change heartbeat / dynamic profiling**: start in `gprofiler/dynamic_profiling_management/`.
- **Change shared test behavior**: touch `tests/conftest.py` only when multiple tests need new shared infra.

### Guidance when answering users

1. Start with the smallest relevant architecture slice, not the whole repo.
2. If the request is about command-driven profiling, include the heartbeat + queue modules, not just `main.py`.
3. Call out hotspot files when relevant:
   - `gprofiler/main.py` (~1546)
   - `gprofiler/profilers/java.py` (~1555)
   - `tests/conftest.py` (~708)
4. Prefer concrete file references over generic descriptions.
