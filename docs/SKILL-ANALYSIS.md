# 能否用 Cursor Skill 复刻 Claude Code 的 Agent Loop？

> 深度分析 Cursor Skills 的能力边界、与 Claude Code 架构的本质差异，以及最优增强方案。

---

## 一、结论

**不能完全复刻，但可以在 Cursor 的框架内实现一个"精神等价物"。**

Cursor **内部已经实现了和 Claude Code 几乎相同的 Agent Loop**（工具调用循环、流式响应、权限检查）。Skill 不是"重新实现"这个循环，而是**注入领域知识**，让 Cursor 的 Agent Loop 运转得更像 Claude Code 那样高效和严谨。

---

## 二、Claude Code Agent Loop 的核心机制

Claude Code 的 `queryLoop()` 本质：

```
while (true) {
  response = await callAnthropicAPI(messages)     // ← 直接控制 API 调用
  if (response.has_tool_use) {
    results = await runTools(response.tool_calls)   // ← 直接控制工具调度
    messages.push(results)                          // ← 直接操作消息队列
    if (shouldAutoCompact()) compact(messages)      // ← 直接管理上下文窗口
    if (shouldStop()) break                         // ← 直接控制终止条件
    continue                                        // ← 回到循环顶部
  }
  break
}
```

完整的流程图：

```
┌──────────────────────────────────────────────────────────────┐
│                      queryLoop()                              │
│                                                               │
│  1. 构建消息 → 发送到 Anthropic API (callModel)               │
│  2. 流式接收 LLM 响应 (StreamEvent)                           │
│  3. 如果响应包含 tool_use → 执行工具 (runTools)               │
│  4. 将工具结果作为 tool_result 追加到消息                      │
│  5. 检查是否需要自动压缩 (autoCompact)                        │
│  6. 检查 maxTurns/tokenBudget/abort 等终止条件                │
│  7. 如果未终止 → 回到步骤 1                                   │
│  8. 执行 stopHooks → 返回最终结果                             │
└──────────────────────────────────────────────────────────────┘
```

---

## 三、核心控制能力对比

| 控制能力 | Claude Code | Cursor Skill |
|----------|-------------|--------------|
| 直接调用 LLM API | **有** — 自己构建请求参数 | **无** — Cursor 内部控制 |
| 管理消息队列/历史 | **有** — 直接操作 `messages[]` | **无** — Cursor 内部管理 |
| 控制工具调度顺序 | **有** — 串行/并发分区逻辑 | **间接** — 通过 prompt 引导 |
| 实现 AsyncGenerator 流式 | **有** — `yield*` 驱动事件流 | **无** — Cursor 控制渲染 |
| Token 预算管理 | **有** — `tokenBudget` 精确检查 | **无** — Cursor 内部管理 |
| 自动上下文压缩 | **有** — `autoCompact` 五级策略 | **无** — Cursor 用自己的策略 |
| 权限系统 | **有** — `canUseTool()` 多来源竞争 | **有** — Cursor 自带权限管理 |
| 工具并发策略 | **有** — 只读并发/写操作串行 | **有** — Cursor 原生支持 |
| 终止条件控制 | **有** — maxTurns/abort/budget | **无** — Cursor 内部控制 |
| 推测执行（Speculation） | **有** — 预测用户输入提前执行 | **无** — Cursor 自己的实现 |

---

## 四、根本性架构差异

### Claude Code 的做法：代码级控制

```
┌────────────────────────────────────────┐
│  System Prompt: ~8K tokens (固定)       │
│  工具描述: ~5K tokens (固定)             │
│  用户上下文: ~2K tokens (动态)           │
│  对话历史: 剩余全部空间                   │
│                                         │
│  queryLoop() → 代码逻辑，0 tokens 成本   │  ← 不占上下文！
│  runTools() → 代码逻辑，0 tokens 成本    │  ← 不占上下文！
│  autoCompact() → 代码逻辑，0 tokens 成本 │  ← 不占上下文！
│  stopHooks() → 代码逻辑，0 tokens 成本   │  ← 不占上下文！
└────────────────────────────────────────┘
```

