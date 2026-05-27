---
name: review-code
description: Review code changes against gProfiler coding standards. Use when the user asks to review code, check a PR, or validate changes before committing.
---

## gProfiler Code Review Checklist

### Current Changes

!`git diff --stat HEAD 2>/dev/null || echo "No git changes"`

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

#### Testing
- [ ] Tests added for new functionality
- [ ] Tests run with root privileges considered
- [ ] Docker fixtures used for runtime testing

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

---

## TODO: Skill Content to Add

- [ ] **Add code pattern examples** - Good vs bad code patterns
- [ ] **Add profiler-specific review rules** - Per-profiler considerations
- [ ] **Add performance review criteria** - Overhead and efficiency checks
- [ ] **Add security checklist expansion** - More security review items
- [ ] **Add test coverage requirements** - Minimum coverage expectations
- [ ] **Add backwards compatibility checks** - CLI and API stability
- [ ] **Add resource cleanup patterns** - File handle and process cleanup
- [ ] **Add logging review guidelines** - What and how to log
