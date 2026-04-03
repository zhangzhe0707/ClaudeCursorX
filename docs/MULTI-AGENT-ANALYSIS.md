# 用多个 MCP Server 替代 Claude Code 多 Agent 体系：深度可行性分析

> 核心问题：能否通过在 Cursor 中开发多个 MCP Server，用不同的 MCP 来对应 Claude Code 的不同 Agent，从而达到 Claude Code 的多 Agent 能力？

---

## 一、结论

**不能通过 MCP Server 直接替代，但可以通过 MCP + Subagent 组合达到 ~70% 的效果。**

原因是 Claude Code 的多 Agent 不只是"多个工具"，而是一个包含 **独立上下文、独立消息队列、进程间通信、权限隔离** 的完整运行时系统。MCP Server 只能提供"工具"，不能提供"独立思考的 Agent"。

---

## 二、Claude Code 多 Agent 体系完整解剖

### 2.1 三层 Agent 架构

```
┌─────────────────────────────────────────────────────────┐
│                   第 3 层：Coordinator 模式               │
│                                                          │
│  Coordinator（协调者）                                    │
│  ├─ 只有 4 个工具：AgentTool, SendMessage, TaskStop,     │
│  │   SyntheticOutput                                     │
│  ├─ 不直接执行任何代码操作                                │
│  └─ 职责：分解任务 → 派发 Worker → 汇总结果              │
│                                                          │
│  Worker（工作者）                                        │
│  ├─ 拥有完整工具集（Bash, Read, Write, Edit, Grep...）   │
│  ├─ 独立的上下文窗口和消息历史                            │
│  └─ 通过 <task-notification> XML 向 Coordinator 报告     │
│                                                          │
├─────────────────────────────────────────────────────────┤
│                   第 2 层：Team Swarm 模式                │
│                                                          │
│  TeamCreateTool → 创建团队                                │
│  ├─ Team Lead（团队领导）                                │
│  ├─ Teammate 1（队员，独立进程/终端面板）                 │
│  ├─ Teammate 2（队员，可以在 Git Worktree 中隔离）       │
│  └─ 通过 Mailbox（邮箱）通信                             │
│      └─ SendMessageTool → writeToMailbox()                │
│          ├─ 点对点消息：to="teammate-name"                │
│          ├─ 广播消息：to="*"                              │
│          └─ 结构化消息：shutdown_request/response          │
│                                                          │
├─────────────────────────────────────────────────────────┤
│                   第 1 层：AgentTool 子 Agent              │
│                                                          │
│  主 Agent 通过 AgentTool 生成子 Agent                     │
│  ├─ 同步模式：共享父 Agent 的 AbortController             │
│  ├─ 异步模式：完全隔离，后台运行                          │
│  ├─ 支持 Git Worktree 隔离（在代码副本中工作）            │
│  └─ 支持远程隔离（在 CCR 容器中运行）                     │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Agent 的核心组成（不只是"工具"）

每个 Claude Code Agent 是一个完整的运行时实体：

```typescript
// 简化自 src/tools/AgentTool/runAgent.ts
runAgent({
  agentDefinition,       // Agent 类型定义（system prompt、工具限制）
  promptMessages,        // 初始消息（用户指令 + 上下文）
  toolUseContext,        // 工具执行上下文
  canUseTool,            // 权限检查函数
  isAsync,               // 同步还是异步
  forkContextMessages,   // 从父 Agent 分叉的上下文
  querySource,           // 查询来源标识
  model,                 // 使用的模型
  maxTurns,              // 最大循环次数
  availableTools,        // 可用工具列表
  allowedTools,          // 权限白名单
  worktreePath,          // Git Worktree 路径（隔离）
})
```

每个 Agent 拥有：

| 组件 | 说明 |
|---|---|
| **独立的 System Prompt** | 根据 agentDefinition 定制 |
| **独立的消息历史** | 自己的 `messages[]`，不与父共享 |
| **独立的 Agent Loop** | 自己的 `query()` 循环 |
| **独立的工具集** | 通过 `ASYNC_AGENT_ALLOWED_TOOLS` 限制 |
| **独立的权限上下文** | 自己的 `toolPermissionContext` |
| **独立的 AbortController** | 可单独取消 |
| **独立的文件状态缓存** | `readFileState` 隔离 |
| **独立的转录存储** | `recordSidechainTranscript` |
| **可选的 Git Worktree** | 文件系统级隔离 |

### 2.3 Agent 间通信机制

```
┌────────────┐                    ┌────────────┐
│ Coordinator│                    │  Worker A  │
│            │  AgentTool(prompt)  │            │
│            │──────────────────▶│ ⟳ query()  │
│            │                    │   loop     │
│            │                    │            │
│            │ <task-notification> │            │
│            │◁─────────────────── │  完成/失败  │
└────────────┘                    └────────────┘

