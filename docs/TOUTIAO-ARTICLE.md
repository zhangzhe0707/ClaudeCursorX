# 我把 Claude Code 的能力"移植"到了 Cursor，开源了

最近 Claude Code 火了，很多人都在夸它的 Agent 能力强——自动规划、工具调用、多 Agent 协作、跨会话记忆……但 Claude Code 是命令行工具，没有 IDE 的代码补全、文件树、调试器。

而 Cursor 有完整的 IDE 体验，却缺乏 Claude Code 那种深度 Agent 能力。

两边各有优势，能不能结合？

我花了几周时间深度分析了 Claude Code 的源码，研究它的核心架构，然后做了这个项目：**ClaudeCursorX**——让 Cursor 拥有接近 Claude Code 的 Agent 能力。

---

## 它能做什么？

一句话：**让 Cursor 里的 AI 更聪明、更能干、更靠谱**。

具体来说，安装后你会获得：

- 📦 **34 个 MCP 工具**：代码影响分析、智能测试、安全 Git 提交、发布说明生成、跨会话记忆……
- 🧠 **4 套行为技能**：教会 AI 如何规划任务、选择工具、管理上下文、把关代码质量
- 📏 **4 条硬约束规则**：先读后写、改后验证、搜索优先——杜绝 AI 的"盲目操作"
- 👥 **4 个专家 Subagent**：架构师、代码审查员、调试专家、安全审计员，按需召唤

---

## 为什么要做这个？

Claude Code 之所以强，不是因为用了更好的模型，而是因为它有一套**完整的工程体系**：

- **Agent Loop**：计划 → 行动 → 观察 → 再计划，循环迭代直到完成
- **40+ 工具**：bash、文件读写、代码搜索、测试运行……覆盖开发全流程
- **多 Agent 编排**：复杂任务拆解给多个子 Agent 并行处理
- **持久化记忆**：跨会话记住项目约定、踩过的坑、团队规范

Cursor 有 MCP 协议、有 Rules、有 Skills，理论上可以实现以上所有能力。但官方没有提供这套完整的"工程体系"。

ClaudeCursorX 就是把这套体系搬过来——针对 Cursor 的架构特点重新设计，开箱即用。

---

## 四层架构设计

```
Layer 1: MCP Servers    → 给 AI 装上"手"（工具）
Layer 2: Skills         → 教 AI "怎么做"（行为注入）
Layer 3: Rules          → 告诉 AI "不能做什么"（硬约束）
Layer 4: Subagents      → 让 AI "找专家"（角色分工）
```

每一层都有具体的设计理由，不是堆砌功能，而是对 Claude Code 工程体系的系统性复现。

---

## 安装只需一条命令

```bash
git clone https://github.com/zhangzhe0707/ClaudeCursorX.git
cd ClaudeCursorX
pip install -r requirements.txt

# 安装到你的项目
./install.sh /path/to/your/project
```

然后用 Cursor 打开你的项目，所有能力自动生效。

---

## 一些真实的使用体验

安装后在 Cursor 里直接说：

> "帮我分析这次修改会影响哪些模块"

AI 会自动调用 `analyze_impact` 工具，给出精确的影响范围分析，而不是靠"猜"。

> "运行一下回归测试"

AI 调用 `regression_check`，自动识别测试框架，运行相关测试，反馈结果。

> "把这次改动整理成发布说明"

AI 调用 `release_notes`，读取 git log，自动生成结构化的发布说明。

最让我满意的是**记忆系统**：`memory_save` 可以把重要的架构决策、踩坑记录保存下来，下次打开项目 AI 还记得，不用反复解释背景。

---

## 和 Claude Code 的差距还有多大？

坦率说，还有差距，主要在两点：

1. **并发工具调用**：Cursor 的 MCP 调用是串行的，Claude Code 可以并发调用多个工具
2. **真正的多 Agent 并行**：Claude Code 可以真正 fork 出并行的子 Agent，Cursor 目前是顺序协调

但对于绝大多数日常开发任务，这套方案已经足够用了。

详细的差距分析见项目 `docs/GAP-ANALYSIS.md`。

---

## 开源地址

GitHub：https://github.com/zhangzhe0707/ClaudeCursorX

欢迎 Star、Fork、提 Issue，也欢迎贡献新的 MCP 工具或 Skill。

如果你也在用 Cursor，不妨试试——装上之后你会发现 AI 的行为明显变得更"专业"了。
