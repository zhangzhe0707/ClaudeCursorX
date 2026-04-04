# Auto Test & Fix — 编码后自动测试与修复编排

> 本 Skill 提供"编码完成 → 智能测试发现 → 自动修复循环 → 结果上报"的完整编排流程。
> 与 `auto-test-fix.mdc` Rule 配合使用：Rule 规定**何时触发**，本 Skill 规定**如何执行**。

Use this skill whenever you have just completed a code change that involves logic, functions, or APIs and need to verify correctness.

---

## Phase 1: Smart Test Discovery（测试发现）

在运行测试之前，先找到**最相关的测试文件**：

### 优先级策略

```
Priority 1: 同名测试文件
  src/utils/parser.py  →  tests/test_parser.py / tests/utils/test_parser.py

Priority 2: 同模块测试
  src/auth/token.ts    →  src/auth/__tests__/token.test.ts
                          src/auth/token.spec.ts

Priority 3: 集成测试（包含该模块的测试）
  用 Grep 搜索: import {changed_symbol} / from changed_module

Priority 4: regression_check（基于 git diff 自动发现）
  当上述方法找不到测试时，使用 regression_check 扫描整个变更影响范围
```

### 工具调用

```python
# 优先用 smart_test（最精准）
smart_test(file_path="<changed_file>", project_dir="<root>")

# 找不到关联测试时用 regression_check
regression_check(project_dir="<root>", base_branch="main")
```

---

## Phase 2: Parallel Test Execution（并行测试，适合大型项目）

当有**多个独立测试文件**需要运行时，使用并行 Agent：

```
发现 3 个相关测试文件:
  - tests/test_auth.py
  - tests/test_api.py
  - tests/test_utils.py

→ 使用 Task 工具并行启动 3 个 shell agent 分别运行
→ 等待全部返回
→ 合并结果：汇总通过/失败数
```

**何时并行 vs 串行：**

| 条件 | 策略 |
|------|------|
| 测试文件互相独立 | 并行 (Task 工具) |
| 测试有共享状态（数据库、文件） | 串行 |
| 只有 1-2 个测试文件 | 串行（无需并行开销） |
| 测试套件极慢（> 60s） | 并行 + 只运行失败的 |

---

## Phase 3: Fix Loop（自动修复循环）

这是核心算法。每次迭代都要严格遵守：

```
MAX_ITERATIONS = 3
iteration = 0

LOOP:
  ─── 分析阶段 ───────────────────────────────────────
  1. 解析失败输出：
     - 提取：失败的测试名 + 断言错误 + 堆栈最后几行
     - 定位：错误发生在哪个文件哪一行
     - 归因：是我的改动直接导致，还是间接影响，还是预存失败？

  ─── 决策树 ─────────────────────────────────────────
  if 归因 == "我的改动直接导致":
      → 进入修复步骤
  elif 归因 == "预存失败（改动前就失败）":
      → 注明"pre-existing failure"，跳过修复，标记为 Known
  elif 归因 == "测试断言逻辑错误":
      → 向用户解释，等待确认，**不单方面修改测试**
  elif 归因 == "环境/依赖问题":
      → 记录，不修复（超出代码修复范围）

  ─── 修复步骤（仅当归因为"我的改动导致"）─────────────
  2. 制定最小修复方案（改动越小越好）
  3. Read 相关文件（确认当前内容）
  4. StrReplace 修复
  5. ReadLints 验证（无新 lint 错误）
  6. 重新运行 smart_test

  ─── 迭代控制 ───────────────────────────────────────
  iteration += 1
  if all_passed OR iteration >= MAX_ITERATIONS: break
```

### 常见失败模式与修复策略

| 失败模式 | 典型表现 | 修复策略 |
|----------|----------|----------|
| 返回值变化 | `AssertionError: expected X, got Y` | 检查函数返回值类型/结构 |
| 参数签名变更 | `TypeError: missing argument` | 检查函数调用方 |
| 副作用改变 | 状态断言失败 | 检查状态修改逻辑 |
| 导入错误 | `ImportError / ModuleNotFoundError` | 检查 import 路径 |
| 异步问题 | `RuntimeError: coroutine was never awaited` | 检查 async/await 一致性 |
| 类型错误 | TypeScript/mypy 类型不匹配 | 修正类型标注 |

---

## Phase 4: Escalation（上报决策）

当 Fix Loop 结束后，按以下标准决定是否上报：

```
if all_passed:
    → 不需要上报，在 completion summary 写 "Tests ✓ (N passed)"

elif only_preexisting_failures:
    → 不阻塞，在 summary 写 "Tests ✓ (N passed, M pre-existing known failures)"
    → 可选：用 memory_save 记录已知失败，供下次会话参考

elif my_fix_failed_after_3_iterations:
    → 停止，明确上报：
      "⚠ Tests failing after 3 fix attempts:
       - Test: <name>
       - Error: <message>
       - Attempted fixes: <list>
       - Recommended next step: <suggestion>"
    → 询问用户是否需要进一步协助

elif test_env_issue:
    → 在 summary 写 "Tests ⚠ (could not run: <reason>)"
    → 提示用户手动运行
```

---

## Phase 5: Summary Format（摘要格式）

在所有测试完成后，在任务摘要中输出标准化的测试状态行：

```
**Tests:**
- ✓ test_token.py — 12/12 passed (0.8s)
- ✓ test_auth.py  — 8/8 passed (1.2s)
- ✗ test_api.py   — 5/7 passed (2 failed, fixed in 2 iterations)
  └─ Fixed: incorrect status code in error handler
```

---

## 反模式（严禁）

- ❌ 为了让测试通过而删除或降低断言标准
- ❌ 修改测试期望值而不理解为什么期望值该变
- ❌ 超过 3 次后继续盲目尝试（应该上报）
- ❌ 隐藏测试失败，假装已完成
- ❌ 在测试失败时跳过测试直接声明 "done"

---

## 与其他 Skill 的分工

| Skill | 职责 |
|-------|------|
| **auto-test-fix** (本 Skill) | 发现测试、运行测试、修复失败、上报结果 |
| **quality-gate** | 整体质量把关（Lint + Build + Test + 需求对齐） |
| **parallel-agents** | 当需要并行运行多个测试文件时提供模式 |
| **code-review** | 测试通过后进行代码质量审查 |