┌────────────┐  writeToMailbox()  ┌────────────┐
│ Teammate A │───────────────────▶│ Teammate B │
│            │  "inbox" 文件       │ 轮询 inbox │
│            │◁──────────────────│  读取消息   │
└────────────┘  writeToMailbox()  └────────────┘
```

**关键**：通信不是 HTTP API 调用，而是通过文件系统级别的邮箱（`~/.claude/teams/{name}/`）和进程内的消息队列。

### 2.4 工具权限分层

```
Coordinator 只有 4 个工具：
  AgentTool, SendMessage, TaskStop, SyntheticOutput

Worker/子 Agent 拥有完整工具集（但有排除项）：
  ✓ Bash, Read, Write, Edit, Grep, Glob, WebSearch, WebFetch, 
    Notebook, Skill, TodoWrite, Worktree...
  ✗ AgentTool (禁止递归), TaskOutput, ExitPlanMode, AskUserQuestion

In-Process Teammate 额外拥有：
  ✓ TaskCreate, TaskGet, TaskList, TaskUpdate, SendMessage, Cron
```

---

## 三、MCP Server 能替代的部分

### 3.1 MCP Server 的能力边界

| MCP Server 能做的 | MCP Server 不能做的 |
|---|---|
| 暴露自定义工具 | 拥有独立的 LLM 上下文/思维 |
| 执行任意代码逻辑 | 独立运行 Agent Loop |
| 返回结构化结果 | 管理独立的消息历史 |
| 跨进程通信（stdio/SSE） | 主动向 Agent 发消息 |
| 维护服务端状态 | 做出需要"推理"的决策 |
| 提供资源（文件/数据） | 控制 LLM 调用参数 |

### 3.2 对应关系分析

| Claude Code Agent 能力 | 用 MCP 替代？ | 替代效果 |
|---|---|---|
| 子 Agent 执行具体任务 | ⚠️ 部分可以 — MCP 工具可以封装复杂操作 | 缺少"思考"能力 |
| Agent 间通信 | ✓ 可以 — MCP Server 可实现消息队列 | 效果好 |
| 独立上下文/推理 | ✗ 不能 — MCP 工具是无状态的函数调用 | 根本缺失 |
| 工具权限隔离 | ⚠️ 部分 — 不同 MCP Server 天然隔离 | 方向正确 |
| 任务编排/调度 | ✓ 可以 — MCP Server 可实现任务队列 | 效果好 |
| Git Worktree 隔离 | ✓ 可以 — MCP 工具可以操作 worktree | 效果好 |
| 并行执行 | ⚠️ 部分 — Cursor 可以并行调用 MCP 工具 | 受 Cursor 限制 |

---

## 四、最关键的差异：MCP 工具 vs Agent

这是整个分析最核心的一点：

```
Claude Code Agent（有"大脑"）：
┌───────────────────────────────┐
│  接收任务: "修复 auth bug"      │
│        ▼                       │
│  思考: 我需要先看看代码...      │  ← LLM 在推理
│        ▼                       │
│  调用工具: Grep("auth")         │
│        ▼                       │
│  思考: 找到了，在 validate.ts   │  ← LLM 在推理
│        ▼                       │
│  调用工具: Read("validate.ts")  │
│        ▼                       │
│  思考: 第 42 行有空指针问题     │  ← LLM 在推理
│        ▼                       │
│  调用工具: Edit("validate.ts")  │
│        ▼                       │
│  返回结果给 Coordinator         │
└───────────────────────────────┘

