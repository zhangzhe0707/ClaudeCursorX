---
name: quality-gate
description: >-
  Enforces quality checks before declaring a task complete. Use after making
  code changes, implementing features, fixing bugs, or any task where
  correctness matters. Runs lint checks, verifies edits, and validates
  against requirements. Inspired by Claude Code's stopHooks mechanism.
---

# Quality Gate

Never declare a task done without verification. This skill defines the
mandatory checks that run before completion, mirroring Claude Code's
stopHooks pipeline.

## Post-Change Checklist

After every code change, run these gates in order:

### Gate 1 — Edit Verification

- **Read back** every file you edited to confirm the change is correct.
- Verify no accidental deletions, duplicate lines, or broken indentation.

### Gate 2 — Lint Check

- Run `ReadLints` on all edited files.
- **If new errors were introduced**: fix them immediately. Do not proceed.
- **If pre-existing errors exist**: note them but don't fix unless asked.

### Gate 3 — Build Check (when applicable)

If the project has a build step and changes could break compilation:

```
Shell: npm run build / cargo build / go build / etc.
```

- Only needed for compiled languages or projects with a build pipeline.
- Skip for pure scripting changes (Python, shell scripts).

### Gate 4 — Test Check (when applicable)

If tests exist for the changed code:

```
Shell: run the relevant test file or test suite
```

- Prefer running **only related tests**, not the full suite.
- If the test fails, diagnose and fix before proceeding.
- If no tests exist for the changed code, mention this in the summary.

### Gate 5 — Requirements Validation

Compare the result against the original request:

- [ ] Does the change accomplish what the user asked for?
- [ ] Are edge cases handled?
- [ ] Is the code style consistent with the surrounding codebase?
- [ ] Are there any unintended side effects?

## When to Run Which Gates

| Change type | Gate 1 | Gate 2 | Gate 3 | Gate 4 | Gate 5 |
|---|---|---|---|---|---|
| Single file edit | Required | Required | Skip | If tests exist | Required |
| Multi-file feature | Required | Required | If compiled | Required | Required |
| Bug fix | Required | Required | If compiled | **Must** run | Required |
| Refactoring | Required | Required | If compiled | Required | Required |
| Config/docs only | Required | Skip | Skip | Skip | Required |

## Failure Handling

### Lint failure

```
1. Read the lint error
2. StrReplace to fix
3. ReadLints again
4. Repeat until clean (max 3 attempts)
5. If stuck → ask the user
```

### Test failure

```
1. Read the test output carefully
2. Identify root cause (your change vs pre-existing)
3. If your change caused it → fix the code (not the test)
4. Re-run the test
5. If the test itself is wrong → explain to the user before modifying
```

### Build failure

```
1. Read the compiler error
2. Fix type errors or missing imports
3. Rebuild
4. If the error is in code you didn't touch → report to user
```

## Completion Summary Template

When all gates pass, report:

```
## Done

**Changes:**
- `path/to/file1.ts` — [what changed and why]
- `path/to/file2.ts` — [what changed and why]

**Verification:**
- Lint: clean (or N pre-existing warnings)
- Tests: passed (or N/A if no relevant tests)
- Build: passed (or N/A)

**Notes:**
- [Anything the user should review or be aware of]
```

## Language-Specific Checks

For TypeScript, Python, Rust, Go, React specific lint/build/test commands
and common post-edit issues, see [checklists.md](checklists.md).

## Abort Conditions

Stop and inform the user if:

- **3 consecutive failures** on the same gate for the same issue.
- **Circular dependency** — fixing one error introduces another.
- **Scope creep** — the fix requires changes far beyond the original request.
- **Missing information** — you need clarification to proceed safely.
