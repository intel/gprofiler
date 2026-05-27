---
name: version-release
description: Release a new version of gProfiler. Use when the user wants to bump the version, create a release, or tag a new version.
disable-model-invocation: true
allowed-tools: Bash(git *) Bash(gh *) Read Edit
---

## gProfiler Version Release Process

**Context from history:** 22 version bumps in 3 years (1.34.0 → 1.56.2)

### Current Version

```!
grep -E "^__version__" gprofiler/__init__.py 2>/dev/null || echo "Version not found"
```

### Release Steps

**Step 1: Update Version**

Edit `gprofiler/__init__.py`:
```python
__version__ = "X.Y.Z"
```

**Step 2: Create PR**
```bash
git checkout -b bump-version-X.Y.Z
git add gprofiler/__init__.py
git commit -m "Bump version to X.Y.Z"
git push -u origin bump-version-X.Y.Z
gh pr create --title "Bump version to X.Y.Z" --body "Version bump for release X.Y.Z"
```

**Step 3: Merge PR**

Wait for CI to pass, then merge.

**Step 4: Create Tag**
```bash
git checkout master
git pull
git tag vX.Y.Z
git push origin vX.Y.Z
```

### CI/CD on Tag Push

When a tag is pushed, `.github/workflows/build-test-deploy.yml`:
1. Builds executables (x86_64, aarch64)
2. Builds Docker containers
3. Runs full test suite
4. Deploys to Docker Hub (`intel/gprofiler:X.Y.Z`, `intel/gprofiler:latest`)
5. Creates GitHub release with executables

### Version Numbering

Semantic versioning: `MAJOR.MINOR.PATCH`
- **MAJOR:** Breaking changes
- **MINOR:** New features, backward compatible
- **PATCH:** Bug fixes, security updates

### Recent Version History

From git history:
```
1.56.2 - Current
1.56.1
1.56.0
...
1.34.0 - ~3 years ago
```

### Pre-Release Checklist

- [ ] All tests pass
- [ ] Linters pass
- [ ] CHANGELOG updated (if maintained)
- [ ] README updated for new features
- [ ] Version number follows semantic versioning
- [ ] No pending security issues

### Hotfix Release

For urgent fixes:
```bash
# Branch from tag
git checkout vX.Y.Z
git checkout -b hotfix-X.Y.Z+1

# Make fix
# ...

# Update version
# Edit gprofiler/__init__.py

# Create PR to master AND tag
git push -u origin hotfix-X.Y.Z+1
gh pr create --title "Hotfix: <description>" --body "..."
```

### Docker Hub Images

After release:
- `intel/gprofiler:latest`
- `intel/gprofiler:X.Y.Z`

### GitHub Release

Created automatically with:
- `gprofiler_x86_64` executable
- `gprofiler_aarch64` executable
- Release notes (from tag message or PR description)

---

## TODO: Skill Content to Add

- [ ] **Add version history table** - Recent versions with highlights
- [ ] **Add release artifact checksums** - How to verify downloads
- [ ] **Add Docker Hub tag convention** - Tagging strategy explanation
- [ ] **Add rollback procedure** - How to revert a bad release
- [ ] **Add release announcement template** - Standard release notes format
- [ ] **Add compatibility notes** - What to check for breaking changes
- [ ] **Add post-release verification** - Steps to verify successful release
- [ ] **Add emergency hotfix process** - Detailed hotfix workflow
