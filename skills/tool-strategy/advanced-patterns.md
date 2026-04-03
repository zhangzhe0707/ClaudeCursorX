# Advanced Tool Strategy Patterns

Reference for complex tool usage scenarios. Read when facing unfamiliar
tool behaviors or multi-tool coordination challenges.

## Complex Search Workflows

### Finding all usages of a function (cross-reference)

```
Step 1: Find the definition
  Grep("function targetName|const targetName|export.*targetName", glob: "*.ts")
  → Identify the source file

Step 2: Find all importers
  Grep("import.*targetName|require.*targetName", output_mode: "files_with_matches")
  → List of consumer files

Step 3: Find dynamic usages (string references, reflection)
  Grep("'targetName'|\"targetName\"|`targetName`")
  → Dynamic references that import search misses
```

### Understanding a call chain

```
Step 1: Find the entry point
  Grep("functionA\\(", path: "src/") → where functionA is called

Step 2: Read functionA → see it calls functionB
  Grep("functionB\\(", path: "src/") → where functionB is called

Step 3: Continue until you reach the leaf
  (Usually 2-4 hops is enough to understand the flow)
```

## Shell Command Patterns

### Long-running processes (dev servers, watchers)

```
Shell(command: "npm run dev", block_until_ms: 0)
→ Returns immediately, server runs in background

Shell(command: "sleep 3000", block_until_ms: 3500)
→ Wait for a timed command, add buffer to block_until_ms

To check background process:
  Read the terminal file (check the header for pid and running_for_ms)
```

### Build + test pipeline

```
Shell("npm run build", block_until_ms: 60000)  ← wait for build
→ Check exit code
→ If success:
    Shell("npm test -- --testPathPattern='relevant'", block_until_ms: 30000)
→ If build fails:
    Read terminal output for error details
```

### Git workflow

```
Parallel batch (all read-only):
  Shell("git status")
  Shell("git diff --stat")
  Shell("git log --oneline -5")

Sequential (depends on each other):
  Shell("git add -A")
  Shell("git commit -m 'message'")
  Shell("git push")  ← only if user requests
```

## StrReplace Edge Cases

### When old_string appears multiple times
1. Include more context (3-5 lines before and after)
2. Include unique surrounding code (comments, variable names)
3. If truly identical: use replace_all: true only when you want ALL changed

### When the file has been modified since you read it
1. Read the file again (it may have changed via external editor or auto-formatter)
2. Rebuild the StrReplace with current content
3. Watch for auto-formatting (prettier, gofmt) changing whitespace

### Editing generated files
1. Check if the file has a "DO NOT EDIT" header
2. If yes, find and edit the source template instead
3. Re-run the generator after editing the template

## Multi-Tool Coordination

### Create a new module with tests

```
Parallel read (gather context):
  Read("tsconfig.json")           → understand module resolution
  Glob("src/**/*.test.ts")        → understand test file naming convention
  Read("src/existing-module.ts")  → understand code style

Sequential write:
  Write("src/new-module.ts")      → create the module
  Write("src/new-module.test.ts") → create the test
  Shell("npm test -- new-module") → verify
  ReadLints("src/new-module.ts")  → lint check
```

### Migrate an API endpoint

```
Phase 1 (read):
  Parallel: Read old endpoint + Read router config + Read tests

Phase 2 (write):
  Serial:
    StrReplace router config (add new route)
    Write new endpoint file
    StrReplace old endpoint (add deprecation notice)
    StrReplace tests (update to new endpoint)

Phase 3 (verify):
  Shell: run tests
  ReadLints: all changed files
```
