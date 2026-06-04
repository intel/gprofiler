---
name: review-code
description: Review code changes against gProfiler coding standards. Use when the user asks to review code, check a PR, or validate changes before committing.
---

## gProfiler Code Review Checklist

### Current Changes

!`git diff --stat HEAD 2>/dev/null || echo "No git changes"`

### Hotspots to review carefully

Large shared files deserve extra skepticism because regressions spread quickly:

| File | Approx. lines | Review concern |
|------|---------------|----------------|
| `gprofiler/main.py` | ~1546 | Broad orchestration / CLI changes can affect many runtimes |
| `gprofiler/profilers/java.py` | ~1555 | Complex runtime-specific behavior |
| `tests/conftest.py` | ~708 | Shared fixtures; small changes can break many tests |
| `gprofiler/dynamic_profiling_management/heartbeat.py` | ~354 | Command-control behavior and backend contract |
| `gprofiler/dynamic_profiling_management/command_control.py` | ~233 | Queue semantics and execution ordering |

Ask whether the change could have been made in a smaller seam before accepting edits to these files.

### Coding Standards

#### Python Style
- [ ] Line length ≤ 120 characters
- [ ] Type hints on all public functions
- [ ] Apache 2.0 license header on new files
- [ ] Imports sorted (stdlib → external → local) with trailing comma
- [ ] Uses `get_logger_adapter(__name__)` for logging

#### Security
- [ ] No hardcoded credentials or secrets
- [ ] Input validation on external data
- [ ] Safe subprocess usage (no shell=True with user input)
- [ ] Proper error handling for privileged operations

#### Profiler-Specific
- [ ] Handles process termination gracefully
- [ ] Cleans up resources in `stop()` method
- [ ] Respects `stop_event` for cancellation
- [ ] Handles missing/unavailable profiler tools

#### Architecture-Aware Checks
- [ ] The change extends an existing flow instead of creating a parallel one
- [ ] Dynamic profiling work stays in `gprofiler/dynamic_profiling_management/` unless `main.py` wiring is truly required
- [ ] `CommandManager` behavior still preserves `stop > adhoc > continuous`
- [ ] Idempotency / completion semantics still make sense for command-driven changes
- [ ] Shared fixtures in `tests/conftest.py` changed only if multiple tests truly need new shared infra
- [ ] User-visible CLI or runtime behavior stays backward-compatible unless the change explicitly intends a break

#### Testing
- [ ] Tests added for new functionality
- [ ] The smallest relevant targeted tests were run first
- [ ] Root privileges and runtime requirements were considered
- [ ] Broader regression coverage was added when shared code changed
- [ ] Docker fixtures / existing test harness were used instead of ad-hoc setup

#### Documentation
- [ ] README updated for user-facing changes
- [ ] Docstrings on complex functions
- [ ] CLI help text for new arguments

### Review Instructions

1. Check the diff above for violations
2. Run `./lint.sh --ci` to verify formatting
3. Verify type hints with `mypy .`
4. Look for common issues:
   - Missing error handling in profiler code
   - Unclosed file handles or processes
   - Missing cleanup in exception paths
   - Hardcoded paths that should be configurable
5. If command-control files changed, explicitly review:
   - queue priority
   - pause/resume behavior
   - duplicate-command protection
   - completion / failure reporting
6. If `main.py` or `tests/conftest.py` changed, ask whether a smaller module could have absorbed the change

### Common regression traps

- Putting runtime-specific logic into `main.py` instead of the relevant profiler or dynamic-profiling module
- Introducing a second control path instead of extending heartbeat + queue handling
- Editing shared fixtures for a single test case
- Adding heavyweight full-suite expectations when a narrower targeted test would have validated the change first
