# Query Pipeline — 查询管线技能

> 借鉴 claude-code-rust 的 QueryEngine + QueryPipeline 多阶段管线设计

Use this skill when handling complex queries that benefit from a structured pipeline approach. Provides a systematic method to transform, compress, and optimize conversations.

## Pipeline Stages

### Stage 1: Normalize

Before processing any query:
- Parse the user's request to identify: goal, constraints, context references
- Normalize message format (strip redundant whitespace, fix encoding)
- Extract file references (@-mentions) and resolve to actual paths

### Stage 2: Context Budget

Check context window usage:
- Call `context_budget` to see remaining tokens
- If above 80% utilization: trigger compression
- Call `context_compress` if needed, keeping recent messages + system prompt

### Stage 3: Tool Planning

Before executing, plan the tool calls:
- Identify which tools are needed
- Check permissions via `permission_check` for dangerous operations
- Batch independent reads (Grep, Glob, Read) into parallel calls
- Sequence dependent writes (Read → StrReplace → ReadLints)

### Stage 4: Execute with Metrics

During execution:
- Call `metrics_record` to log each major operation
- Log start time, tool name, result status
- On error: retry once, then report

### Stage 5: Post-process

After completing the task:
- Run `ReadLints` on all modified files
- Generate a summary comparing result to original request
- If significant decisions were made, call `memory_save` to persist them
- Call `audit_log` to record the operation for audit trail

## When to Apply

- Complex multi-file tasks (3+ files)
- Tasks involving external API calls
- Security-sensitive operations
- Long conversations approaching context limits
- Tasks where decisions should be remembered

## Compression Strategy (from ContextCompressor)

```
if total_tokens > max_context_tokens:
    keep = [system_prompt] + recent_N_messages
    for msg in keep:
        if msg.tokens > budget_per_message:
            msg.content = truncate(msg.content, budget_per_message)
    return keep
```
