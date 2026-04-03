# Large Codebase Navigation Guide

Reference for working with codebases that have 100+ files or 50K+ lines.
Read this file when entering an unfamiliar large codebase.

## First-Contact Protocol

When you've never seen this codebase before:

```
Batch 1 (parallel — structure):
  Glob("*")                          → top-level files
  Glob("src/**", head_limit: 50)     → source structure
  Read("package.json")               → or Cargo.toml, go.mod, pyproject.toml
  Read("README.md", limit: 80)       → project overview

Batch 2 (parallel — architecture):
  Grep("^export ", glob: "src/index.*")     → main exports
  Grep("^import ", path: "src/main.*")      → main dependencies
  Glob("src/**/*.test.*", head_limit: 20)   → test structure
```

This gives you a complete mental map in 2 round-trips (~3 seconds).

## Directory Significance Heuristics

| Directory name | Likely contents | Read priority |
|---|---|---|
| src/, lib/ | Core source code | High |
| types/, interfaces/ | Type definitions | High (dense info) |
| utils/, helpers/ | Utility functions | Medium |
| tests/, __tests__/ | Test files | Low (read when debugging) |
| config/, .config/ | Configuration | Read when debugging build/env |
| scripts/ | Build/deploy scripts | Low |
| docs/ | Documentation | Low (prefer reading code) |
| node_modules/, vendor/ | Dependencies | Never read |
| dist/, build/, out/ | Build output | Never read |

## Monorepo Navigation

For monorepos with multiple packages:

```
1. Glob("packages/*/package.json") → list all packages
2. Read the root package.json → understand workspace config
3. For each relevant package:
   Grep("dependencies", path: "packages/X/package.json") → inter-package deps
4. Build a dependency graph mentally before diving into code
```

## Symbol Tracking Across Files

When a type or function is used across 10+ files:

```
Don't read all 10 files. Instead:

1. Find the definition (Grep for "export.*SymbolName")
2. Find the 2-3 most important consumers:
   - The main entry point that uses it
   - The test that validates it
   - The most complex consumer
3. Read only those 3-4 files
4. Infer the rest from patterns
```

## Context Budget Planning

Before starting a large task, estimate context cost:

| Resource | Typical tokens | Budget guidance |
|---|---|---|
| Short file (< 100 lines) | ~500 | Read freely |
| Medium file (100-300 lines) | ~1,500 | Read if needed |
| Large file (300-1000 lines) | ~5,000 | Read selectively |
| Huge file (1000+ lines) | ~10,000+ | Never read fully |
| Grep result (20 matches) | ~800 | Acceptable |
| Glob result (50 files) | ~400 | Acceptable |
| Shell output (build log) | varies | Skim, don't memorize |

**Rule of thumb**: Keep total "files read in this session" under 20.
If you need more, delegate exploration to a subagent.

## Stale Context Detection

After 10+ conversation turns, some of your earlier context may be stale:

Signs of stale context:
- StrReplace fails with "old_string not found"
- Lint errors mention lines that don't match what you read
- Import errors for symbols you know exist

Recovery:
1. Re-read the specific file that seems stale
2. Don't re-read everything — targeted refresh only
3. If many files seem stale, consider asking the user to start a new session
