---
name: implement-from-spec
description: Implement gProfiler changes from a design spec. Use when the user asks to build a feature, modify behavior, or turn an architecture/design proposal into code with local setup, targeted unit/e2e tests, and regression-aware review.
context: fork
user-invocable: true
---

## Implementing gProfiler Changes From a Design Spec

Use this skill when the task is not just "edit code", but "map a spec onto the current gProfiler architecture, implement it with the smallest safe change, and prove it locally."

## Workflow

1. Read the design spec and classify the change before editing code.
2. Map the request onto the current architecture and choose the smallest existing flow that already solves most of the problem.
3. Prefer extending an existing path over inventing a parallel one.
4. Make focused edits first, then run the smallest test set that exercises the changed path.
5. Expand to broader regression coverage only after targeted tests pass.
6. Do an architecture-aware review before handing off.

## Architecture map

| Area | Primary files | Notes |
|------|---------------|-------|
| CLI + orchestration | `gprofiler/main.py` (~1546 lines) | Entry point, wiring, runtime args. High-risk hotspot; avoid broad rewrites. |
| Dynamic profiling command control | `gprofiler/dynamic_profiling_management/command_control.py` (~233), `heartbeat.py` (~354), `continuous.py` (~68), `ad_hoc.py` (~76) | Best place for heartbeat polling, queueing, start/stop handling, pause/resume semantics. |
| Runtime profilers | `gprofiler/profilers/*.py` | Extend the profiler-specific module before touching orchestration. Large hotspots include `java.py` (~1555) and `perf.py` (~441). |
| Merge/output | `gprofiler/merge.py` (~330) | Use for stack merging and output shaping, not profiler lifecycle changes. |
| Test harness | `tests/conftest.py` (~708), `tests/test_heartbeat_system.py` (~362), targeted `tests/test_*.py` | `conftest.py` is shared infra and regression-sensitive. Change it only when the spec truly needs new shared fixtures/behaviors. |

## Choose the right existing flow

### If the spec changes command-driven profiling

Prefer this flow:

1. Heartbeat poll / response handling in `gprofiler/dynamic_profiling_management/heartbeat.py`
2. Queueing / priority rules in `gprofiler/dynamic_profiling_management/command_control.py`
3. Execution routing in `continuous.py` or `ad_hoc.py`
4. Completion / status propagation back through the heartbeat path
5. Tests in `tests/test_heartbeat_system.py` plus any targeted profiler tests

Do **not** add a second control path if the existing heartbeat + `CommandManager` path can be extended.

### If the spec changes sampling or profiler behavior

Prefer:

1. Update the specific profiler in `gprofiler/profilers/`
2. Keep lifecycle behavior compatible with `ProfilerBase`
3. Only touch `main.py` if the change needs new CLI wiring or orchestration
4. Add/adjust profiler-specific tests before broad test-suite runs

### If the spec changes stack merging, metadata, or output

Prefer:

1. `gprofiler/merge.py` for aggregation/output semantics
2. `gprofiler/metadata/` for metadata enrichment
3. README/help text only if the change is user-visible

## Command-control example

For a design such as "add new targeting metadata to heartbeat and make command dispatch use it":

1. Start with `docs/HEARTBEAT_SYSTEM_README.md` to understand the current protocol.
2. Check `heartbeat.py` for payload creation/parsing.
3. Check `command_control.py` for queue semantics:
   - stop commands stay highest priority
   - ad-hoc commands outrank continuous commands
   - continuous commands may be paused/resumed
4. Keep idempotency intact. Do not create a path that can enqueue or execute the same command twice.
5. Validate with `tests/test_heartbeat_system.py` and then broader tests if shared behavior changed.

## Local setup and test bootstrap

Prefer the repo's existing test harness over ad-hoc local setup. It already knows how to install dependencies and spin up runtime resources.

### First-time setup

```bash
git submodule update --init
python3 -m pip install -r requirements.txt -r dev-requirements.txt
sudo python3 -m pip install -r requirements.txt -r dev-requirements.txt
./scripts/copy_resources_from_image.sh
```

### Fastest way to bootstrap the test stack

```bash
sudo ./tests/test.sh --executable
```

Use this first when you need a quick sanity check without the full container/resource-heavy suite.

### Full local validation

```bash
sudo ./tests/test.sh
```

Notes:

- `tests/test.sh` can auto-install missing apt packages and Docker dependencies unless `NO_APT_INSTALL` is set.
- The pytest infrastructure and `tests/conftest.py` spin up the needed runtime containers/resources for many tests. Prefer that over writing one-off setup scripts.
- If `gprofiler/resources/perf` is missing, extract resources first rather than patching tests around it.

## Test selection matrix

Run the narrowest useful test first, then widen.

| Change type | First tests | Then |
|-------------|-------------|------|
| Dynamic profiling / heartbeat / queue behavior | `sudo python3 -m pytest -v tests/test_heartbeat_system.py` | `sudo ./tests/test.sh --executable`, then `sudo ./tests/test.sh` if shared behavior changed |
| Specific profiler behavior | Target that file, for example `sudo python3 -m pytest -v tests/test_perf.py` or `tests/test_java.py` | Full suite if lifecycle/shared fixtures changed |
| Merge/output/metadata | Targeted `tests/test_merge.py`, `tests/test_app_metadata.py`, `tests/test_appids.py` | `sudo ./tests/test.sh --executable` or full suite depending on scope |
| Shared fixtures / `main.py` / cross-cutting changes | Start with the most relevant targeted tests | Always finish with `sudo ./tests/test.sh` before handoff |

For debugging:

```bash
cd tests && sudo python3 -m pytest -v -s --tb=long -k "test_name"
docker ps -a
```

## Regression-aware implementation rules

- Extend the current flow before adding a new abstraction.
- Keep behavior compatible with existing queue priority and cancellation semantics.
- Preserve cleanup paths for spawned profilers/processes.
- Respect root-only / privileged behavior in tests and runtime code.
- Avoid changing `tests/conftest.py` unless multiple tests truly need the new fixture or environment behavior.
- If you touch a large hotspot file, explain why a smaller module was not sufficient.

## Architecture-aware review checklist

- [ ] The spec was mapped to an existing flow before code was added.
- [ ] No duplicate control path was introduced for heartbeat/command handling.
- [ ] `CommandManager` priority still behaves as `stop > adhoc > continuous`.
- [ ] Idempotency and completion reporting still make sense.
- [ ] New code uses the smallest reasonable seam instead of broad rewrites in `main.py`, `java.py`, or `conftest.py`.
- [ ] User-visible changes update docs/help text if needed.
- [ ] Targeted tests were run first, and broader regression coverage was added when the change touched shared code.
- [ ] The final test plan clearly separates unit-ish targeted tests from heavier end-to-end validation.

## When to stop and rethink

Stop and revisit the plan if:

- the design spec seems to require a new control path even though heartbeat/queueing already covers it
- the change starts spreading across `main.py`, multiple profilers, and `conftest.py` at once
- the only way to make progress is to skip the repo's existing test harness
- targeted tests cannot be identified from the changed architecture area

In those cases, narrow the design, split the change, or document why a larger refactor is unavoidable.
