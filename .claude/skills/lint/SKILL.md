---
name: lint
description: Run all code quality checks and auto-fix formatting issues. Use when the user wants to lint, format, or check code style before committing.
allowed-tools: Bash(isort *) Bash(black *) Bash(flake8 *) Bash(mypy *) Bash(./lint.sh *) Bash(./shell_lint.sh) Bash(./dockerfile_lint.sh) Bash(pip *) Bash(bandit *)
---

## gProfiler Code Quality Tools

**Project standards:** Line length 120, Python 3.10+, strict mypy

### Quick Commands

```bash
# Run all Python linters with auto-fix
./lint.sh

# CI mode (check only, no auto-formatting)
./lint.sh --ci

# Shell script linting (uses Docker)
./shell_lint.sh

# Dockerfile linting (uses Docker)
./dockerfile_lint.sh
```

### Individual Tools

| Tool | Command | Purpose |
|------|---------|---------|
| **isort** | `isort --settings-path .isort.cfg .` | Import sorting (line_length: 120) |
| **black** | `black --line-length 120 .` | Code formatting |
| **flake8** | `flake8 --config .flake8 .` | Style linting |
| **mypy** | `mypy .` | Type checking (strict mode) |
| **shellcheck** | `./shell_lint.sh` | Shell script linting |
| **hadolint** | `./dockerfile_lint.sh` | Dockerfile linting |

### Configuration Files

- `.isort.cfg` - Import sorting config (line_length=120, multi_line_output=3)
- `.flake8` - Flake8 config (max_line_length=120)
- `mypy.ini` - Type checking (strict mode, no_implicit_optional=False)

### Common Issues from History

**1. Import Order (isort)**
```python
# Correct format (3-line grouped with trailing comma)
import logging
import os
from pathlib import Path

from granulate_utils import ...
from psutil import ...

from gprofiler.utils import ...
```

**2. Line Length (120 chars)**
- Use parentheses for line continuation
- Break long strings with implicit concatenation

**3. Type Hints (mypy)**
- All public functions need type hints
- Use `Optional[T]` for nullable parameters
- Use `from __future__ import annotations` for forward refs

**4. Black Formatting**
- CVE-2024 issue with black package fixed in recent versions
- Ensure dev-requirements.txt has latest black

### Pre-Commit Checklist

1. `./lint.sh` - Auto-fix formatting
2. `mypy .` - Check types
3. `./shell_lint.sh` - If shell scripts modified
4. `./dockerfile_lint.sh` - If Dockerfiles modified

### CI Workflow

The `.github/workflows/linters.yml` runs:
1. Python linters (isort, black, flake8)
2. mypy type checking
3. shellcheck via Docker
4. hadolint via Docker

### Bandit Security Linting

For security-sensitive changes:
```bash
pip install bandit
bandit -r gprofiler/
```

Note: B404 (subprocess import) is a known finding - use subprocess safely.

---

## TODO: Skill Content to Add

- [ ] **Add example error messages** - Common linting errors and how to fix them
- [ ] **Add IDE integration section** - VS Code, PyCharm settings for auto-formatting
- [ ] **Add mypy error explanations** - Common mypy errors specific to gProfiler patterns
- [ ] **Add flake8 ignore patterns** - Document which rules are ignored and why
- [ ] **Add shell lint examples** - Common shellcheck warnings in build scripts
- [ ] **Add Dockerfile lint examples** - hadolint warnings and fixes
- [ ] **Add CI failure troubleshooting** - How to debug linting failures in CI
- [ ] **Add git hooks setup** - Optional pre-commit hook configuration