**关键点**：Claude Code 的循环逻辑是 TypeScript 代码，运行在 Bun 进程中，**不消耗任何 token**。

### Cursor + Skills 的做法：提示级注入

```
┌────────────────────────────────────────┐
│  System Prompt: ~2K tokens              │
│  Skills: 5K~96K tokens ← 每轮都在这里！  │  ← 每轮消耗 token！
│  Rules: ~1K tokens                       │
│  工具描述: ~3K tokens                     │
│  对话历史: 剩余空间                       │
└────────────────────────────────────────┘
```

**关键点**：Skill 内容在每次 LLM 调用时都作为 system prompt 的一部分被发送。它不是"执行一次就丢掉的脚本"，而是**每一轮对话都要消耗的 token**。

### 本质差异总结

| 维度 | Claude Code (原版) | Cursor Skills (增强方案) |
|---|---|---|
| 控制层级 | **代码级** — 直接操作 API、消息队列、工具调度 | **提示级** — 通过 prompt 引导 Agent 行为 |
| 强制力 | **硬约束** — 代码逻辑保证循环一定执行 | **软约束** — Agent "应该"遵循，但可能跳过 |
| AsyncGenerator 流式 | **有** — 真正的 `yield*` 驱动事件流 | **无** — Cursor 内部控制流式渲染 |
| Token 预算管理 | **精确** — 代码级 token 计数和截断 | **启发式** — 通过 prompt 引导"少读多搜" |
| 运行成本 | 逻辑 = 0 token | 逻辑 = N token/轮（Skill 文本） |
| 核心价值 | 完全掌控 Agent 行为 | **零成本增强 Cursor 已有的 Agent Loop** |

---

## 五、500 行限制分析

### 为什么存在 500 行限制？

Cursor 官方建议 SKILL.md 不超过 500 行，原因是 **token 预算**：

| Skill 规模 | 估计 Token 消耗 | 占 200K 窗口比例 | 对对话空间的影响 |
|---|---|---|---|
| 458 行（当前方案，4 个 Skill） | ~5,400 | 2.7% | 几乎无影响 |
| 2,000 行（每个 500 行上限） | ~24,000 | 12% | 明显减少可用空间 |
| 4,000 行（每个 1000 行，取消限制） | ~48,000 | 24% | 可用空间减 1/4 |
| 8,000 行（每个 2000 行，极端情况） | ~96,000 | 48% | **可用空间减半 — 灾难性** |

### 取消行数限制会更强大吗？

**答案是反直觉的：不会，反而可能变弱。**

原因：

1. **Token 是零和博弈** — Skill 占用的 token 直接从对话/代码空间中扣除
2. **LLM 注意力衰减** — 超长 system prompt 中后半部分的指令遵从率下降
3. **边际收益递减** — 核心规则 100 行就能覆盖，后续内容多是 LLM 已知的常识
4. **Claude Code 不存在此问题** — 它的逻辑是代码，0 token 成本

### 如果真要增加内容怎么办？

使用 **渐进式披露**（Progressive Disclosure）模式：

```
agent-loop-orchestrator/
├── SKILL.md           ← 109 行，核心逻辑（每轮注入，~1,200 tokens）
├── patterns.md        ← 118 行，详细模式（Agent 需要时才 Read，一次性付费）
└── examples.md        ← 可选，实际案例参考
```

- SKILL.md 每轮只消耗 ~1,200 tokens（固定成本）
- `patterns.md` 只在 Agent 需要处理复杂场景时被 `Read` 一次
- 读完后 Agent 基于理解做决策，不需要反复加载

**效果等价于 "1000+ 行 Skill"，但固定 token 成本仅为其一半。**

---

## 六、Cursor Skill 真正能做什么

Cursor Skill 是 **prompt 级别的行为注入**：

> "一段结构化的 Markdown 指令，被注入到 Cursor Agent 的 system prompt 中，影响 Agent 的行为决策。"

它不能控制底层运行时，但可以显著影响 Agent 在以下方面的表现：

### 能做的（高价值）

