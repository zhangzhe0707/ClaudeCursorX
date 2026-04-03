# Advanced Agent Loop Patterns

Reference material for complex scenarios. Read this file only when facing
a situation not covered by the core SKILL.md instructions.

## Pattern 1: Dependency-Ordered Multi-File Refactoring

When renaming a symbol/type across many files:

```
1. Grep("OldName", output_mode: "files_with_matches") → get all affected files
2. Read all affected files (parallel batch)
3. Classify files:
   - TYPE_DEFINITIONS: where OldName is defined (types, interfaces)
   - IMPLEMENTATIONS: where OldName is used in logic
   - CONSUMERS: where OldName is imported/referenced
   - TESTS: test files for OldName
4. Edit in order: TYPE_DEFINITIONS → IMPLEMENTATIONS → CONSUMERS → TESTS
5. Lint after each category, not after each file
6. Run tests at the end
```

## Pattern 2: Feature Implementation Across Layers

For full-stack features (e.g., add API endpoint + UI):

```
Phase 1 — Data Layer
  1. Define types/interfaces
  2. Implement data access / API handler
  3. Lint + unit test

Phase 2 — Business Logic
  1. Implement service/controller logic
  2. Wire up routes/exports
  3. Lint + integration test

Phase 3 — UI Layer
  1. Create/modify components
  2. Connect to data layer
  3. Lint + visual check instructions

Phase 4 — Cross-Cutting
  1. Error handling
  2. Config/environment variables
  3. Documentation updates
```

## Pattern 3: Debugging a Failing Test

```
1. Run the failing test → capture full output
2. Read the test file → understand what it expects
3. Read the source file being tested → understand current behavior
4. Identify the gap between expected and actual
5. Decide: fix the source (bug) or fix the test (outdated expectation)
6. Make the fix
7. Re-run the test → confirm it passes
8. Run related tests → confirm no regressions
```

## Pattern 4: Exploratory Refactoring (Unknown Scope)

When the user says "clean up module X" but scope is unclear:

```
1. Use Task(subagent_type="explore") to survey the module
2. Summarize findings to the user
3. Propose specific refactoring steps
4. Wait for user approval before proceeding
5. Execute approved steps with normal loop
```

## Pattern 5: Concurrent Independent Fixes

When fixing multiple unrelated issues in one request:

```
1. Create separate todo groups per issue
2. For each issue:
   a. Read relevant files
   b. Make the fix
   c. Verify (lint/test)
   d. Mark complete
3. Issues sharing no files can be interleaved
4. Issues sharing files must be serialized
```

## Pattern 6: Large-Scale Code Generation

When generating boilerplate across many files (e.g., CRUD endpoints):

```
1. Generate one complete example file first
2. Show the user for approval
3. If approved, batch-generate the remaining files
4. Lint all generated files
5. Generate corresponding test files
```

## Anti-Pattern Recovery

### "I edited the wrong file"
1. Read the file to see current state
2. If the original content is in your conversation history, StrReplace to restore
3. If not, check git: Shell("git diff path/to/file") → Shell("git checkout path/to/file")
4. Then edit the correct file

### "My edit broke something unrelated"
1. Shell("git diff") to see all changes
2. Identify which change caused the break
3. Revert that specific change
4. Re-approach with a more targeted fix

### "The test passes but the code is wrong"
1. Read the test carefully — it may be testing the wrong behavior
2. Explain to the user: "The test passes but I believe it's testing X when it should test Y"
3. Get confirmation before modifying the test

## Pattern 7: Deep Review Mode (Ultra-Review)

When conducting thorough code review (security + logic + architecture):

```
Phase 1 — Surface Scan
  1. git diff --stat → list all changed files
  2. Classify by risk: auth/crypto/input → CRITICAL; business logic → HIGH; UI/docs → LOW
  3. review_diff(stat_only=True) for overview

Phase 2 — Deep Analysis (CRITICAL files only)
  1. Read each file fully
  2. For each function/method changed:
     - Trace all inputs: where do they come from? Are they validated?
     - Trace all outputs: where do they go? Could they leak data?
     - Check error paths: what happens on failure?
  3. Use analyze_impact() to check downstream effects
  4. Use symbol_references() to find all call sites

Phase 3 — Security Audit
  Delegate to security-reviewer subagent with the file list from Phase 1.

Phase 4 — Report
  Structure: Critical → High → Medium → Low
  Each finding: file:line, evidence snippet, risk description, concrete fix.
  End with "Approval recommendation: APPROVE / REQUEST_CHANGES / BLOCK"
```

## Pattern 8: Subagent Result Handling (Agent Summary Protocol)

When delegating to a subagent, always follow this protocol:

```
Before delegation:
  1. Write a precise, self-contained prompt (subagent has no prior context)
  2. Include: file paths, symbol names, specific questions to answer
  3. Specify expected output format

After receiving result:
  1. Verify the result addresses all questions
  2. Extract key findings into a structured summary:
     - Decisions made
     - Files affected
     - Risks identified
     - Recommended next steps
  3. Use memory_save() to persist important decisions
  4. Use prompt_suggestion() to determine follow-up actions
  5. Report to user with: "Subagent found X. Recommended action: Y."
```

## Pattern 9: Session Bootstrap (New Conversation)

At the start of every new conversation on a familiar project:

```
1. memory_read() → load persisted decisions and context
2. project_overview() → refresh project structure understanding
3. branch_status() → understand current git state
4. If memory exists, briefly mention: "I recall from previous sessions: [key points]"
5. Proceed with user's request using recovered context
```

## Pattern 10: Post-Task Completion Protocol

After completing any non-trivial task:

```
1. Run quality gates (see quality-gate SKILL)
2. prompt_suggestion(completed_task=description) → get follow-up ideas
3. If the task involved an important decision:
   memory_save(key="...", content="...", category="decision")
4. Present completion summary + top 2-3 suggested next steps
```
