---
name: dependency-update
description: Safely update Python dependencies with security and compatibility checks. Use when the user wants to update packages, fix CVEs, or upgrade dependencies.
allowed-tools: Bash(pip *) Bash(pip3 *) Bash(python3 -m pip *) Bash(safety *) Bash(pip-audit *) Read Edit Grep
---

## gProfiler Dependency Management

**Context from history:** 26% of all commits (41/156) are dependency updates. Security-first approach.

### Current Dependencies

```!
echo "=== requirements.txt (runtime) ==="
head -15 requirements.txt 2>/dev/null || echo "File not found"
echo ""
echo "=== dev-requirements.txt (development) ==="
head -15 dev-requirements.txt 2>/dev/null || echo "File not found"
```

### CVE Fixes from History

| CVE | Package | Fix Commit |
|-----|---------|------------|
| CVE-2025-71176 | pytest | Update pytest to 9.0.3 (#1035) |
| CVE (black) | black | Fix CVE issue from black package (#1023) |
| Security | requests | Update requests to 2.33.0 (#1032) |
| Security | grpcio | Update granulate-utils reference (#989) |

### Security Scanning

```bash
# Using pip-audit (recommended)
pip install pip-audit
pip-audit -r requirements.txt
pip-audit -r dev-requirements.txt

# Using safety
pip install safety
safety check -r requirements.txt

# Using bandit for code security
pip install bandit
bandit -r gprofiler/
```

### Update Workflow

**Step 1: Check for vulnerabilities**
```bash
pip-audit -r requirements.txt
```

**Step 2: Check outdated packages**
```bash
pip list --outdated
```

**Step 3: Update specific package**
```bash
# Check current version
pip show <package>

# Check available versions
pip index versions <package>

# Edit requirements file
# Then test:
pip install -r requirements.txt -r dev-requirements.txt
```

**Step 4: Verify compatibility**
```bash
./lint.sh
mypy .
cd tests && sudo python3 -m pytest -v test_sanity.py
```

### Key Dependencies

| Package | File | Purpose | Update Frequency |
|---------|------|---------|------------------|
| `psutil` | requirements.txt | Process utilities | Stable |
| `requests` | requirements.txt | HTTP client | Security-sensitive |
| `granulate-utils` | requirements.txt | Shared utilities | Internal |
| `pytest` | dev-requirements.txt | Testing | Security-sensitive |
| `black` | dev-requirements.txt | Formatting | CVE history |
| `mypy` | dev-requirements.txt | Type checking | Regular |
| `flake8` | dev-requirements.txt | Linting | Stable |

### Base Image Dependencies

From history, these also get updated:
- Alpine version (v3.22 → v3.23)
- OpenSSL version (v3.0.11)
- zlib version (1.3 → 1.3.1)

Check Dockerfiles:
```bash
grep -E "FROM|apk add|apt-get" container.Dockerfile executable.Dockerfile
```

### Commit Message Pattern

For security updates:
```
Update <package> to <version> to fix CVE-XXXX-XXXXX (#PR)
```

For regular updates:
```
Update <package> to <version> (#PR)
Bump <package> from X.Y.Z to A.B.C (#PR)
```

### Version Pinning Strategy

- **Runtime deps (requirements.txt):** Pin major.minor, allow patch
- **Dev deps (dev-requirements.txt):** Pin exact version for reproducibility
- **Example:**
  ```
  requests>=2.33.0,<3.0
  pytest==9.0.3
  ```

### Testing After Updates

```bash
# Quick validation
./lint.sh
mypy .

# Full test (if significant update)
sudo ./tests/test.sh

# Build test (if build-related dep)
./scripts/build_x86_64_executable.sh
```

### PR Checklist for Dependency Updates

- [ ] CVE number referenced (if security fix)
- [ ] Compatibility verified with Python 3.10+
- [ ] Linters pass
- [ ] Type checking passes
- [ ] Tests pass (or document known issues)
- [ ] No breaking API changes

---

## TODO: Skill Content to Add

- [ ] **Add full dependency list** - Complete list with purposes
- [ ] **Add version constraint explanations** - Why specific versions pinned
- [ ] **Add transitive dependency notes** - Important indirect dependencies
- [ ] **Add security advisory links** - Links to CVE databases
- [ ] **Add test matrix for updates** - What to test for each dep type
- [ ] **Add granulate-utils documentation** - Internal package details
- [ ] **Add Docker base image deps** - Alpine/Ubuntu package lists
- [ ] **Add breaking change indicators** - How to identify risky updates
