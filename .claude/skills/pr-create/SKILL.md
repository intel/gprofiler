---
name: pr-create
description: Create a well-formatted pull request for gProfiler. Use when the user wants to submit changes, create a PR, or push their work.
disable-model-invocation: true
allowed-tools: Bash(git *) Bash(gh *)
---

## Create gProfiler Pull Request

### Current Branch Status

```!
echo "Branch: $(git branch --show-current)"
echo "Base: master"
echo ""
echo "Commits to include:"
git log master..HEAD --oneline 2>/dev/null || echo "No commits ahead of master"
echo ""
echo "Files changed:"
git diff --stat master..HEAD 2>/dev/null || git diff --stat HEAD
```

### PR Checklist

Before creating PR, verify:
- [ ] Code formatted: `./lint.sh` passes
- [ ] Type hints: `mypy .` passes
- [ ] Tests pass locally (if applicable)
- [ ] Commit messages are descriptive
- [ ] No secrets or credentials in diff

### PR Template

**Title format:** `<type>: <short description>`
- Types: `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`

**Body sections:**
```markdown
## Summary
Brief description of what this PR does.

## Changes
- Bullet points of specific changes

## Testing
How the changes were tested.

## Related Issues
Fixes #123 (if applicable)
```

### Instructions

1. Review the commits and changes above
2. Generate appropriate PR title and description
3. Run: `gh pr create --title "..." --body "..."`
4. If tests need to run: `gh pr checks --watch`

### Example Commands

```bash
# Create PR with editor
gh pr create

# Create PR inline
gh pr create --title "fix: handle missing perf binary gracefully" --body "..."

# Create draft PR
gh pr create --draft --title "wip: add Go profiler support"

# Push and create PR
git push -u origin $(git branch --show-current) && gh pr create
```