MCP 工具（无"大脑"）：
┌───────────────────────────────┐
│  接收调用: fix_auth_bug()       │
│        ▼                       │
│  执行预定义的代码逻辑           │  ← 写死的逻辑
│        ▼                       │
│  返回结果                       │
└───────────────────────────────┘
```

**Agent 可以"思考" → 多次工具调用 → 再思考 → 再调用**，形成自主的问题解决循环。
**MCP 工具是一次性的函数调用**，不能在内部循环和推理。

---

## 五、最优混合方案

既然 MCP 不能替代 Agent 的"思考"能力，但能补充"工具"能力，最优方案是 **MCP + Subagent + Skills 三者组合**：

### 5.1 架构设计

```
┌────────────────────────────────────────────────────────────┐
│                    Cursor 主 Agent                          │
│  （受 Skills + Rules 约束，拥有"思考"能力）                 │
│                                                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Subagent:    │  │ Subagent:    │  │ Subagent:           │ │
│  │ architect    │  │ code-reviewer│  │ debugger            │ │
│  │ (有独立上下文│  │ (有独立上下文│  │ (有独立上下文       │ │
│  │  和推理能力) │  │  和推理能力) │  │  和推理能力)        │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────────────┘ │
│         │                │                  │                │
│  ┌──────┴──────────────┴──────────────────┴──────────────┐ │
│  │              MCP Server 层（提供增强工具）              │ │
│  │                                                        │ │
│  │  mcp-orchestrator:    任务分解、状态追踪、Agent 通信    │ │
│  │  mcp-code-intel:      代码分析、依赖图、影响范围        │ │
│  │  mcp-test-runner:     智能测试、覆盖率、回归检测        │ │
│  │  mcp-git-ops:         高级 Git 操作、Worktree、PR       │ │
│  └────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

### 5.2 四个 MCP Server 设计

#### MCP 1: `mcp-orchestrator` — 任务编排器

**对应 Claude Code**：`coordinator/coordinatorMode.ts` + `TeamCreateTool` + `SendMessageTool`

```python
@mcp.tool()
def task_decompose(goal: str, context: str) -> str:
    """将高层目标分解为可并行的子任务"""
    # 返回结构化的任务列表

@mcp.tool()
def task_status(task_id: str) -> str:
    """查询任务状态（通过文件系统状态追踪）"""

@mcp.tool()
def agent_message(from_agent: str, to_agent: str, message: str) -> str:
    """Agent 间消息传递（基于文件的消息队列）"""

@mcp.tool()
def worktree_create(branch_name: str) -> str:
    """创建 Git Worktree 隔离环境"""

@mcp.tool()
def worktree_cleanup(worktree_path: str) -> str:
    """清理 Git Worktree"""
```

**价值**：将 Claude Code 的 Coordinator 模式中"不需要推理"的部分提取为工具。
任务分解的"思考"部分仍由主 Agent 或 architect Subagent 完成。

#### MCP 2: `mcp-code-intel` — 代码智能

**对应 Claude Code**：`context.ts` + `tokenEstimation.ts` + Agent 的代码分析能力

```python
@mcp.tool()
def analyze_impact(file_path: str) -> str:
    """分析修改一个文件的影响范围（谁 import 了它）"""

@mcp.tool()
def module_summary(directory: str) -> str:
    """生成模块摘要：公共 API、类型定义、依赖关系"""

@mcp.tool()
def find_test_coverage(file_path: str) -> str:
    """找到覆盖指定文件的所有测试"""

@mcp.tool()
def symbol_references(symbol: str, scope: str) -> str:
    """追踪符号的所有引用（定义→使用→测试）"""

@mcp.tool()
def token_count(file_path: str) -> str:
    """Token 估算 + 读取策略建议"""

@mcp.tool()
def project_map(root_dir: str) -> str:
    """项目结构全景图"""
```