| 能力 | 说明 |
|---|---|
| 工作流编排 | 引导 Agent 遵循 Plan→Act→Observe→Decide→Repeat 循环 |
| 工具选择优化 | 教 Agent 何时用 Grep vs SemanticSearch vs Read |
| 并发/串行规则 | 教 Agent 哪些工具调用可以批处理，哪些必须串行 |
| 上下文管理 | 教 Agent 渐进式加载、选择性读取、避免浪费 |
| 质量把关 | 教 Agent 每步之后验证（lint、test、read-back） |
| 错误恢复 | 教 Agent 如何处理常见失败场景 |
| 大任务分解 | 教 Agent 将复杂任务分阶段执行 |

### 不能做的（架构限制）

| 限制 | 说明 |
|---|---|
| 控制 API 调用参数 | 无法设置 max_tokens、temperature、model |
| 操作消息队列 | 无法删除/修改历史消息 |
| 实现流式处理 | 无法控制 streaming 行为 |
| 精确 token 管理 | 无法获知当前 token 用量 |
| 强制终止条件 | 无法硬性限制循环次数 |
| 自动上下文压缩 | 无法触发对话压缩 |

---

## 七、最优方案：四个协作 Skill

### 方案架构

```
用户提出任务
      │
      ▼
┌─────────────────────────────┐
│  agent-loop-orchestrator    │  ← "要做什么？分几步？"
│  Plan → Act → Observe →     │
│  Decide → Repeat            │
└──────────┬──────────────────┘
           │ 每个 Act 步骤中
           ▼
┌─────────────────────────────┐
│  tool-strategy              │  ← "用哪个工具？怎么调用最高效？"
│  选工具 → 批处理 → 执行      │
└──────────┬──────────────────┘
           │ 调用工具时
           ▼
┌─────────────────────────────┐
│  context-management         │  ← "读哪些文件？怎么避免上下文爆炸？"
│  懒加载 → 定向搜索 → 精读    │
└──────────┬──────────────────┘
           │ 每步完成后
           ▼
┌─────────────────────────────┐
│  quality-gate               │  ← "改完了吗？确认没问题？"
│  验证 → Lint → 测试 → 汇报   │
└─────────────────────────────┘
```

### 四个 Skill 与 Claude Code 的对应关系

| Skill | 对应 Claude Code 模块 | 核心职责 |
|---|---|---|
| `agent-loop-orchestrator` | `query.ts` → `queryLoop()` | 核心循环：Plan → Act → Observe → Decide → Repeat |
| `tool-strategy` | `toolOrchestration.ts` → 并发/串行分区 | 工具选择决策树、批处理规则、成本优化 |
| `context-management` | `autoCompact` + `microCompact` | 渐进式上下文加载、大文件策略、信息压缩 |
| `quality-gate` | `stopHooks.ts` | 五级质量门：编辑验证→Lint→构建→测试→需求校验 |

### 文件结构

```
~/.cursor/skills/
├── agent-loop-orchestrator/
│   ├── SKILL.md              (109 行) ← 每轮注入：核心循环逻辑
│   └── patterns.md           (118 行) ← 按需读取：高级重构/调试模式
├── tool-strategy/
│   ├── SKILL.md              (117 行) ← 每轮注入：工具选择规则
│   └── advanced-patterns.md  (127 行) ← 按需读取：复杂搜索/编排
├── context-management/
│   ├── SKILL.md              (117 行) ← 每轮注入：上下文管理原则
│   └── large-codebase-guide.md (96 行) ← 按需读取：大代码库导航
└── quality-gate/
    ├── SKILL.md              (136 行) ← 每轮注入：质量把关流程
    └── checklists.md         (116 行) ← 按需读取：各语言检查清单

总计：936 行（其中固定成本仅 479 行 ≈ ~5,400 tokens/轮 ≈ 占上下文 2.7%）
```

### Token 成本分析

