# Code Review — 多维度代码审查技能

> 适配自 Claude Code 官方 code-review 插件的多模型分级审查策略

Use this skill when the user asks to review a pull request, review recent code changes, or perform a code audit. Provides a structured, multi-dimensional review workflow.

## Review Workflow

### Step 1: Scope Identification

确定审查范围：
- Run `git diff --name-only` to identify changed files
- Run `git diff --stat` to see change volume
- Run `git log --oneline -5` to understand recent context

### Step 2: Pre-screening

Quick check — skip review if:
- Changes are trivial (only whitespace, comments, or version bumps)
- PR is still in draft / WIP state

### Step 3: Multi-dimensional Review

Review each dimension with **confidence scoring** (0-100). Only report issues with **confidence >= 80**.

#### Dimension A: Bug Detection (Critical)
- Logic errors, off-by-one, null/undefined access
- Race conditions, deadlocks, memory leaks
- Missing error handling on I/O operations
- Security vulnerabilities (injection, XSS, secrets in code)

#### Dimension B: CLAUDE.md / Project Conventions
- Check project-level rules (CLAUDE.md, .cursor/rules, eslintrc, etc.)
- Import patterns, naming conventions, framework idioms
- Test coverage requirements

#### Dimension C: Code Quality
- Significant code duplication
- Missing critical error handling
- Accessibility problems
- Performance issues (N+1 queries, O(n²) where O(n) is trivial)

### Step 4: Validation

For each issue found, **validate before reporting**:
- Is this a real issue or a false positive?
- Is this a pre-existing issue (not introduced by this change)?
- Would a senior engineer flag this?

**DO NOT flag:**
- Pre-existing issues not introduced by this change
- Style nitpicks that a linter would catch
- General quality concerns unless explicitly required by project rules
- Issues silenced by explicit comments (lint ignore, etc.)

### Step 5: Output Format

```markdown
## Code Review Summary

### Critical Issues (must fix) 🔴
- [file:line] Description — Confidence: X%

### Important Issues (should fix) 🟡
- [file:line] Description — Confidence: X%

### Suggestions (nice to have) 🟢
- [file:line] Description

### Strengths
- What's well-done in this change

### Verdict
[ ] Ready to merge
[ ] Needs fixes (list what)
```

## Key Principles

1. **Quality over quantity** — fewer high-confidence issues beat many uncertain ones
2. **Actionable** — every issue must include file:line and a concrete fix suggestion
3. **Respectful** — acknowledge good work, not just problems
4. **Focused on the diff** — review what changed, not the entire codebase