**价值**：Claude Code 的 Agent 在探索代码库时，会执行多轮 Grep + Read + 推理。
这个 MCP 将"数据收集"部分预计算好，Agent 只需调用一次就能获取完整分析。

#### MCP 3: `mcp-test-runner` — 智能测试

**对应 Claude Code**：Worker Agent 运行测试的场景

```python
@mcp.tool()
def smart_test(file_path: str, change_description: str) -> str:
    """根据修改的文件和变更描述，自动选择并运行最相关的测试"""

@mcp.tool()
def regression_check(git_diff: str) -> str:
    """基于 git diff 分析回归风险，运行受影响的测试"""

@mcp.tool()
def test_generate_skeleton(file_path: str) -> str:
    """为源文件生成测试骨架（框架、mock 结构）"""
```

**价值**：Claude Code 的 Worker Agent 需要多步推理才能决定"跑哪个测试"。
这个 MCP 把"推断测试文件 → 检测框架 → 构建命令 → 执行 → 解析结果"一步完成。

#### MCP 4: `mcp-git-ops` — 高级 Git 操作

**对应 Claude Code**：`EnterWorktreeTool` + `/commit` + `/review` + PR 相关

```python
@mcp.tool()
def safe_commit(message: str, files: list[str]) -> str:
    """安全提交：检查 diff、排除 secrets、生成 commit message"""

@mcp.tool()
def create_pr(title: str, body: str, base: str) -> str:
    """创建 PR 并生成描述"""

@mcp.tool()
def review_diff(base_branch: str) -> str:
    """分析当前分支与 base 的差异，生成审查报告"""

@mcp.tool()
def stash_and_switch(branch: str) -> str:
    """安全切换分支（自动 stash 当前变更）"""
```

### 5.3 MCP + Subagent 的协作模式

```
用户: "实现用户认证功能，包括登录、注册、JWT 验证"

主 Agent:
  ▼
  调用 MCP: task_decompose("实现用户认证", context)
  返回: [
    { id: 1, task: "设计 API 接口", type: "architecture" },
    { id: 2, task: "实现注册端点", type: "implementation" },
    { id: 3, task: "实现登录端点", type: "implementation" },
    { id: 4, task: "实现 JWT 中间件", type: "implementation" },
    { id: 5, task: "编写测试", type: "testing" }
  ]
  ▼
  委托 architect Subagent: "设计认证 API 接口"
  ← 返回设计方案
  ▼
  主 Agent 按方案依次实现（利用 Skills 指导工作流）
  ├─ 每个步骤调用 MCP: analyze_impact() 了解影响
  ├─ 实现后调用 MCP: smart_test() 运行相关测试
  └─ 完成后委托 code-reviewer Subagent 审查
  ▼
  如果有问题，委托 debugger Subagent 调试
  ▼
  最终调用 MCP: safe_commit() 提交
```

---

## 六、能达到 Claude Code 多少能力？

### 逐项对比

| Claude Code 多 Agent 能力 | MCP + Subagent 方案 | 达到度 |
|---|---|---|
| **Coordinator 分发任务** | MCP `task_decompose` + 主 Agent 分发 | ~80% |
| **Worker 独立执行** | Subagent 有独立上下文和推理 | ~85% |
| **Agent 间通信** | MCP `agent_message`（文件队列） | ~60% |
| **并行 Worker** | Cursor `Task` 工具可并行启动 Subagent | ~70% |
| **Git Worktree 隔离** | MCP `worktree_create` | ~90% |
| **代码分析能力** | MCP `analyze_impact` + `module_summary` | ~85% |
| **智能测试运行** | MCP `smart_test` + `regression_check` | ~80% |
| **权限分层** | Subagent 自然隔离，MCP 工具按需暴露 | ~70% |
| **任务状态追踪** | MCP `task_status` + TodoWrite | ~75% |
| **Coordinator 专用 Prompt** | 可通过 architect Subagent 模拟 | ~60% |

