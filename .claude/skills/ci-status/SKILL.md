---
name: ci-status
description: Check CI pipeline status and troubleshoot failures. Use when the user wants to check build status, see why CI failed, or monitor workflow runs.
allowed-tools: Bash(gh *) Bash(git *)
---

## gProfiler CI/CD Status

### Current PR/Branch Status

```!
BRANCH=$(git branch --show-current)
echo "Branch: $BRANCH"
echo ""
# Try to get PR status
gh pr view --json state,statusCheckRollup,url 2>/dev/null || echo "No PR found for this branch"
```

### CI Workflows

| Workflow | Triggers | Purpose |
|----------|----------|---------|
| `linters.yml` | PR, push | Python/Shell/Dockerfile linting |
| `build-test-deploy.yml` | PR, push, schedule | Build & test executables/containers |
| `codeql.yml` | PR, push, schedule | Security scanning |
| `build-base-images.yml` | Manual | Base Docker image builds |

### Check Commands

```bash
# View PR checks
gh pr checks

# Watch checks until complete
gh pr checks --watch

# List recent workflow runs
gh run list --limit 10

# View specific run details
gh run view <run-id>

# View failed run logs
gh run view <run-id> --log-failed

# Re-run failed jobs
gh run rerun <run-id> --failed
```

### Common CI Failures

#### Linting Failures
```bash
# Fix locally
./lint.sh          # Auto-format
./lint.sh --ci     # Check mode (what CI runs)
mypy .             # Type check
```

#### Build Failures
- Check Docker layer caching
- Verify base image availability
- Check for network/download issues

#### Test Failures
```bash
# Run specific failing test locally
sudo python3 -m pytest tests/test_<name>.py -v -k "test_name"
```

### Instructions

1. Check current CI status with commands above
2. If failed, identify which job failed
3. Fetch logs with `gh run view <id> --log-failed`
4. Suggest fixes based on error messages
5. Help re-run if it was a flaky failure

---

## TODO: Skill Content to Add

- [ ] **Add workflow file documentation** - Explain each workflow file
- [ ] **Add job dependency diagram** - Visual CI pipeline flow
- [ ] **Add runner specifications** - What runs on which runner type
- [ ] **Add common failure patterns** - Categorized CI failure examples
- [ ] **Add cache configuration** - How Docker layer caching works
- [ ] **Add artifact retention** - Where build artifacts are stored
- [ ] **Add required checks list** - Which checks must pass for merge
- [ ] **Add workflow dispatch guide** - How to manually trigger workflows
