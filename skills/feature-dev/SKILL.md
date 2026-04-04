# Feature Development — 多阶段引导式功能开发

> 适配自 Claude Code 官方 feature-dev 插件，提供系统化的功能开发流程

Use this skill when the user wants to implement a new feature, add functionality, or build something non-trivial. Provides a structured 7-phase workflow from discovery to delivery.

## Core Principles

- **Ask clarifying questions** — identify all ambiguities and edge cases. Ask early, before designing.
- **Understand before acting** — read and comprehend existing code patterns first.
- **Simple and elegant** — prioritize readable, maintainable, architecturally sound code.
- **Track progress** — use TodoWrite to track all phases.

---

## Phase 1: Discovery

**Goal**: Understand what needs to be built.

1. Create todo list covering all 7 phases
2. If the feature request is unclear, ask the user:
   - What problem are they solving?
   - What should the feature do?
   - Any constraints or requirements?
3. Summarize your understanding and confirm with user

---

## Phase 2: Codebase Exploration

**Goal**: Understand relevant existing code and patterns.

1. Use the `explore` subagent to survey 2-3 different aspects:
   - Similar features and their implementation patterns
   - High-level architecture and abstractions
   - UI patterns, testing approaches, extension points
2. Read all key files identified during exploration
3. Present a comprehensive summary of findings

---

## Phase 3: Clarifying Questions

**Goal**: Fill gaps and resolve all ambiguities BEFORE designing.

**CRITICAL — DO NOT SKIP THIS PHASE.**

1. Review codebase findings + original feature request
2. Identify underspecified aspects:
   - Edge cases and error handling
   - Integration points with existing code
   - Scope boundaries (what's NOT included)
   - Design preferences and constraints
   - Backward compatibility requirements
   - Performance needs
3. Present ALL questions in a clear, organized list
4. **Wait for answers before proceeding**

---

## Phase 4: Architecture Design

**Goal**: Design implementation approaches with trade-offs.

1. Use the `architect` subagent with different focuses:
   - **Minimal changes** — smallest diff, maximum reuse
   - **Clean architecture** — maintainability, elegant abstractions
   - **Pragmatic balance** — speed + quality
2. Review all approaches and form your recommendation
3. Present to user:
   - Brief summary of each approach
   - Trade-offs comparison
   - **Your recommendation with reasoning**
4. **Ask user which approach they prefer**

---

## Phase 5: Implementation

**Goal**: Build the feature.

**DO NOT START WITHOUT USER APPROVAL on the approach.**

1. Read all relevant files identified in previous phases
2. Implement following the chosen architecture
3. Follow codebase conventions strictly
4. Write clean, well-documented code
5. Update todos as you progress

---

## Phase 6: Quality Review

**Goal**: Ensure code is simple, DRY, elegant, and correct.

1. Use the `code-reviewer` subagent to review from 3 angles:
   - Simplicity / DRY / elegance
   - Bugs / functional correctness
   - Project conventions / abstractions
2. Present findings with severity ratings
3. **Ask user what to do**: fix now, fix later, or proceed as-is
4. Address issues based on user decision

---

## Phase 7: Summary

**Goal**: Document what was accomplished.

1. Mark all todos complete
2. Summarize:
   - What was built
   - Key decisions made
   - Files modified/created
   - Suggested next steps (testing, deployment, follow-up features)