### 综合评估

| 维度 | Claude Code | MCP + Subagent + Skills |
|---|---|---|
| 多 Agent 编排 | 100% | ~70% |
| 单 Agent 执行质量 | 100% | ~85%（Skills + Rules 增强） |
| 工具丰富度 | 100% | ~90%（MCP 补充自定义工具） |
| Agent 间通信 | 100% | ~60%（受 Cursor 架构限制） |
| 整体综合 | 100% | **~75%** |

### 关键差距在哪？

**1. 真正的并行 Agent（差距最大）**

Claude Code 可以同时运行 5 个 Worker，各自独立思考和执行。
Cursor 的 Subagent 是串行的（主 Agent 等待 Subagent 返回），Task 工具虽然可以后台运行但无法同时启动多个并行的"思考型"Agent。

**2. Agent 间实时通信（差距较大）**

Claude Code 的 Teammate 可以通过 Mailbox 实时互发消息。
MCP 可以实现消息队列，但 Cursor 的 Agent 无法被"唤醒"去检查邮箱——只能在下次被调用时主动检查。

**3. Coordinator 专用模式（差距中等）**

Claude Code 有专门的 Coordinator 模式，只保留 4 个工具，system prompt 完全聚焦于"编排"而非"执行"。
Cursor 没有等价物——主 Agent 始终拥有所有工具，无法强制它"只编排不执行"。

---

## 七、实施建议

### 优先级排序

| 优先级 | 实施内容 | 投入 | 收益 |
|---|---|---|---|
| P0 | `mcp-code-intel`（代码分析工具） | 中 | 高 — 补充最缺失的分析能力 |
| P0 | 3 个 Subagent（已完成） | 低 | 高 — 提供独立推理能力 |
| P1 | `mcp-test-runner`（智能测试） | 中 | 高 — 质量保证大幅提升 |
| P1 | `mcp-git-ops`（高级 Git 操作） | 低 | 中 — 简化常见 Git 工作流 |
| P2 | `mcp-orchestrator`（任务编排） | 高 | 中 — 受 Cursor 并行限制，效果打折 |

### 不建议做的

- **不要试图在 MCP Server 中嵌入 LLM 调用** — 这会导致双重收费（Cursor 的 LLM + MCP 的 LLM），且延迟翻倍。
- **不要试图用 MCP 实现完整的 Agent Loop** — 这是 Cursor 内部的职责，MCP 工具是"被调用"的，不能"主动运行循环"。
- **不要创建超过 5 个 MCP Server** — 每个 MCP Server 都是一个常驻进程，过多会影响系统资源和 Cursor 启动速度。

---

## 八、总结

```
Claude Code 多 Agent 体系:
  Coordinator ──→ Worker A ──→ 执行
              ├──→ Worker B ──→ 执行    （真正并行，独立思考）
              └──→ Worker C ──→ 执行
  Agent 间通过 Mailbox 实时通信

Cursor 最优替代方案:
  主 Agent ──→ architect Subagent ──→ 设计（独立上下文）
          ├──→ code-reviewer Subagent ──→ 审查（独立上下文）
          └──→ debugger Subagent ──→ 调试（独立上下文）
  MCP 层提供增强工具（代码分析、测试、Git、编排辅助）
  Skills 层引导行为（工作流、工具策略、上下文管理、质量）
  Rules 层确保底线（先读再改、必须验证、失败停止）
```

**一句话**：MCP Server 是"更强的手"（增强工具能力），Subagent 是"更多的脑"（增加推理实体），两者结合是 Cursor 框架内最接近 Claude Code 多 Agent 体系的方案。但受限于 Cursor 不支持真正的并行 Agent 思考和 Agent 间实时通信，完全等价是不可能的——这是架构层面的根本差异。

---

*本分析基于 Claude Code 源码中 AgentTool、TeamCreateTool、SendMessageTool、coordinatorMode 的完整实现，与 Cursor 的 MCP/Subagent 机制进行的深度对比。*
