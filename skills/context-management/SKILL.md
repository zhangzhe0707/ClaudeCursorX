---
name: context-management
description: >-
  Manages conversation context efficiently to prevent token overflow and
  maintain focus. Use when working on long tasks, large codebases, or when
  the conversation grows beyond comfortable context window limits. Provides
  strategies for progressive context loading, information compression, and
  selective file reading.
---

# Context Management

Keep the context window lean and focused, inspired by Claude Code's
autoCompact and microCompact strategies.

## Principles

1. **Load context lazily** — Don't read files until you need them.
2. **Read selectively** — Use `offset`/`limit` or search before reading whole files.
3. **Summarize and discard** — After extracting what you need from a large read, work from the summary.
4. **Batch related reads** — One parallel batch is cheaper than five sequential reads.

## Progressive Context Loading

### Phase 1 — Orientation (lightweight)

Before diving into code, build a mental map with cheap operations:

```
1. Glob("src/**/*") → understand directory structure
2. Read("package.json") → understand dependencies and scripts
3. Read("README.md", limit: 50) → understand project purpose
```

### Phase 2 — Targeted Investigation

Once you know where to look:

```
1. Grep("functionName", path: "src/relevant-dir/") → find exact locations
2. Read specific files with offset/limit around the matches
```

### Phase 3 — Deep Dive (only when necessary)

```
1. Read full files only when editing them
2. Use SemanticSearch for architectural understanding
```

## Large File Strategies

| File size | Strategy |
|---|---|
| < 200 lines | Read entirely |
| 200–1000 lines | Grep for the relevant section, then Read with offset/limit |
| > 1000 lines | SemanticSearch scoped to the file, or Grep + targeted Read |

**Never** read a file > 500 lines without a reason. If you need to understand
its structure, Grep for key symbols (class/function/export definitions) first.

## Information Density Techniques

### When gathering context, prioritize:

1. **Type signatures and interfaces** over implementations (they're denser)
2. **Export statements** to understand a module's public API
3. **Test file names** to understand what's covered
4. **Import statements** to understand dependencies

### Search patterns for fast orientation:

```
Grep("^export ", path: "src/module/") → public API surface
Grep("^import ", path: "src/file.ts") → dependency graph
Grep("class |interface |type |enum ", path: "src/types/") → type definitions
Grep("describe\\(|it\\(|test\\(", path: "tests/") → test coverage map
```

## Multi-File Edit Strategy

When editing multiple files (e.g., a feature spanning 5+ files):

1. **Read all target files first** (parallel batch).
2. **Plan all edits** before making any changes.
3. **Make edits** in dependency order (base types → implementations → consumers).
4. **Verify as you go** — lint after each file, don't wait until the end.

This prevents the context from being polluted with stale file contents
that were read before an earlier edit changed them.

## When Context Gets Long

If the conversation has been running for many steps:

1. **Summarize progress** — State what's been done and what remains.
2. **Avoid re-reading** files you've already read and haven't changed.
3. **Use TodoWrite** to track state externally — the todo list persists across context boundaries.
4. **Trust your earlier analysis** — Don't re-search for things you already found.

## Large Codebase Navigation

For first-contact with unfamiliar large codebases (100+ files),
monorepo navigation, and context budget planning,
see [large-codebase-guide.md](large-codebase-guide.md).

## Exploration Delegation

For broad codebase exploration tasks (e.g., "how does auth work in this project?"):

- Use the `Task` tool with `subagent_type="explore"` to delegate the search.
- This preserves main conversation context for the actual work.
- The subagent returns a focused summary.

For targeted lookups (known file/symbol):

- Do it directly — don't spawn a subagent for a single Grep.
