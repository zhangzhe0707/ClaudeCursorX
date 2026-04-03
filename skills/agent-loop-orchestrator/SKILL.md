---
name: agent-loop-orchestrator
description: >-
  Orchestrates complex multi-step coding tasks using a structured loop pattern
  inspired by agentic tool-use loops. Use when the user requests a task that
  requires planning, multi-file changes, iterative tool calls, or any work
  that benefits from a think-act-verify cycle. Activates for requests like
  "implement feature X", "refactor module Y", "debug issue Z", or any
  non-trivial coding task.
---

# Agent Loop Orchestrator

Execute complex tasks through a disciplined loop: **Plan → Act → Observe → Decide → Repeat**.

## Core Loop

For every non-trivial task, follow this cycle:

```
1. PLAN    — Break the task into concrete, ordered steps
2. ACT     — Execute one step (tool call or code change)
3. OBSERVE — Read the result, check for errors / unexpected output
4. DECIDE  — Is the step done? Should I continue, retry, or abort?
5. REPEAT  — Go to step 2 for the next step, or finish
```

### Step 1 — Plan (mandatory before any action)

Before touching any file:

1. **Gather context** — Read relevant files, search for symbols, understand the current state.  
   Batch independent reads in a single message (parallel tool calls).
2. **State the goal** — One sentence describing the desired end state.
3. **List steps** — Use the TodoWrite tool to create a numbered checklist. Each item should be a single, verifiable action.
4. **Identify risks** — Note files that are tricky, tests that might break, or side effects to watch for.

### Step 2 — Act (one step at a time)

- Execute **one logical step** per iteration. Do not batch unrelated writes.
- Mark the current todo as `in_progress` before starting.
- Prefer the smallest correct change. Avoid unnecessary refactoring within the same step.

### Step 3 — Observe (always verify)

After every write action:

- **Read the changed file** to confirm the edit landed correctly.
- **Run lints** (`ReadLints`) on edited files to catch syntax / type errors immediately.
- **Run related tests** if a test runner is available and the change is testable.
- If the tool call failed or returned an error, diagnose before retrying.

### Step 4 — Decide

| Observation | Action |
|---|---|
| Step succeeded, no errors | Mark todo `completed`, proceed to next step |
| Lint / type error introduced | Fix immediately in a follow-up edit, then re-verify |
| Test failure | Diagnose root cause, fix, re-run test |
| Unexpected result | Re-read surrounding context, adjust plan if needed |
| Repeated failure (3+ attempts) | Stop, explain the blocker to the user, ask for guidance |

### Step 5 — Finish

When all todos are completed:

1. Do a final `ReadLints` pass on all edited files.
2. Summarize what was done: files changed, key decisions, anything the user should review.
3. If tests were run, report the results.

## Parallel vs Serial Execution

Follow this heuristic (inspired by Claude Code's tool orchestration):

| Operation type | Strategy |
|---|---|
| Multiple file reads / searches | **Parallel** — batch in one message |
| Multiple independent code changes in different files | **Parallel** — batch if no ordering dependency |
| Writes that depend on each other (A must exist before B imports it) | **Serial** — one at a time |
| A write followed by its verification | **Serial** — write, then read/lint |

## Handling Large Tasks

If a task has more than 8 steps:

1. Group steps into **phases** (e.g., "Phase 1: Data layer", "Phase 2: UI").
2. Complete and verify one phase before starting the next.
3. After each phase, give a brief progress summary.

## Interruption Recovery

If the user interrupts or provides new instructions mid-loop:

1. Pause the current plan.
2. Acknowledge the change.
3. Decide whether to **amend** the existing plan or **restart** with a new one.
4. Update todos accordingly.

## Advanced Patterns

For complex scenarios (dependency-ordered refactoring, full-stack features,
debugging workflows, concurrent fixes), see [patterns.md](patterns.md).

## Anti-Patterns to Avoid

- **Blind bulk edits** — Never apply changes to many files without reading them first.
- **Skipping verification** — Never assume an edit worked; always read-back or lint.
- **Over-planning** — If the task is 1–2 steps, skip the todo list and just do it.
- **Ignoring errors** — Never proceed to the next step when the current one has unresolved errors.