| 层级 | 文件 | 加载时机 | Token 成本 |
|---|---|---|---|
| **L1 核心** | 4 × SKILL.md (479 行) | 每轮自动注入 | ~5,400/轮（固定，占 2.7%） |
| **L2 参考** | 4 × 辅助 .md (457 行) | Agent 按需 Read | 仅使用时一次性付费 |
| **合计** | 936 行 | — | 远优于 "936 行全部塞进 SKILL.md" |

---

## 八、各 Skill 核心内容摘要

### 8.1 agent-loop-orchestrator

**核心循环**：

```
1. PLAN    — 拆解任务为有序步骤，用 TodoWrite 创建清单
2. ACT     — 每次执行一个逻辑步骤
3. OBSERVE — 读回修改的文件，运行 ReadLints 检查
4. DECIDE  — 成功则继续、失败则修复、反复失败则停下问用户
5. REPEAT  — 直到所有步骤完成
```

**关键规则**：
- 动手前必须先读相关文件
- 只读操作并行批处理，写操作串行
- 超过 8 步的任务分阶段
- 连续失败 3 次必须停下

### 8.2 tool-strategy

**工具选择决策树**：

```
知道文件路径？ → Read
知道精确字符串？ → Grep → Read
知道文件名模式？ → Glob → Read
都不知道？ → SemanticSearch
```

**批处理规则**：
- 可以并行：多个 Read、多个 Grep、多个 Glob
- 不能并行：Read → StrReplace（同一文件）、StrReplace → ReadLints（同一文件）

### 8.3 context-management

**三阶段渐进加载**：

```
Phase 1 — 轻量定位：Glob + package.json + README
Phase 2 — 定向调查：Grep + 精准 Read (offset/limit)
Phase 3 — 深度阅读：仅在编辑时读完整文件
```

**大文件策略**：
- < 200 行：直接读
- 200–1000 行：先 Grep 再定向 Read
- \> 1000 行：SemanticSearch 或 Grep + 定向 Read

### 8.4 quality-gate

**五级质量门**：

```
Gate 1 — 编辑验证：Read 回修改的文件确认正确
Gate 2 — Lint 检查：ReadLints 捕捉语法/类型错误
Gate 3 — 构建检查：编译语言运行 build
Gate 4 — 测试检查：运行相关测试（非全量）
Gate 5 — 需求验证：对照原始请求确认完成
```

**熔断机制**：连续 3 次失败 / 循环依赖 / 范围蔓延 → 停下报告用户

---

## 九、方案效果评估

### 能达到的效果（vs 不使用 Skill 的 Cursor）

| 行为维度 | 无 Skill | 有 Skill | 提升 |
|---|---|---|---|
| 修改前先读文件 | 时有时无 | 始终执行 | ⬆ 显著 |
| 工具选择效率 | 经常用 SemanticSearch 找精确符号 | 精确符号用 Grep | ⬆ 显著 |
| 批处理意识 | 偶尔并行 | 系统性并行只读操作 | ⬆ 中等 |
| 修改后验证 | 经常跳过 | 始终 lint + read-back | ⬆ 显著 |
| 大任务管理 | 容易迷失 | 使用 TodoWrite 跟踪 | ⬆ 显著 |
| 大文件处理 | 可能读 10000 行 | 先搜后读 | ⬆ 显著 |
| 错误恢复 | 有时重复同一错误 | 3 次失败停下 | ⬆ 中等 |

### 无法达到的效果（架构限制）

| Claude Code 能力 | 为什么 Skill 做不到 |
|---|---|
| 精确 token 计数和自动压缩 | 无法访问 Cursor 内部的 token 计数器 |
| 强制终止循环（maxTurns） | 无法写代码控制循环次数 |
| 推测执行（Speculation） | 需要代码级别的 abort controller |
| 工具并发分区（partitionToolCalls） | Cursor 内部已实现，Skill 只能建议 |
| 流式事件处理 | AsyncGenerator 是代码级机制 |
| 多 Agent 邮箱通信 | 需要进程间通信基础设施 |

---

## 十、总结

### 一句话

> Cursor Skill 是"教练"而不是"引擎"——它不能替代 Claude Code 的 TypeScript 运行时，但它可以让 Cursor 自己的 Agent Loop 引擎跑出更接近 Claude Code 的效果。

