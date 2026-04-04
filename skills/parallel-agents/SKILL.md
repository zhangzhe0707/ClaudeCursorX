# Parallel Agent Orchestration — 多 Agent 并行编排技能

> 借鉴 claude-code-rust 的 AgentExecutor + ForkedAgent 并行模型，
> 以及 Claude Code feature-dev / code-review / pr-review-toolkit 的多 Agent 工作流

Use this skill when a task benefits from parallel analysis, such as code review from multiple dimensions, architecture comparison, or broad codebase exploration. Provides patterns for launching and orchestrating multiple subagents.

## Core Patterns

### Pattern 1: Parallel Exploration (2-3 agents)

Use when you need to understand a codebase from multiple angles:

```
Launch in parallel (single message, multiple Task calls):
  Agent A (explore): "Find similar features and trace implementation patterns"
  Agent B (explore): "Map high-level architecture, abstractions, and data flow"  
  Agent C (explore): "Analyze testing patterns and extension points"

After all return:
  → Read key files identified by each agent
  → Synthesize findings into a comprehensive summary
```

### Pattern 2: Parallel Review (3-4 agents)

Use when reviewing code changes from multiple dimensions:

```
Launch in parallel:
  Agent 1 (code-reviewer): Focus on bugs, logic errors, security
  Agent 2 (code-reviewer): Focus on project conventions and DRY
  Agent 3 (code-reviewer): Focus on error handling and edge cases
  Agent 4 (security-reviewer): Focus on security vulnerabilities

After all return:
  → Merge findings, deduplicate, sort by severity
  → Filter out false positives (confidence < 80%)
  → Present consolidated review
```

### Pattern 3: Parallel Architecture Design (2-3 agents)

Use when designing features with multiple possible approaches:

```
Launch in parallel:
  Agent A (architect): "Design minimal-change approach — smallest diff, maximum reuse"
  Agent B (architect): "Design clean-architecture approach — maintainability first"
  Agent C (architect): "Design pragmatic-balance approach — speed + quality"

After all return:
  → Compare approaches: file changes, complexity, trade-offs
  → Form recommendation with reasoning
  → Present to user for decision
```

### Pattern 4: Validate-then-Act (serial + parallel)

**CRITICAL — This is the most important pattern. Always use it for code review.**

Use when issues need verification before acting. This prevents false positives from eroding trust.

```
Step 1 — Discovery (parallel):
  Launch N agents to independently find issues
  Each agent returns: [{issue, file, line, confidence, reason}]

Step 2 — Validation (parallel):
  For EACH issue with confidence < 95%:
    Launch a validation agent with:
      - The issue description
      - The relevant code context (file:line ± 20 lines)
      - The PR/change description
    Agent checks: "Is this truly a bug/violation, or a false positive?"
    Returns: {validated: true/false, confidence_adjusted, explanation}

Step 3 — Filtering (serial):
  Remove all issues where validation returned validated=false
  Remove all issues where confidence_adjusted < 80%
  This is the HIGH SIGNAL issue list

Step 4 — Presentation:
  Group by severity (Critical > Important > Suggestion)
  For each issue: file:line, description, fix suggestion
  If no issues remain: "No issues found. Checked for [dimensions]."
```

**Why this matters**: Without validation, parallel review agents tend to produce
10-30% false positives. The validation step typically removes 40-60% of flagged
issues, leaving only genuine problems. This is adapted from Claude Code's
code-review plugin which uses this exact 2-pass pattern.

## Agent Selection Guide

| Task | Subagent Type | Model |
|------|--------------|-------|
| Quick file search | `explore` | `fast` |
| Deep code analysis | `generalPurpose` | default |
| Architecture design | `architect` | default |
| Code review | `code-reviewer` | default |
| Bug investigation | `debugger` | default |
| Security audit | `security-reviewer` | default |
| Isolated experiment | `best-of-n-runner` | default |

## Key Rules

1. **Always launch parallel agents in a single message** — multiple Task tool calls in one response
2. **Each agent gets full context** — include relevant findings, PR title, feature description
3. **Star topology** — agents don't talk to each other; the orchestrator (you) merges results
4. **Deduplicate** — multiple agents may find the same issue; consolidate before presenting
5. **Wait for all** — don't act on partial results unless explicitly asked

## Anti-patterns

- Don't launch agents for trivial tasks (single file, simple question)
- Don't launch more than 4 parallel agents (diminishing returns + cost)
- Don't skip the merge step — raw agent output is too verbose for the user
- Don't use `fast` model for deep analysis tasks — it lacks reasoning depth
