# Model Migration — AI 模型迁移技能

> 适配自 Claude Code 官方 claude-opus-4-5-migration 插件，泛化为通用模型迁移工具

Use this skill when the user wants to migrate their codebase from one AI model version to another (e.g., GPT-3.5 → GPT-4, Claude Sonnet → Claude Opus, or any model upgrade/switch). Handles model string updates, API parameter changes, and prompt adjustments for behavioral differences.

## Migration Workflow

### Step 1: Search & Inventory

1. Search codebase for model strings and API calls:
   - `Grep` for known model identifiers (e.g., `claude-sonnet`, `gpt-4`, `claude-opus`)
   - `Grep` for API endpoints (`/v1/messages`, `/v1/chat/completions`, etc.)
   - `Grep` for SDK client instantiation (`Anthropic(`, `OpenAI(`, etc.)
2. Create a migration inventory listing all files and locations

### Step 2: Model String Updates

Replace model strings according to the target model. Common patterns:

| Platform | Pattern |
|----------|---------|
| Anthropic API | `claude-{family}-{version}-{date}` |
| AWS Bedrock | `anthropic.claude-{family}-{version}-{date}-v1:0` |
| Google Vertex | `claude-{family}-{version}@{date}` |
| OpenAI API | `gpt-{version}` / `o1-{variant}` |
| Azure OpenAI | deployment name in config |

### Step 3: API Parameter Changes

Check for parameters that differ between model versions:
- **Thinking/reasoning** — `thinking` parameter, extended thinking budget
- **Effort/quality** — `effort` parameter for some models
- **Max tokens** — different limits per model
- **Beta headers** — some features require specific beta headers
- **Response format** — structured output support differences
- **Context window** — verify prompts fit within new model's limits

### Step 4: Prompt Adjustments

**Only apply if user reports issues.** Different models have behavioral differences:

#### Common Behavioral Shifts (stronger models)
- **Tool overtriggering** — Soften aggressive language:
  - `CRITICAL:` / `You MUST...` / `ALWAYS` → use calmer phrasing
  - Only adjust tool-triggering instructions, leave other emphasis alone
- **Over-engineering** — Stronger models may add unnecessary abstractions
  - Add guidance: "Implement exactly what's requested, no extra files or abstractions"
- **Code exploration hesitancy** — Model may propose fixes without reading code
  - Add guidance: "Always read relevant files before proposing changes"

#### Common Behavioral Shifts (model switching)
- **System prompt sensitivity** — Different models respond differently to prompt structure
- **Temperature sensitivity** — May need to adjust temperature/top_p
- **Output format** — JSON mode, function calling format may differ

### Step 5: Validation

1. Verify all model strings are updated
2. Check no deprecated parameters remain
3. Run existing tests to verify functionality
4. Summarize all changes made

### Step 6: Report

```markdown
## Migration Summary

### Model Change
- From: [old model]
- To: [new model]

### Files Modified
- `path/to/file.py` — updated model string
- `path/to/config.yaml` — updated deployment config

### API Changes
- Added/removed parameters: [list]
- Updated headers: [list]

### Prompt Adjustments
- [None / list of changes]

### Action Items
- [ ] Run tests to verify behavior
- [ ] Monitor for behavioral differences in production
- [ ] If issues arise, prompt adjustments may be needed
```

## Important Notes

- **Do NOT migrate models the user didn't ask to migrate** (e.g., don't touch Haiku if migrating Sonnet)
- **Prompt adjustments are opt-in** — only modify prompts when the user explicitly requests it or reports specific issues
- **Preserve existing behavior** — the goal is to swap the model, not rewrite the application
- **Be cautious with cost** — note any significant pricing differences between models