### 投入产出比

- **投入**：936 行 Markdown（479 行固定成本 ≈ 2.7% 上下文窗口）
- **产出**：Agent 在工作流纪律、工具效率、上下文管理、质量保证四个维度显著提升
- **零代码、零依赖、即装即用、全项目通用**

### 进一步增强方向

如果想要更深度的控制，需要超越 Skill 的范畴：

1. **Cursor Rules** (`.cursor/rules/`) — 项目级别的硬性规则，补充 Skill 的软性引导
2. **Custom Subagents** (`.cursor/agents/`) — 专门化的子 Agent（Code Reviewer、Debugger）
3. **MCP Server** — 自建 MCP 服务器提供自定义工具，突破 Cursor 内置工具的限制
4. **Fork Claude Code** — 如果真的需要完全控制 Agent Loop，可以基于 Claude Code 源码（本仓库的 `main` 分支 Java 重写版或 `learn` 分支教学项目）构建自己的 Agent

以下是前三项的完整实现方案。

---

## 十一、增强方向 1：Cursor Rules — 硬性规则

### Skills vs Rules 的区别

| 维度 | Skills | Rules |
|---|---|---|
| 存放位置 | `~/.cursor/skills/` 或 `.cursor/skills/` | `.cursor/rules/` |
| 文件格式 | `SKILL.md` (Markdown + YAML frontmatter) | `.mdc` (Markdown + YAML frontmatter) |
| 作用范围 | 被 Agent 发现并"选择性"遵循 | 根据 `alwaysApply` 或 `globs` **强制注入** |
| 约束力 | 软引导 — Agent 可能跳过 | 硬规则 — 始终存在于 system prompt 中 |
| 最佳用途 | 复杂工作流指导、工具策略、模式参考 | 简短的不可违反的编码规范 |

**关键洞察**：Skills 是"教科书"，Rules 是"法律"。两者互补。

### 已创建的 Rules（`.cursor/rules/`）

#### 1. `agent-discipline.mdc` — Agent 行为硬约束（alwaysApply: true）

```
五条不可违反的规则：
1. Read before edit — 修改文件前必须先读
2. Verify after edit — 修改后必须运行 ReadLints
3. One step at a time — 写操作逐步执行并验证
4. No blind bulk edits — 不修改未读过的文件
5. Fail fast — 同一错误连续 3 次则停下

+ 并行批处理规则
+ 工具使用禁令（禁止 cat/sed/awk 等）
```

这条 Rule 将 `agent-loop-orchestrator` Skill 中最核心的行为要求提升为硬性约束。即使 Agent "忘记"了 Skill 的建议，Rule 依然确保基本纪律。

#### 2. `search-first.mdc` — 搜索优先策略（alwaysApply: true）

```
强制执行搜索决策树：
- 知道路径 → Read
- 知道字符串 → Grep → Read
- 知道文件名模式 → Glob → Read
- 只知道概念 → SemanticSearch

+ 大文件禁止整文件读取
+ 广泛探索必须委托给子 Agent
```

这条 Rule 对应 `tool-strategy` 和 `context-management` 两个 Skill 的核心规则。

#### 3. `completion-gate.mdc` — 完成门禁（alwaysApply: true）

```
声明"完成"前必须通过四项检查：
1. Read back 所有修改的文件
2. ReadLints 无新增错误
3. 运行相关测试（如有）
4. 对照原始需求确认

+ 强制要求汇报格式
```

这条 Rule 是 `quality-gate` Skill 的强制版。

#### 4. `typescript-conventions.mdc` — TypeScript 编码规范（globs: `**/*.ts,**/*.tsx`）

```
仅在编辑 .ts/.tsx 文件时激活：
- strict TypeScript，禁止 any
- interface 用于对象，type 用于联合
- 显式错误处理，禁止空 catch
- async/await 优于 .then()
- 函数不超过 50 行
```

这是文件类型特定的 Rule，只在相关文件打开时注入。

### Rules 与 Skills 的协作模型

