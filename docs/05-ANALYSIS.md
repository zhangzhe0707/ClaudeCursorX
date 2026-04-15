# Claude Code 源码运行逻辑分析

> 基于 `claude` 分支的 TypeScript 源码快照（2026-03-31 公开暴露版本）进行的架构与运行逻辑分析。

---

## 一、技术栈概览

| 类别 | 技术 |
|------|------|
| 运行时 | [Bun](https://bun.sh) |
| 语言 | TypeScript (strict) |
| 终端 UI | [React](https://react.dev) + [Ink](https://github.com/vadimdemedes/ink) |
| CLI 解析 | [Commander.js](https://github.com/tj/commander.js) (extra-typings) |
| Schema 验证 | [Zod v4](https://zod.dev) |
| 代码搜索 | [ripgrep](https://github.com/BurntSushi/ripgrep) |
| 协议 | [MCP SDK](https://modelcontextprotocol.io)、LSP |
| API | [Anthropic SDK](https://docs.anthropic.com) |
| 遥测 | OpenTelemetry + gRPC |
| 特性开关 | GrowthBook + `bun:bundle` 编译时 DCE（死代码消除） |
| 认证 | OAuth 2.0、JWT、macOS Keychain |

**规模**：~1,900 文件，512,000+ 行代码

---

## 二、项目目录结构

```text
src/
├── main.tsx                 # 主入口（Commander.js CLI + React/Ink 渲染器）
├── commands.ts              # 命令注册中心
├── tools.ts                 # 工具注册中心
├── Tool.ts                  # 工具类型定义
├── QueryEngine.ts           # LLM 查询引擎（核心编排器）
├── query.ts                 # 查询主循环（Agent Loop）
├── context.ts               # 系统/用户上下文收集
├── cost-tracker.ts          # Token 成本追踪
│
├── commands/                # 斜杠命令实现 (~70+)
├── tools/                   # Agent 工具实现 (~40+)
├── components/              # Ink UI 组件 (~140)
├── hooks/                   # React Hooks
├── services/                # 外部服务集成
│   ├── api/                 # Anthropic API 客户端
│   ├── mcp/                 # MCP 服务器连接管理
│   ├── oauth/               # OAuth 认证流程
│   ├── lsp/                 # Language Server Protocol
│   ├── analytics/           # GrowthBook 特性开关 + 分析
│   ├── compact/             # 对话上下文压缩
│   └── tools/               # 工具执行编排
├── screens/                 # 全屏 UI（Doctor、REPL、Resume）
├── types/                   # TypeScript 类型定义
├── utils/                   # 工具函数
│
├── bridge/                  # IDE 双向通信桥（VS Code、JetBrains）
├── coordinator/             # 多 Agent 协调器
├── plugins/                 # 插件系统
├── skills/                  # Skill 系统
├── keybindings/             # 快捷键配置
├── vim/                     # Vim 模式
├── voice/                   # 语音输入
├── remote/                  # 远程会话
├── server/                  # Server 模式
├── memdir/                  # 持久化记忆目录
├── tasks/                   # 任务管理
├── state/                   # 状态管理
├── migrations/              # 配置迁移
├── schemas/                 # 配置 Schema（Zod）
├── entrypoints/             # 初始化逻辑
├── ink/                     # Ink 渲染器封装
├── buddy/                   # Companion Sprite
├── native-ts/               # 原生 TypeScript 工具
├── outputStyles/            # 输出样式
├── query/                   # 查询管线
└── upstreamproxy/           # 代理配置
```

---

## 三、启动流程（入口 → REPL）

启动分为 **三个阶段**：

### 阶段 1：Bootstrap 入口 (`src/entrypoints/cli.tsx`)

```typescript
async function main(): Promise<void> {
  const args = process.argv.slice(2);

  // 快速路径：--version 零模块加载
  if (args[0] === '--version') {
    console.log(`${MACRO.VERSION} (Claude Code)`);
    return;
  }

  // 其他快速路径：--dump-system-prompt、bridge、daemon、ps/logs/attach/kill
  // 所有模块通过 await import() 动态加载，最大限度减少启动时间
  // ...

  // 主流程：加载完整 CLI
  const { createProgram } = await import('../main.js');
  await createProgram();
}
```

- 最外层入口，只做**命令分发**
- 针对 `--version`、`--dump-system-prompt`、`daemon`、`bridge`、`ps/logs/attach/kill` 等设计了**快速路径**，不加载完整模块
- 所有模块通过 `await import()` **动态加载**，最大限度减少启动时间

### 阶段 2：并行预取 + CLI 解析 (`src/main.tsx`)

```typescript
// 这些副作用必须在所有其他 import 之前运行：
// 1. profileCheckpoint 在重模块评估开始前标记入口
// 2. startMdmRawRead 启动 MDM 子进程，与后续 ~135ms 的 import 并行运行
// 3. startKeychainPrefetch 并行启动两个 macOS keychain 读取
import { profileCheckpoint } from './utils/startupProfiler.js';
profileCheckpoint('main_tsx_entry');

import { startMdmRawRead } from './utils/settings/mdm/rawRead.js';
startMdmRawRead();

import { startKeychainPrefetch } from './utils/secureStorage/keychainPrefetch.js';
startKeychainPrefetch();
```

**关键设计模式 — 并行预取**：在模块评估（约 135ms）期间，并行启动三件事：

1. **MDM 设置读取**（macOS 的 `plutil` / Windows 的 `reg query`）
2. **Keychain 预取**（OAuth token + API key，两次读取并行化）
3. **GrowthBook 特性开关初始化**

然后通过 Commander.js 解析完整的 CLI 参数（`--model`、`--permission-mode`、`--resume`、`--tools` 等约 40+ 个选项）。

### 阶段 3：初始化 + 启动 REPL

```typescript
// src/entrypoints/init.ts
export const init = memoize(async (): Promise<void> => {
  enableConfigs();                        // 启用配置系统
  applySafeConfigEnvironmentVariables();  // 应用安全的环境变量
  applyExtraCACertsFromConfig();          // TLS 证书
  setupGracefulShutdown();                // 优雅退出
  configureGlobalAgents();                // HTTP 代理
  // ... 预连接 API、初始化遥测等
});
```

初始化完成后，`main.tsx` 创建 Ink 渲染器，将 React 组件树挂载到终端：

```typescript
// src/replLauncher.tsx
export async function launchRepl(root, appProps, replProps, renderAndRun) {
  const { App } = await import('./components/App.js');
  const { REPL } = await import('./screens/REPL.js');
  await renderAndRun(root, <App {...appProps}><REPL {...replProps} /></App>);
}
```

**组件树结构**：`App`（全局状态 Provider）→ `REPL`（主交互界面）

---

## 四、核心交互循环（Agent Loop）

这是整个系统最关键的部分，实现了一个 **Tool-Use Loop**（工具调用循环）。

### 4.1 用户输入处理

`REPL` 组件接收用户输入文本，封装为 `UserMessage`，然后调用 `onQuery`：

```typescript
// src/screens/REPL.tsx
const handleIncomingPrompt = useCallback((content: string) => {
  if (queryGuard.isActive) return false;
  const userMessage = createUserMessage({ content });
  void onQuery([userMessage], newAbortController, true, [], mainLoopModel);
  return true;
}, [onQuery, mainLoopModel]);
```

### 4.2 QueryEngine — 查询引擎 (`src/QueryEngine.ts`)

`QueryEngine` 是核心编排器，约 46K 行，职责包括：

- 构建 **System Prompt**（系统提示词，包含工具描述、用户上下文、git 状态等）
- 加载 **Memory**（持久化记忆，来自 `.claude/` 目录）
- 管理 **对话历史**
- 调用 `query()` 函数执行主循环
- 处理 **自动压缩**（autoCompact）
- 跟踪 **token 成本**

### 4.3 Query 主循环 (`src/query.ts`)

`query()` 是一个 **AsyncGenerator**（异步生成器），实现了流式的事件驱动架构：

```typescript
export async function* query(params: QueryParams): AsyncGenerator<
  StreamEvent | RequestStartEvent | Message | TombstoneMessage | ToolUseSummaryMessage,
  Terminal
> {
  const consumedCommandUuids: string[] = [];
  const terminal = yield* queryLoop(params, consumedCommandUuids);
  for (const uuid of consumedCommandUuids) {
    notifyCommandLifecycle(uuid, 'completed');
  }
  return terminal;
}
```

**主循环流程**：

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

`queryLoop` 内部维护了一个可变状态对象 `State`，跨迭代传递：

```typescript
type State = {
  messages: Message[]
  toolUseContext: ToolUseContext
  autoCompactTracking: AutoCompactTrackingState | undefined
  maxOutputTokensRecoveryCount: number
  hasAttemptedReactiveCompact: boolean
  maxOutputTokensOverride: number | undefined
  pendingToolUseSummary: Promise<ToolUseSummaryMessage | null> | undefined
  stopHookActive: boolean | undefined
  turnCount: number
  transition: Continue | undefined
}
```

### 4.4 API 调用 (`src/services/api/claude.ts`)

```typescript
export async function* queryModelWithStreaming({
  messages, systemPrompt, thinkingConfig, tools, signal, options,
}): AsyncGenerator<StreamEvent | AssistantMessage | SystemAPIErrorMessage, void> {
  return yield* withStreamingVCR(messages, async function* () {
    yield* queryModel(messages, systemPrompt, thinkingConfig, tools, signal, options);
  });
}
```

底层核心调用：

```typescript
// 使用原始流而不是 BetaMessageStream，避免 O(n²) 的 JSON 部分解析开销
const result = await anthropic.beta.messages
  .create({ ...params, stream: true }, { signal })
  .withResponse();
```

**关键优化**：使用原始流（raw stream）而非 SDK 的 `BetaMessageStream`，因为后者在每个 `input_json_delta` 上调用 `partialParse()`，而 Claude Code 自己处理工具输入累积。

### 4.5 工具执行 (`src/services/tools/toolOrchestration.ts`)

```typescript
export async function* runTools(
  toolUseMessages: ToolUseBlock[],
  assistantMessages: AssistantMessage[],
  canUseTool: CanUseToolFn,
  toolUseContext: ToolUseContext,
): AsyncGenerator<MessageUpdate, void> {
  for (const { isConcurrencySafe, blocks } of partitionToolCalls(toolUseMessages, toolUseContext)) {
    if (isConcurrencySafe) {
      // 只读工具并发执行
      for await (const update of runToolsConcurrently(blocks, ...)) { ... }
    } else {
      // 写操作工具串行执行
      for await (const update of runToolsSerially(blocks, ...)) { ... }
    }
  }
}
```

**关键设计**：工具调用被分为两类：

- **只读工具**（Glob、Grep、FileRead 等）→ **并发执行**
- **写操作工具**（BashTool、FileWrite、FileEdit 等）→ **串行执行**

单个工具的执行流程（`toolExecution.ts`）：

```
findToolByName() → 权限检查 → 输入处理 → tool.call() → 后置 hooks → 返回结果
```

---

## 五、工具系统 (`src/tools/`)

### 5.1 工具注册表 (`src/tools.ts`)

```typescript
export function getAllBaseTools(): Tools {
  return [
    AgentTool,          // 子 Agent 生成
    TaskOutputTool,     // 任务输出
    BashTool,           // Shell 命令执行
    GlobTool,           // 文件模式匹配搜索
    GrepTool,           // ripgrep 内容搜索
    FileReadTool,       // 文件读取（支持图片、PDF、笔记本）
    FileWriteTool,      // 文件创建/覆写
    FileEditTool,       // 文件部分修改（字符串替换）
    NotebookEditTool,   // Jupyter 笔记本编辑
    WebFetchTool,       // URL 内容抓取
    WebSearchTool,      // 网页搜索
    TodoWriteTool,      // 待办事项管理
    // ... 40+ 工具
  ];
}
```

### 5.2 工具完整列表

| 工具 | 说明 |
|---|---|
| `BashTool` | Shell 命令执行 |
| `FileReadTool` | 文件读取（图片、PDF、笔记本） |
| `FileWriteTool` | 文件创建/覆写 |
| `FileEditTool` | 部分文件修改（字符串替换） |
| `GlobTool` | 文件模式匹配搜索 |
| `GrepTool` | ripgrep 内容搜索 |
| `WebFetchTool` | URL 内容抓取 |
| `WebSearchTool` | 网页搜索 |
| `AgentTool` | 子 Agent 生成 |
| `SkillTool` | Skill 执行 |
| `MCPTool` | MCP 服务器工具调用 |
| `LSPTool` | Language Server Protocol 集成 |
| `NotebookEditTool` | Jupyter 笔记本编辑 |
| `TaskCreateTool` / `TaskUpdateTool` | 任务创建和管理 |
| `SendMessageTool` | Agent 间消息传递 |
| `TeamCreateTool` / `TeamDeleteTool` | 团队 Agent 管理 |
| `EnterPlanModeTool` / `ExitPlanModeTool` | Plan 模式切换 |
| `EnterWorktreeTool` / `ExitWorktreeTool` | Git Worktree 隔离 |
| `ToolSearchTool` | 延迟工具发现 |
| `CronCreateTool` | 定时触发器创建 |
| `RemoteTriggerTool` | 远程触发 |
| `SleepTool` | Proactive 模式等待 |
| `SyntheticOutputTool` | 结构化输出生成 |
| `ConfigTool` | 配置管理 |
| `TodoWriteTool` | 待办事项 |
| `AskUserQuestionTool` | 向用户提问 |

### 5.3 工具类型定义 (`src/Tool.ts`)

每个 Tool 是一个标准化模块，定义：

- `name` — 工具名称
- `description` — 工具描述（给 LLM 看的）
- `inputSchema` — Zod/JSON Schema 输入定义
- `isReadOnly()` — 是否只读（影响并发策略）
- `isEnabled()` — 是否可用
- `needsPermissions()` — 权限需求
- `call()` — 实际执行逻辑

### 5.4 特性开关门控

通过 `bun:bundle` 的 `feature()` 实现**编译时死代码消除**：

```typescript
const SleepTool = feature('PROACTIVE') || feature('KAIROS')
  ? require('./tools/SleepTool/SleepTool.js').SleepTool
  : null;
```

重要特性开关：`PROACTIVE`、`KAIROS`、`BRIDGE_MODE`、`DAEMON`、`VOICE_MODE`、`AGENT_TRIGGERS`、`MONITOR_TOOL`、`COORDINATOR_MODE`、`CONTEXT_COLLAPSE`

---

## 六、命令系统 (`src/commands/`)

约 **70+ 个斜杠命令**，分为几类：

| 类别 | 命令 |
|---|---|
| Git 操作 | `/commit`、`/review`、`/diff`、`/pr_comments` |
| 配置管理 | `/config`、`/permissions`、`/theme`、`/vim` |
| 会话管理 | `/resume`、`/share`、`/compact`、`/cost`、`/rename` |
| 认证 | `/login`、`/logout` |
| 诊断 | `/doctor`、`/status`、`/usage` |
| 高级 | `/tasks`、`/skills`、`/mcp`、`/agents`、`/plugins` |
| 开发 | `/context`、`/memory`、`/hooks`、`/branch` |

同样使用 `feature()` 门控按需加载。

---

## 七、权限系统 (`src/hooks/toolPermission/`)

### 权限模式

| 模式 | 说明 |
|---|---|
| `default` | 每次写操作都询问用户 |
| `plan` | 只允许只读操作 |
| `auto` | AI 自动判断（带安全分类器） |
| `bypassPermissions` | 跳过所有权限检查（`--dangerously-skip-permissions`） |

### 权限检查流程

```
工具调用请求
     │
     ▼
┌─ 权限规则匹配 ─┐
│  alwaysAllow   │ → 直接放行
│  alwaysDeny    │ → 直接拒绝
│  alwaysAsk     │ → 弹出确认
└───────┬────────┘
        │ (无匹配规则)
        ▼
┌─ Hook 检查 ─┐
│  permissionRequest hooks  │
└──────┬──────┘
       │ (未决)
       ▼
┌─ 分类器 (Bash only) ─┐
│  BASH_CLASSIFIER       │
└──────┬──────┘
       │ (未决)
       ▼
┌─ 交互式对话框 ─┐
│  用户手动批准/拒绝  │
│  + Bridge 远程批准   │
│  + Channel 通道批准  │
└──────────────┘
```

### 多来源竞争

权限对话框支持多个来源竞争，先到先得（`claim()` 原子操作）：
- 本地用户在终端批准
- Bridge（VS Code/JetBrains IDE）远程批准
- Channel（MCP 通道）批准
- 分类器自动批准

---

## 八、上下文系统 (`src/context.ts`)

### 系统提示词构成

系统提示词由多层上下文组成：

1. **System Context** — 日期、OS、shell、git 状态
2. **User Context** — 用户配置、CLAUDE.md 文件内容
3. **Memory** — `.claude/` 目录中的持久化记忆
4. **工具描述** — 所有可用工具的 schema
5. **MCP 资源** — 外部 MCP 服务器提供的资源

### Git 上下文收集

```typescript
const [branch, mainBranch, status, log, userName] = await Promise.all([
  getBranch(),
  getDefaultBranch(),
  execFileNoThrow(gitExe(), ['status', '--short'], ...),
  execFileNoThrow(gitExe(), ['log', '--oneline', '-n', '5'], ...),
  execFileNoThrow(gitExe(), ['config', 'user.name'], ...),
]);
```

并行获取 5 项 git 信息，状态超过 2000 字符时截断。

---

## 九、自动压缩系统 (`src/services/compact/`)

### 压缩触发条件

```typescript
export function getAutoCompactThreshold(model: string): number {
  const effectiveContextWindow = getEffectiveContextWindowSize(model);
  return effectiveContextWindow - AUTOCOMPACT_BUFFER_TOKENS;
}
```

当 token 用量接近上下文窗口阈值时触发自动压缩。

### 压缩策略层级

```
1. Session Memory Compaction — 优先尝试会话记忆压缩
2. Standard Compact — 标准对话摘要压缩
3. Microcompact — 轻量级微压缩
4. Reactive Compact — 被动压缩（API 返回 prompt-too-long 时）
5. Context Collapse — 实验性上下文折叠
```

### 熔断机制

```typescript
const MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3;
// 连续失败 3 次后停止尝试，避免无意义的 API 调用
```

---

## 十、多 Agent 协调 (`src/coordinator/`)

### Agent 生成

- `AgentTool` — 在主 Agent 中生成**子 Agent**
- `TeamCreateTool` — 创建团队级并行 Agent
- `SendMessageTool` — Agent 间消息传递

### 协调模式

```typescript
const coordinatorModeModule = feature('COORDINATOR_MODE')
  ? require('./coordinator/coordinatorMode.js')
  : null;
```

- `coordinator/` 处理多 Agent 编排
- 子 Agent 运行在受限的工具集中（通过 `ALL_AGENT_DISALLOWED_TOOLS` 限制）
- Swarm Worker 权限通过邮箱系统转发给 Leader 批准

---

## 十一、Bridge 系统 (`src/bridge/`)

双向通信层，连接 IDE 扩展（VS Code、JetBrains）与 CLI：

| 模块 | 说明 |
|---|---|
| `bridgeMain.ts` | Bridge 主循环 |
| `bridgeMessaging.ts` | 消息协议 |
| `bridgePermissionCallbacks.ts` | 权限回调 |
| `replBridge.ts` | REPL 会话桥接 |
| `jwtUtils.ts` | JWT 认证 |
| `sessionRunner.ts` | 会话执行管理 |

---

## 十二、服务层 (`src/services/`)

| 服务 | 说明 |
|---|---|
| `api/` | Anthropic API 客户端、文件 API、Bootstrap |
| `mcp/` | Model Context Protocol 服务器连接管理 |
| `oauth/` | OAuth 2.0 认证流程 |
| `lsp/` | Language Server Protocol 管理器 |
| `analytics/` | GrowthBook 特性开关 + 分析 |
| `plugins/` | 插件加载器 |
| `compact/` | 对话上下文压缩 |
| `policyLimits/` | 组织策略限制 |
| `remoteManagedSettings/` | 远程托管设置 |
| `extractMemories/` | 自动记忆提取 |
| `tokenEstimation.ts` | Token 计数估算 |
| `teamMemorySync/` | 团队记忆同步 |
| `tools/` | 工具执行编排（串行/并发） |

---

## 十三、状态管理 (`src/state/`)

使用自定义的 **Store** 模式（类似 Redux），管理全局 `AppState`：

```typescript
export type AppState = {
  messages: Message[]
  toolPermissionContext: ToolPermissionContext
  mcpClients: MCPServerConnection[]
  plugins: LoadedPlugin[]
  speculationState: SpeculationState
  // ... 50+ 个状态字段
}
```

### 推测执行（Speculation）

```typescript
export type SpeculationState =
  | { status: 'idle' }
  | {
      status: 'active'
      id: string
      abort: () => void
      messagesRef: { current: Message[] }
      writtenPathsRef: { current: Set<string> }
      boundary: CompletionBoundary | null
      // ...
    }
```

在用户输入前预测可能的操作，提前开始执行以减少延迟。

---

## 十四、设计模式总结

### 1. 并行预取（Parallel Prefetch）

启动时间优化，在模块评估期间并行启动 MDM 读取、Keychain 预取和 API 预连接：

```typescript
startMdmRawRead();        // 与后续 import 并行
startKeychainPrefetch();  // 与后续 import 并行
```

### 2. 懒加载（Lazy Loading）

重模块（OpenTelemetry、gRPC、分析、特性门控子系统）通过 `import()` 延迟到实际需要时：

```typescript
const { initializeTelemetry } = await import('./services/instrumentation.js');
```

### 3. 编译时死代码消除（DCE）

```typescript
const voiceCommand = feature('VOICE_MODE')
  ? require('./commands/voice/index.js').default
  : null;
// VOICE_MODE 为 false 时，整个 require 分支在构建时被移除
```

### 4. AsyncGenerator 事件流

整个查询管线使用 `yield*` 串联，实现统一的流式事件传播：

```
query() ──yield*──> queryLoop() ──yield*──> callModel() ──yield──> StreamEvent
                                  ──yield*──> runTools() ──yield──> Message
                                  ──yield*──> stopHooks() ──yield──> Message
```

### 5. Agent Swarms（Agent 群集）

子 Agent 通过 `AgentTool` 生成，团队 Agent 通过 `TeamCreateTool` 创建，支持并行工作。

### 6. Skill 系统

可复用的工作流定义在 `skills/` 中，通过 `SkillTool` 执行。用户可以添加自定义 Skill。

### 7. 插件架构

内置和第三方插件通过 `plugins/` 子系统加载。

---

## 十五、整体数据流

```
用户输入
   │
   ▼
┌─────────┐     ┌───────────────┐     ┌──────────────────┐
│ REPL.tsx │────▶│ QueryEngine.ts│────▶│   query.ts       │
│ (React)  │     │ (编排器)      │     │ (主循环)          │
└─────────┘     └───────────────┘     └──────────────────┘
                                             │
                                    ┌────────┴────────┐
                                    ▼                  ▼
                          ┌──────────────┐    ┌──────────────────┐
                          │ claude.ts    │    │ toolOrchestration │
                          │ (API 调用)   │    │ (工具执行)        │
                          └──────────────┘    └──────────────────┘
                                │                      │
                                ▼                      ▼
                          ┌──────────┐        ┌────────────────┐
                          │ Anthropic│        │ BashTool       │
                          │ API      │        │ FileReadTool   │
                          │ (Claude) │        │ FileWriteTool  │
                          └──────────┘        │ GrepTool ...   │
                                              └────────────────┘
                                                       │
                                              tool_result 返回
                                                       │
                                              ◄────────┘
                                    继续循环直到 LLM 不再调用工具
                                                       │
                                                       ▼
                                              最终文本响应
                                              渲染到终端
```

---

## 十六、关键文件索引

| 文件 | 行数 | 说明 |
|---|---|---|
| `src/QueryEngine.ts` | ~46K | 核心查询引擎，LLM API 调用编排 |
| `src/Tool.ts` | ~29K | 工具基础类型定义、权限模型、进度类型 |
| `src/commands.ts` | ~25K | 命令注册和执行，条件导入 |
| `src/main.tsx` | ~4.6K | Commander.js CLI 解析 + React/Ink 渲染器初始化 |
| `src/query.ts` | ~1.7K | 查询主循环（Agent Loop） |
| `src/tools.ts` | ~390 | 工具注册表（getAllBaseTools） |
| `src/context.ts` | ~190 | 系统/用户上下文收集 |
| `src/services/api/claude.ts` | ~3.3K | Anthropic API 调用、流式处理、重试 |
| `src/services/tools/toolOrchestration.ts` | ~180 | 工具执行编排（并发/串行分区） |
| `src/services/tools/toolExecution.ts` | ~1.7K | 单个工具执行（权限→调用→后置 hook） |
| `src/services/compact/autoCompact.ts` | ~350 | 自动上下文压缩 |
| `src/hooks/toolPermission/` | 多文件 | 权限系统（交互式/分类器/Hook） |
| `src/state/AppStateStore.ts` | ~570 | 全局状态定义 |
| `src/screens/REPL.tsx` | ~5K | 主交互界面（React/Ink） |

---

*本分析基于 2026-03-31 公开暴露的 Claude Code TypeScript 源码快照，用于教育和架构学习目的。*
