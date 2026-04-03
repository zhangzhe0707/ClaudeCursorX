# Language-Specific Quality Checklists

Reference for quality gate checks specific to different languages and
frameworks. Read the relevant section when working in that language.

## TypeScript / JavaScript

### Lint
```
ReadLints on all .ts/.tsx/.js/.jsx files edited
```

### Build
```
Shell: npx tsc --noEmit          (type checking)
Shell: npm run build             (if build script exists)
```

### Test
```
Shell: npx jest --testPathPattern='<pattern>' --no-coverage
Shell: npx vitest run <file>
Shell: npm test -- --grep '<test name>'
```

### Common issues after edits
- Missing imports (added a usage but forgot the import)
- Type mismatches (changed a type in one place, consumers break)
- Async/await mismatch (forgot await on a Promise)
- Default export vs named export confusion

## Python

### Lint
```
Shell: python -m py_compile <file>     (syntax check)
Shell: ruff check <file>               (if ruff is configured)
Shell: mypy <file>                     (if mypy is configured)
```

### Test
```
Shell: python -m pytest <test_file> -x     (stop on first failure)
Shell: python -m pytest -k '<test_name>'   (run specific test)
```

### Common issues
- IndentationError (mixed tabs/spaces after edit)
- ImportError (circular import after refactoring)
- NameError (renamed variable but missed a reference)

## Rust

### Build (always run — Rust is compiled)
```
Shell: cargo check          (fast type checking)
Shell: cargo build          (full build, only if check passes)
```

### Test
```
Shell: cargo test <test_name>     (specific test)
Shell: cargo test -- --nocapture  (with output)
```

### Common issues
- Borrow checker violations after refactoring
- Missing trait implementations
- Lifetime annotation needed

## Go

### Build (always run)
```
Shell: go build ./...      (all packages)
Shell: go vet ./...        (static analysis)
```

### Test
```
Shell: go test ./path/to/package -run TestName
Shell: go test -v ./...    (verbose, all tests)
```

### Common issues
- Unused imports (Go won't compile)
- Exported names must be capitalized
- Error not handled (go vet catches this)

## React / Frontend

### Additional checks beyond TypeScript
```
Shell: npm run lint        (ESLint with React rules)
```

### Common issues after edits
- Missing key prop in list rendering
- Hook rules violation (conditional hooks)
- Stale closure in useEffect/useCallback
- Missing dependency in useEffect deps array

## General Post-Edit Verification

For any language, after a non-trivial change:

```
1. Read the edited file (confirm edit)
2. ReadLints (catch syntax/type errors)
3. If the project has a type-checker:
   Shell: run type-checker
4. If tests exist for the changed code:
   Shell: run relevant tests
5. If all pass → done
6. If any fail → fix and re-verify
```