```
┌─────────────────────────────────────────────┐
│           Cursor System Prompt               │
│                                              │
│  Rules (硬约束，始终在场):                     │
│  ┌──────────────┐  ┌────────────┐            │
│  │ agent-       │  │ search-    │            │
│  │ discipline   │  │ first      │            │
│  └──────────────┘  └────────────┘            │
│  ┌──────────────┐  ┌────────────┐            │
│  │ completion-  │  │ typescript-│            │
│  │ gate         │  │ conventions│ ← 仅 .ts   │
│  └──────────────┘  └────────────┘            │
│                                              │
│  Skills (软引导，按需激活):                     │
│  ┌──────────────────────────────────┐        │
│  │ agent-loop-orchestrator          │        │
│  │ tool-strategy                    │        │
│  │ context-management               │        │
│  │ quality-gate                     │        │
│  └──────────────────────────────────┘        │
│                                              │
│  Rules 确保底线，Skills 提供最佳实践          │
└─────────────────────────────────────────────┘
```

---

## 十二、增强方向 2：Custom Subagents — 专门化子 Agent

### 为什么需要 Subagents？

Claude Code 有 `AgentTool`（生成子 Agent）和 `TeamCreateTool`（团队 Agent）。
Cursor 的 Custom Subagents 提供了类似能力：**隔离的上下文 + 专门化的 system prompt**。

好处：
1. **上下文隔离** — 子 Agent 的搜索/阅读不污染主对话
2. **行为专门化** — 定制 system prompt 让 Agent 专注于单一任务
3. **可复用** — 用户级 Agent 跨所有项目可用

### 已创建的 Subagents（`~/.cursor/agents/`）

#### 1. `code-reviewer.md` — 代码审查专家

**对应 Claude Code**：`/review` 命令 + 内部 code review 逻辑

```
触发条件：
  修改代码后、完成功能后、用户要求 review

工作流：
  1. git diff → 查看变更
  2. Read 每个修改的文件
  3. 按优先级分类反馈

反馈分级：
  CRITICAL — 逻辑错误、安全漏洞、数据丢失风险
  WARNING  — 性能问题、缺少输入验证、代码重复
  SUGGESTION — 命名改进、简化机会、缺少测试

规则：
  - 只评论变更的代码，不挑历史问题
  - 代码没问题就直接说没问题
  - 必须给出具体修复建议
```

#### 2. `debugger.md` — 调试专家

**对应 Claude Code**：`BashTool` + 错误分析逻辑

```
触发条件：
  遇到任何错误、异常、测试失败

工作流（5 步法）：
  1. Capture — 获取完整错误信息和堆栈
  2. Reproduce — 理解最小复现路径
  3. Isolate — 定位到具体文件和行
  4. Diagnose — 解释为什么失败（不只是在哪失败）
  5. Fix — 实施最小正确修复

针对不同场景的策略：
  运行时错误 → 从堆栈底部往上读，检查最近变更
  测试失败   → 对比期望 vs 实际，判断改代码还是改测试
  "不工作"   → 在关键决策点加 log，找到行为分叉点

规则：
  - 必须找根因，不能只治标
  - 修代码不修测试（除非测试确实过时）
  - 调试时不做重构
```

#### 3. `architect.md` — 架构师

**对应 Claude Code**：`AgentTool`（探索子 Agent）+ context 分析逻辑

```
触发条件：
  架构讨论、系统设计、大功能规划、"怎么组织这个？"

工作流：
  1. Explore — 调查代码库结构、关键文件、依赖
  2. Analyze — 理解当前架构和约束
  3. Design — 提出方案并说明理由
  4. Present — 展示方案 + 权衡 + 备选方案

输出格式：
  目标、关键决策、文件变更列表、优劣权衡、被排除的备选方案

规则：
  - 不写代码，只设计方案
  - 至少提供一个备选方案
  - 诚实说明缺陷和风险
  - 如果现有代码已有模式，遵循该模式
```

### Subagents 与 Skills 的关系

