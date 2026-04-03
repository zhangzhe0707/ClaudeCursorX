---
name: tool-strategy
description: >-
  Optimizes how tools are selected and invoked for maximum efficiency. Use when
  performing file operations, code searches, shell commands, or any task that
  involves multiple tool calls. Provides strategies for batching reads,
  sequencing writes, and choosing the right tool for the job.
---

# Tool Strategy

Select and sequence tool calls for speed and correctness, modeled after
Claude Code's tool orchestration engine.

## Tool Selection Guide

### Finding Code

| Goal | Best tool | Why |
|---|---|---|
| Find a file by name or glob pattern | `Glob` | Fast filesystem scan, no content reading |
| Find exact text / symbol / regex | `Grep` | ripgrep-powered, fastest for literal matches |
| Understand behavior or architecture | `SemanticSearch` | Meaning-based, good for "how does X work?" |
| Read a known file | `Read` | Direct read, use when you already know the path |

**Decision flow:**

```
Know the file path?
  → YES → Read
  → NO  → Know the exact string?
            → YES → Grep (files_with_matches to find, then Read)
            → NO  → Know the filename pattern?
                      → YES → Glob, then Read
                      → NO  → SemanticSearch
```

### Modifying Code

| Goal | Best tool |
|---|---|
| Replace a specific string in a file | `StrReplace` (precise, safe) |
| Write a new file from scratch | `Write` |
| Run a command (build, test, git) | `Shell` |
| Delete a file | `Delete` |

**Always Read before StrReplace** — you must see the current content to
provide a unique `old_string`.

## Batching Rules

### Batch these (parallel, single message):

- Multiple `Read` calls for different files
- Multiple `Grep` searches for different patterns
- Multiple `Glob` searches for different patterns
- `Read` + `Grep` + `Glob` together (all read-only)
- Multiple `SemanticSearch` queries for **different** questions

### Never batch these (must be sequential):

- `Read` then `StrReplace` on the same file (need content first)
- `StrReplace` then `ReadLints` on the same file (need edit result first)
- `Shell` (build) then `Shell` (test) when test depends on build output
- Two `StrReplace` calls on the same file (second depends on first's result)
- `Write` then `Read` (verify the write landed)

### Conditional batching:

- Multiple `StrReplace` on **different** files → batch if edits are independent
- Multiple `Shell` commands → batch if commands are independent (e.g., `git status` and `npm test`)

## Search Optimization

1. **Start narrow, widen if needed.**  
   Search in a specific directory first (`path` param). Only search the whole repo if the narrow search misses.

2. **Use `files_with_matches` mode first** when you need to find which files contain something, then `Read` the relevant ones.

3. **Prefer `Grep` over `Shell` + `rg`.**  
   The built-in Grep tool is optimized and sandboxed. Don't shell out to ripgrep.

4. **Combine Glob + Read for bulk exploration.**  
   `Glob("src/components/**/*.tsx")` → pick the relevant paths → batch `Read` them all.

## Shell Command Discipline

- **Quote paths** with spaces: `cd "/path with spaces"`
- **Chain dependent commands** with `&&`: `mkdir -p foo && cp bar foo/`
- **Separate independent commands** into parallel Shell calls
- **Set `block_until_ms`** appropriately: short for quick commands, `0` for dev servers
- **Never use `cat`/`head`/`tail`** to read files — use the `Read` tool
- **Never use `sed`/`awk`** to edit files — use `StrReplace`
- **Never use `echo`** to communicate — write your response as text

## Cost-Conscious Patterns

| Pattern | Cost | Better alternative |
|---|---|---|
| Reading a 10,000-line file entirely | High | `Grep` for the relevant section, then `Read` with `offset`/`limit` |
| SemanticSearch for an exact class name | Wasteful | `Grep` with the class name |
| Running a full test suite after a one-line fix | Slow | Run only the relevant test file |
| Reading every file in a directory | High | `Glob` to list, then read only what matters |

## Advanced Patterns

For complex search workflows, shell pipelines, StrReplace edge cases,
and multi-tool coordination, see [advanced-patterns.md](advanced-patterns.md).

## Error Recovery

| Error | Recovery |
|---|---|
| `StrReplace` failed (old_string not unique) | `Read` the file, find the right context, include more surrounding lines |
| `StrReplace` failed (old_string not found) | File may have changed; `Read` it again and retry |
| `Shell` command timed out | Check if the process is still running (read terminal file), increase `block_until_ms`, or kill and retry |
| `Grep` returned too many results | Add a `glob` filter or narrow the `path` |
