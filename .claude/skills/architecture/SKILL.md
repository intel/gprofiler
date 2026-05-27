---
name: architecture
description: Understand gProfiler architecture and codebase structure. Use when the user asks how gProfiler works, wants to understand the codebase, or needs architectural guidance.
context: fork
user-invocable: true
---

## gProfiler Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      gprofiler/main.py                       │
│                    (Orchestration Layer)                     │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌────────┐│
│  │  perf   │ │  Java   │ │ Python  │ │  Ruby   │ │  .NET  ││
│  │profiler │ │profiler │ │profiler │ │profiler │ │profiler││
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └───┬────┘│
│       └──────────┴──────────┴──────────┴───────────┘      │
│                         ▼                                   │
│              gprofiler/merge.py                             │
│           (Profile Data Aggregation)                        │
├─────────────────────────────────────────────────────────────┤
│                    Output Layer                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Flamegraph  │  │   Upload    │  │   Local Output      │ │
│  │   (HTML)    │  │  (Studio)   │  │   (collapsed)       │ │
│  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

#### 1. Profiler Registry (`gprofiler/profilers/registry.py`)
- Decorator-based profiler registration
- Runtime discovery of available profilers
- Configuration-based profiler selection

#### 2. Profiler Base (`gprofiler/profilers/profiler_base.py`)
- Abstract base class for all profilers
- Lifecycle: `start()` → `snapshot()` → `stop()`
- Common utilities for process discovery

#### 3. Individual Profilers (`gprofiler/profilers/*.py`)

| Profiler | Backend Tool | Key Features |
|----------|--------------|--------------|
| `perf.py` | Linux perf | System-wide, kernel stacks |
| `java.py` | async-profiler | JVM attach, allocation profiling |
| `python.py` | py-spy | No instrumentation needed |
| `python_ebpf.py` | PyPerf | eBPF-based, lower overhead |
| `ruby.py` | rbspy | Ruby VM sampling |
| `php.py` | phpspy | PHP process profiling |
| `dotnet.py` | dotnet-trace | .NET Core/5+ support |
| `node.py` | perf | V8 JavaScript profiling |

#### 4. Merge Layer (`gprofiler/merge.py`)
- Combines samples from multiple profilers
- Handles symbol resolution
- Produces unified stack traces

#### 5. Metadata Collection (`gprofiler/metadata/`)
- `application_identifiers.py` - Extracts app names from processes
- `system_metadata.py` - Collects host information
- Enriches profiles with context

### Data Flow

```
1. Process Discovery
   └── Scan /proc for target processes

2. Profiler Selection
   └── Match processes to appropriate profilers

3. Sampling
   └── Each profiler collects stacks independently

4. Aggregation
   └── merge.py combines all samples

5. Output
   └── Generate flamegraph or upload to Studio
```

### Key Files to Understand

| File | Lines | Purpose |
|------|-------|---------|
| `main.py` | ~1500 | Entry point, CLI, orchestration |
| `profilers/perf.py` | ~500 | Core perf integration |
| `profilers/java.py` | ~1800 | Complex JVM profiling |
| `merge.py` | ~400 | Profile aggregation |
| `utils/perf_process.py` | ~200 | perf subprocess management |

### Extension Points

1. **Add new profiler**: Implement `ProfilerBase`, use `@register_profiler`
2. **Add metadata**: Extend `application_identifiers.py`
3. **New output format**: Modify `main.py` output handling
4. **New deployment**: Add to `deploy/` directory

### Instructions

When user asks about architecture:
1. Start with high-level overview above
2. Dive into specific component if asked
3. Reference actual code files with line numbers
4. Explain data flow through the system