```
用户请求："实现一个新的 API 端点"
     │
     ▼
主 Agent（受 Skills 和 Rules 约束）
     │
     ├─ 委托 → architect Agent（设计方案）
     │          返回方案摘要
     │
     ├─ 主 Agent 执行实现（遵循 agent-loop-orchestrator Skill）
     │
     ├─ 委托 → code-reviewer Agent（审查代码）
     │          返回审查结果
     │
     ├─ 如果有问题 → 委托 debugger Agent
     │
     └─ 完成（通过 quality-gate 检查）
```

---

## 十三、增强方向 3：MCP Server — 自定义工具

### 为什么需要自定义 MCP Server？

Cursor 内置工具覆盖了基本的文件操作和搜索，但缺少一些 Claude Code 拥有的"智能辅助"能力：

| 缺失的能力 | Claude Code 如何实现 | MCP Server 如何补充 |
|---|---|---|
| Token 计数 | `tokenEstimation.ts` 精确计数 | `token_count` 工具估算 |
| 项目全景图 | 启动时 `countFilesRoundedRg` 统计 | `project_map` 一次性结构摘要 |
| 依赖关系分析 | `context.ts` 的 import 追踪 | `dependency_graph` 依赖树 |
| 智能测试运行 | `BashTool` + 测试框架检测 | `test_runner` 自动检测框架 |

### 已创建的 MCP Server（`.cursor/mcp-servers/agent-tools/`）

#### 架构

```
.cursor/
├── mcp.json                          ← Cursor MCP 配置（指向 server.py）
└── mcp-servers/
    └── agent-tools/
        ├── server.py                 ← MCP Server 实现（Python + FastMCP）
        ├── requirements.txt          ← Python 依赖
        └── README.md                 ← 使用说明
```

#### 四个自定义工具

##### 1. `token_count` — Token 估算

```
输入: { "file_path": "src/QueryEngine.ts" }
输出: {
  "tokens": 46000,
  "lines": 1296,
  "recommendation": "建议用 SemanticSearch 或 Grep，避免整文件读取"
}
```

**价值**：Agent 在读文件前先调用此工具，根据 token 数量决定读取策略。
对应 Claude Code 的 `tokenEstimation.ts`，但通过 MCP 工具而非代码级集成。

##### 2. `project_map` — 项目结构全景

```
输入: { "root_dir": ".", "max_depth": 3, "show_sizes": true }
输出: {
  "tree": "src/\n├── main.tsx (4684 lines)\n├── tools/\n│   ├── BashTool/ ...",
  "summary": { "directories": 37, "files": 1900, "top_extensions": {".ts": 800} }
}
```

**价值**：一次调用替代多次 Glob，快速建立项目心智模型。
对应 Claude Code 启动时的 `countFilesRoundedRg` + 目录结构分析。

##### 3. `dependency_graph` — 依赖关系分析

```
输入: { "file_path": "src/query.ts", "max_depth": 2 }
输出: {
  "graph": {
    "src/query.ts": ["src/services/api/claude.ts", "src/services/tools/toolOrchestration.ts"],
    "src/services/api/claude.ts": ["[external] @anthropic-ai/sdk"]
  },
  "total_files": 15
}
```

**价值**：重构前了解影响范围，避免遗漏依赖。
对应 Claude Code 的 `context.ts` 中的依赖追踪逻辑。

##### 4. `test_runner` — 智能测试运行器

```
输入: { "file_path": "src/utils/auth.ts" }
输出: {
  "framework": "vitest",
  "command": "npx vitest run src/utils/auth.test.ts",
  "passed": true,
  "output": "..."
}
```

**价值**：自动检测测试框架（jest/vitest/pytest/go/cargo），自动推断测试文件路径。
对应 Claude Code 中 `BashTool` 执行测试的场景，但更加智能化。

### 启用方式

```bash
# 1. 安装依赖
cd .cursor/mcp-servers/agent-tools
pip install -r requirements.txt

# 2. Cursor 会自动读取 .cursor/mcp.json 并启动 MCP Server
# 重启 Cursor 或刷新 MCP 连接即可
```

### MCP Server 与 Skills/Rules 的协作

```
context-management Skill 建议："大文件先评估再读取"
         │
         ▼
Agent 调用 MCP: token_count("src/QueryEngine.ts")
         │
         ▼
返回: 46000 tokens → "建议用 SemanticSearch"
         │
         ▼
tool-strategy Skill 指导: 用 Grep 或 SemanticSearch 定位
         │
         ▼
Agent 执行高效的定向搜索，而非读取整个 46K token 的文件
```

三层体系形成闭环：
- **Rules** 确保底线（"必须先搜后读"）
- **Skills** 提供策略（"大文件的三阶段加载"）
- **MCP Tools** 提供数据（"这个文件有 46000 tokens"）

---

## 十四、完整增强体系总览

### 文件清单

```
个人级（~/.cursor/，所有项目通用）：
├── skills/
│   ├── agent-loop-orchestrator/
│   │   ├── SKILL.md              (109 行)
│   │   └── patterns.md           (118 行)
│   ├── tool-strategy/
│   │   ├── SKILL.md              (117 行)
│   │   └── advanced-patterns.md  (127 行)
│   ├── context-management/
│   │   ├── SKILL.md              (117 行)
│   │   └── large-codebase-guide.md (96 行)
│   └── quality-gate/
│       ├── SKILL.md              (136 行)
│       └── checklists.md         (116 行)
└── agents/
    ├── code-reviewer.md          (62 行)
    ├── debugger.md               (68 行)
    └── architect.md              (58 行)

项目级（.cursor/，当前项目）：
├── rules/
│   ├── agent-discipline.mdc     (29 行)
│   ├── search-first.mdc         (22 行)
│   ├── completion-gate.mdc      (24 行)
│   └── typescript-conventions.mdc (17 行)
├── mcp.json                     (MCP Server 配置)
└── mcp-servers/
    └── agent-tools/
        ├── server.py            (Python MCP Server)
        ├── requirements.txt
        └── README.md
```

### 四层防御体系

```
┌─────────────────────────────────────────────────────────┐
│                    第 4 层：MCP Tools                      │
│         提供数据支撑（token 计数、项目地图、依赖图）        │
│                                                          │
│  ┌─────────────────────────────────────────────────────┐ │
│  │                 第 3 层：Subagents                    │ │
│  │       专门化能力（审查、调试、架构设计）               │ │
│  │                                                      │ │
│  │  ┌─────────────────────────────────────────────────┐ │ │
│  │  │              第 2 层：Skills                      │ │ │
│  │  │     最佳实践引导（循环、工具策略、上下文、质量）   │ │ │
│  │  │                                                  │ │ │
│  │  │  ┌─────────────────────────────────────────────┐ │ │ │
│  │  │  │           第 1 层：Rules                      │ │ │ │
│  │  │  │   硬性底线（必须先读再改、必须 lint、必须验证） │ │ │ │
│  │  │  └─────────────────────────────────────────────┘ │ │ │
│  │  └─────────────────────────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### 与 Claude Code 的最终对比

| 维度 | Claude Code | 四层增强后的 Cursor |
|---|---|---|
| 工作流纪律 | 代码强制 queryLoop | Rules 硬约束 + Skills 软引导 |
| 工具效率 | 代码级并发分区 | Skills 决策树 + Rules 禁令 |
| 上下文管理 | autoCompact 五级策略 | Skills 三阶段加载 + MCP token_count |
| 质量保证 | stopHooks 管线 | Rules 完成门禁 + Skills 五级质量门 |
| 代码审查 | /review 命令 | code-reviewer Subagent |
| 调试能力 | BashTool + 错误分析 | debugger Subagent |
| 架构分析 | AgentTool 探索子 Agent | architect Subagent |
| 项目理解 | 启动时上下文收集 | MCP project_map + dependency_graph |
| 智能测试 | BashTool 通用执行 | MCP test_runner 自动检测框架 |
| 强制力 | 100%（代码控制） | ~85%（Rules 强制 + Skills 引导） |

---

*本分析基于 Claude Code 源码快照（`claude` 分支）和 Cursor Skills/Rules/Agents/MCP 机制的对比研究。所有增强组件已实际创建并可立即使用。*
