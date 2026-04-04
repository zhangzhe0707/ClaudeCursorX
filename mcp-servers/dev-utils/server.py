"""
Dev Utils MCP Server — 开发辅助工具集。

弥补 Claude Code 第 2 级能力差距：
1. release_notes     — 基于 git log 生成变更日志
2. tool_search       — 列出 + 搜索所有已注册 MCP 工具
3. workflow_runner    — 读取 .cursor/workflows/*.md 并解析步骤
4. terminal_capture   — 捕获终端文件内容
5. config_manager     — 读写 Cursor/项目配置
6. cron_manager       — 简易定时任务管理
7. prompt_suggestion  — 任务完成后建议后续步骤
8. lsp_diagnostics    — 增强版 LSP 诊断（包装编译器/类型检查器输出）
9. security_scan      — 安全风险扫描（移植自 Claude Code security-guidance）
10. hookify_evaluate  — 规则引擎评估（适配自 Claude Code hookify 插件）
11. hookify_validate_rules — 规则验证
12. audit_log         — 审计日志记录（借鉴 claude-code-rust AuditLogger）
13. audit_query       — 审计日志查询
14. sandbox_check     — 沙箱命令安全检查（借鉴 claude-code-rust SandboxManager）
15. context_compress  — 上下文压缩（借鉴 claude-code-rust ContextCompressor）
16. feature_flags     — 特性开关管理（借鉴 claude-code-rust FeatureManager）
17. editor_detect     — 编辑器兼容检测（借鉴 claude-code-rust EditorIntegrationManager）
18. plugin_registry   — 插件注册表扫描与验证（借鉴 claude-code-rust PluginManager）
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

# fastmcp 默认将 INFO 日志输出到 stderr，Cursor 会将其误标为 error，统一静默
logging.basicConfig(level=logging.WARNING)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("请先安装依赖: pip install mcp fastmcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("dev-utils")


# ─── release_notes ───────────────────────────────────────

@mcp.tool()
def release_notes(
    project_dir: str = ".",
    from_ref: str = "",
    to_ref: str = "HEAD",
    style: str = "grouped",
) -> str:
    """基于 git log 生成结构化的变更日志/Release Notes。

    自动将 commit 按类型分组（feat/fix/refactor/docs/chore 等），
    生成 Markdown 格式的 Release Notes。

    Args:
        project_dir: 项目根目录
        from_ref: 起始引用（留空则从最近的 tag 开始）
        to_ref: 结束引用（默认 HEAD）
        style: 输出风格 — "grouped"(按类型分组), "flat"(时间线), "conventional"(Conventional Commits)
    """
    proj = Path(project_dir).expanduser().resolve()
    cwd = str(proj)

    # 自动获取起始 ref
    if not from_ref:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            from_ref = result.stdout.strip()
        else:
            result = subprocess.run(
                ["git", "rev-list", "--max-parents=0", "HEAD"],
                cwd=cwd, capture_output=True, text=True, timeout=10,
            )
            from_ref = result.stdout.strip().split("\n")[0][:12] if result.returncode == 0 else "HEAD~20"

    # 获取 git log
    log_format = "%H|%s|%an|%ad"
    log_result = subprocess.run(
        ["git", "log", f"{from_ref}..{to_ref}", f"--pretty=format:{log_format}", "--date=short"],
        cwd=cwd, capture_output=True, text=True, timeout=15,
    )

    if log_result.returncode != 0:
        return json.dumps({"error": f"git log 失败: {log_result.stderr}"})

    commits = []
    for line in log_result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        commits.append({
            "hash": parts[0][:8],
            "message": parts[1],
            "author": parts[2],
            "date": parts[3],
        })

    if not commits:
        return json.dumps({"message": f"在 {from_ref}..{to_ref} 范围内没有 commit"})

    # 分类 commits (Conventional Commits 格式)
    categories: dict[str, list] = defaultdict(list)
    type_labels = {
        "feat": "✨ Features",
        "fix": "🐛 Bug Fixes",
        "refactor": "♻️ Refactoring",
        "docs": "📚 Documentation",
        "test": "🧪 Tests",
        "chore": "🔧 Chores",
        "perf": "⚡ Performance",
        "style": "💅 Style",
        "ci": "🔄 CI/CD",
        "build": "📦 Build",
        "other": "📝 Other",
    }

    for c in commits:
        msg = c["message"]
        cc_match = re.match(r'^(\w+)(?:\(.*?\))?[!:]?\s*(.*)$', msg)
        if cc_match:
            ctype = cc_match.group(1).lower()
            if ctype in type_labels:
                categories[ctype].append(c)
            else:
                categories["other"].append(c)
        else:
            categories["other"].append(c)

    # 生成 Markdown
    md_lines = [f"# Release Notes\n", f"**{from_ref}** → **{to_ref}** ({len(commits)} commits)\n"]

    if style == "flat":
        for c in commits:
            md_lines.append(f"- `{c['hash']}` {c['message']} — {c['author']} ({c['date']})")
    else:
        for ctype, label in type_labels.items():
            if ctype in categories:
                md_lines.append(f"\n## {label}\n")
                for c in categories[ctype]:
                    md_lines.append(f"- {c['message']} (`{c['hash']}` by {c['author']})")

    # 统计
    stat = subprocess.run(
        ["git", "diff", "--stat", f"{from_ref}..{to_ref}"],
        cwd=cwd, capture_output=True, text=True, timeout=10,
    )
    if stat.returncode == 0 and stat.stdout:
        md_lines.append(f"\n## 📊 Stats\n```\n{stat.stdout.strip()[-2000:]}\n```")

    # 贡献者
    authors = list(set(c["author"] for c in commits))
    if authors:
        md_lines.append(f"\n## 👥 Contributors\n")
        for a in sorted(authors):
            count = sum(1 for c in commits if c["author"] == a)
            md_lines.append(f"- {a} ({count} commits)")

    markdown = "\n".join(md_lines)

    return json.dumps({
        "from": from_ref,
        "to": to_ref,
        "total_commits": len(commits),
        "categories": {k: len(v) for k, v in categories.items()},
        "contributors": len(authors),
        "markdown": markdown,
    }, ensure_ascii=False, indent=2)


# ─── tool_search ─────────────────────────────────────────

@mcp.tool()
def tool_search(query: str = "", project_dir: str = ".") -> str:
    """列出所有已注册的 MCP Server 及其工具，支持模糊搜索。

    帮助 Agent 发现可用工具，类似 Claude Code 的 ToolSearchTool。

    Args:
        query: 搜索关键词（在工具名和描述中匹配），留空列出全部
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    mcp_config_path = proj / ".cursor" / "mcp.json"

    servers_info: list[dict] = []

    # 读取 mcp.json 获取所有 server
    if mcp_config_path.exists():
        try:
            config = json.loads(mcp_config_path.read_text())
            mcp_servers = config.get("mcpServers", {})

            for name, cfg in mcp_servers.items():
                server_info = {"name": name, "command": cfg.get("command", ""), "tools": []}

                # 尝试从 server.py 提取工具列表
                args = cfg.get("args", [])
                for arg in args:
                    server_path = proj / arg
                    if server_path.exists() and server_path.suffix == ".py":
                        content = server_path.read_text(encoding="utf-8", errors="replace")
                        # 提取 @mcp.tool() 下面的函数名和 docstring
                        tool_pattern = re.compile(
                            r'@mcp\.tool\(\)[\s\S]*?def\s+(\w+)\s*\([^)]*\)\s*->\s*\w+:\s*\n\s*"""([^"]*(?:""[^"]*)*?)"""',
                            re.MULTILINE,
                        )
                        for m in tool_pattern.finditer(content):
                            tool_name = m.group(1)
                            docstring = m.group(2).strip().split("\n")[0]
                            server_info["tools"].append({
                                "name": tool_name,
                                "description": docstring[:200],
                            })

                servers_info.append(server_info)

        except Exception as e:
            return json.dumps({"error": f"解析 mcp.json 失败: {e}"})
    else:
        return json.dumps({"error": "未找到 .cursor/mcp.json"})

    # 搜索过滤
    all_tools = []
    for s in servers_info:
        for t in s["tools"]:
            all_tools.append({
                "server": s["name"],
                "tool": t["name"],
                "description": t["description"],
            })

    if query:
        query_lower = query.lower()
        all_tools = [t for t in all_tools
                     if query_lower in t["tool"].lower()
                     or query_lower in t["description"].lower()
                     or query_lower in t["server"].lower()]

    return json.dumps({
        "servers": len(servers_info),
        "total_tools": sum(len(s["tools"]) for s in servers_info),
        "matched": len(all_tools),
        "query": query or "(all)",
        "tools": all_tools,
        "servers_detail": [{
            "name": s["name"],
            "tool_count": len(s["tools"]),
        } for s in servers_info],
    }, ensure_ascii=False, indent=2)


# ─── workflow_runner ─────────────────────────────────────

@mcp.tool()
def workflow_runner(
    workflow_name: str = "",
    action: str = "list",
    project_dir: str = ".",
) -> str:
    """工作流管理器：列出、查看或解析 .cursor/workflows/ 下的工作流文件。

    工作流是 Markdown 格式的步骤清单，Agent 按步骤执行。

    Args:
        workflow_name: 工作流文件名（不含 .md 后缀）
        action: 操作 — "list"(列出所有), "view"(查看内容), "parse"(解析为步骤)
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    wf_dir = proj / ".cursor" / "workflows"

    if action == "list":
        if not wf_dir.exists():
            # 创建示例工作流
            wf_dir.mkdir(parents=True, exist_ok=True)
            example = wf_dir / "example-feature.md"
            if not example.exists():
                example.write_text(
                    "# Feature Implementation Workflow\n\n"
                    "## Steps\n\n"
                    "1. [ ] Analyze requirements and identify affected files\n"
                    "2. [ ] Design the API/interface\n"
                    "3. [ ] Implement core logic\n"
                    "4. [ ] Add error handling\n"
                    "5. [ ] Write unit tests\n"
                    "6. [ ] Run tests and fix failures\n"
                    "7. [ ] Lint check\n"
                    "8. [ ] Create commit with descriptive message\n",
                    encoding="utf-8",
                )

        workflows = []
        if wf_dir.exists():
            for f in sorted(wf_dir.glob("*.md")):
                content = f.read_text(encoding="utf-8", errors="replace")
                title = content.split("\n")[0].lstrip("# ").strip() if content else f.stem
                step_count = len(re.findall(r'^\d+\.\s', content, re.MULTILINE))
                workflows.append({
                    "name": f.stem,
                    "title": title,
                    "steps": step_count,
                })

        return json.dumps({
            "directory": str(wf_dir.relative_to(proj)),
            "workflows": workflows,
            "total": len(workflows),
        }, ensure_ascii=False, indent=2)

    if not workflow_name:
        return json.dumps({"error": "请指定 workflow_name"})

    wf_file = wf_dir / f"{workflow_name}.md"
    if not wf_file.exists():
        return json.dumps({"error": f"工作流不存在: {workflow_name}.md"})

    content = wf_file.read_text(encoding="utf-8")

    if action == "view":
        return json.dumps({
            "name": workflow_name,
            "content": content[:10_000],
        }, ensure_ascii=False)

    elif action == "parse":
        steps = []
        for m in re.finditer(r'^\d+\.\s*\[([x ])\]\s*(.+)$', content, re.MULTILINE):
            steps.append({
                "done": m.group(1) == "x",
                "text": m.group(2).strip(),
            })

        if not steps:
            for m in re.finditer(r'^\d+\.\s+(.+)$', content, re.MULTILINE):
                steps.append({"done": False, "text": m.group(1).strip()})

        return json.dumps({
            "name": workflow_name,
            "total_steps": len(steps),
            "completed": sum(1 for s in steps if s["done"]),
            "remaining": sum(1 for s in steps if not s["done"]),
            "steps": steps,
        }, ensure_ascii=False, indent=2)

    return json.dumps({"error": f"未知操作: {action}。支持: list, view, parse"})


# ─── terminal_capture ────────────────────────────────────

@mcp.tool()
def terminal_capture(
    terminal_id: str = "",
    last_n_lines: int = 50,
) -> str:
    """捕获 Cursor 终端输出内容。

    读取 Cursor 终端文件，获取最近的命令输出。

    Args:
        terminal_id: 终端 ID（留空则列出所有终端）
        last_n_lines: 读取最后 N 行
    """
    # Cursor 终端文件路径模式
    terminals_dir = None
    for candidate in [
        Path.home() / ".cursor" / "projects",
    ]:
        if candidate.exists():
            for p in candidate.rglob("terminals"):
                if p.is_dir():
                    terminals_dir = p
                    break

    if not terminals_dir or not terminals_dir.exists():
        return json.dumps({
            "error": "未找到终端文件目录",
            "hint": "Cursor 终端文件通常在 ~/.cursor/projects/*/terminals/",
        })

    if not terminal_id:
        # 列出所有终端
        terminals = []
        for f in sorted(terminals_dir.glob("*.txt")):
            content = f.read_text(encoding="utf-8", errors="replace")
            lines = content.split("\n")
            metadata = {}
            for line in lines[:10]:
                if ":" in line and not line.startswith("---"):
                    key, _, val = line.partition(":")
                    metadata[key.strip()] = val.strip().strip('"')
            terminals.append({
                "id": f.stem,
                "metadata": metadata,
            })
        return json.dumps({
            "directory": str(terminals_dir),
            "terminals": terminals,
        }, ensure_ascii=False, indent=2)

    # 读取特定终端
    term_file = terminals_dir / f"{terminal_id}.txt"
    if not term_file.exists():
        return json.dumps({"error": f"终端文件不存在: {terminal_id}.txt"})

    content = term_file.read_text(encoding="utf-8", errors="replace")
    lines = content.split("\n")

    if last_n_lines and len(lines) > last_n_lines:
        output = "\n".join(lines[-last_n_lines:])
    else:
        output = content

    if len(output) > 15_000:
        output = output[-15_000:]

    return json.dumps({
        "terminal_id": terminal_id,
        "total_lines": len(lines),
        "content": output,
    }, ensure_ascii=False)


# ─── config_manager ──────────────────────────────────────

@mcp.tool()
def config_manager(
    action: str = "read",
    key: str = "",
    value: str = "",
    project_dir: str = ".",
) -> str:
    """项目配置管理器：读写 .cursor/ 下的配置文件。

    Args:
        action: 操作 — "read"(读取), "write"(写入), "list"(列出所有配置文件)
        key: 配置键（JSON path 格式，如 "mcpServers.agent-tools"）
        value: 写入的值（JSON 格式字符串）
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    cursor_dir = proj / ".cursor"

    if action == "list":
        configs = []
        if cursor_dir.exists():
            for f in sorted(cursor_dir.rglob("*")):
                if f.is_file() and f.suffix in {".json", ".mdc", ".md"}:
                    rel = str(f.relative_to(proj))
                    try:
                        size = f.stat().st_size
                    except Exception:
                        size = 0
                    configs.append({"path": rel, "size": size})
        return json.dumps({"configs": configs}, ensure_ascii=False, indent=2)

    elif action == "read":
        if not key:
            # 读取 mcp.json 作为默认
            mcp_path = cursor_dir / "mcp.json"
            if mcp_path.exists():
                return json.dumps({
                    "file": str(mcp_path.relative_to(proj)),
                    "content": json.loads(mcp_path.read_text()),
                }, ensure_ascii=False, indent=2)
            return json.dumps({"error": "请指定 key 或默认读取 mcp.json"})

        # key 可以是文件路径或 JSON path
        target = cursor_dir / key
        if target.exists() and target.is_file():
            content = target.read_text(encoding="utf-8")
            if target.suffix == ".json":
                try:
                    return json.dumps({
                        "file": str(target.relative_to(proj)),
                        "content": json.loads(content),
                    }, ensure_ascii=False, indent=2)
                except json.JSONDecodeError:
                    pass
            return json.dumps({
                "file": str(target.relative_to(proj)),
                "content": content[:5000],
            }, ensure_ascii=False)

        return json.dumps({"error": f"配置文件不存在: {key}"})

    elif action == "write":
        if not key or not value:
            return json.dumps({"error": "write 需要 key 和 value"})

        target = cursor_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            parsed = json.loads(value)
            target.write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except json.JSONDecodeError:
            target.write_text(value, encoding="utf-8")

        return json.dumps({
            "success": True,
            "file": str(target.relative_to(proj)),
        }, ensure_ascii=False, indent=2)

    return json.dumps({"error": f"未知操作: {action}"})


# ─── cron_manager ────────────────────────────────────────

@mcp.tool()
def cron_manager(
    action: str = "list",
    name: str = "",
    schedule: str = "",
    command: str = "",
) -> str:
    """简易定时任务管理（基于 .cursor/crons.json 文件）。

    不直接操作系统 crontab，而是维护一个任务清单文件，
    Agent 可在适当时机检查并执行。

    Args:
        action: 操作 — "list", "create", "delete", "check"(检查到期任务)
        name: 任务名称
        schedule: cron 表达式或简易格式 ("daily", "hourly", "on-commit")
        command: 要执行的命令
    """
    cron_file = Path.home() / ".cursor" / "crons.json"
    cron_file.parent.mkdir(parents=True, exist_ok=True)

    if cron_file.exists():
        try:
            crons = json.loads(cron_file.read_text())
        except Exception:
            crons = []
    else:
        crons = []

    if action == "list":
        return json.dumps({
            "crons": crons,
            "total": len(crons),
        }, ensure_ascii=False, indent=2)

    elif action == "create":
        if not name or not command:
            return json.dumps({"error": "create 需要 name 和 command"})
        new_cron = {
            "name": name,
            "schedule": schedule or "manual",
            "command": command,
            "created": __import__("datetime").datetime.now().isoformat(),
            "last_run": None,
        }
        # 去重
        crons = [c for c in crons if c["name"] != name]
        crons.append(new_cron)
        cron_file.write_text(json.dumps(crons, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.dumps({"success": True, "cron": new_cron}, ensure_ascii=False, indent=2)

    elif action == "delete":
        if not name:
            return json.dumps({"error": "delete 需要 name"})
        before = len(crons)
        crons = [c for c in crons if c["name"] != name]
        cron_file.write_text(json.dumps(crons, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.dumps({
            "success": True,
            "deleted": before - len(crons),
            "remaining": len(crons),
        })

    elif action == "check":
        # 列出应该执行的任务（简化：列出所有 on-commit 和 manual）
        due = [c for c in crons if c["schedule"] in ("on-commit", "manual")]
        return json.dumps({
            "due_tasks": due,
            "total": len(due),
            "hint": "使用 Shell 工具执行这些命令",
        }, ensure_ascii=False, indent=2)

    return json.dumps({"error": f"未知操作: {action}"})


# ─── lsp_diagnostics ─────────────────────────────────────

@mcp.tool()
def lsp_diagnostics(
    file_path: str,
    project_dir: str = ".",
    check_type: str = "auto",
) -> str:
    """增强版诊断工具：运行类型检查器/编译器获取详细错误和类型信息。

    比 ReadLints 更深入——直接运行 tsc/pyright/go vet 等获取完整诊断。

    Args:
        file_path: 要检查的文件路径
        project_dir: 项目根目录
        check_type: 检查类型 — "auto"(自动检测), "typescript", "python", "go", "rust"
    """
    proj = Path(project_dir).expanduser().resolve()
    target = Path(file_path).expanduser().resolve()

    if not target.exists():
        return json.dumps({"error": f"文件不存在: {file_path}"})

    if check_type == "auto":
        ext = target.suffix.lower()
        if ext in {".ts", ".tsx"}:
            check_type = "typescript"
        elif ext in {".js", ".jsx"}:
            check_type = "javascript"
        elif ext == ".py":
            check_type = "python"
        elif ext == ".go":
            check_type = "go"
        elif ext == ".rs":
            check_type = "rust"
        else:
            return json.dumps({"error": f"无法自动检测 {ext} 的检查工具"})

    cmd: list[str] = []
    cwd = str(proj)

    if check_type == "typescript":
        cmd = ["npx", "tsc", "--noEmit", "--pretty", "false"]
        # 只检查指定文件会比较复杂，先全量检查然后过滤
    elif check_type == "javascript":
        # 尝试 eslint
        cmd = ["npx", "eslint", "--format", "compact", str(target)]
    elif check_type == "python":
        # 尝试 pyright > mypy > pylint
        for tool, args in [
            (["pyright", "--outputjson", str(target)], "pyright"),
            (["python", "-m", "mypy", "--no-color-output", str(target)], "mypy"),
            (["python", "-m", "pylint", "--output-format=text", str(target)], "pylint"),
        ]:
            try:
                subprocess.run([tool[0], "--version"], capture_output=True, timeout=5)
                cmd = tool
                break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        if not cmd:
            cmd = ["python", "-m", "py_compile", str(target)]
    elif check_type == "go":
        cmd = ["go", "vet", "./" + str(target.parent.relative_to(proj))]
    elif check_type == "rust":
        cmd = ["cargo", "check", "--message-format=short"]

    if not cmd:
        return json.dumps({"error": f"未找到 {check_type} 的检查工具"})

    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=60,
        )
        output = result.stdout + result.stderr

        # 过滤只与目标文件相关的诊断
        rel_path = str(target.relative_to(proj)) if target.is_relative_to(proj) else str(target)
        relevant_lines = []
        all_lines = output.split("\n")
        for i, line in enumerate(all_lines):
            if rel_path in line or target.name in line:
                relevant_lines.append(line)
                # 包含后续的上下文行
                for j in range(i + 1, min(i + 3, len(all_lines))):
                    if all_lines[j].strip() and not any(p in all_lines[j] for p in [".ts(", ".py:", ".go:"]):
                        relevant_lines.append(all_lines[j])
                    else:
                        break

        # 解析错误
        errors = []
        warnings = []
        for line in relevant_lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue
            if any(k in line_stripped.lower() for k in ["error", "错误"]):
                errors.append(line_stripped[:200])
            elif any(k in line_stripped.lower() for k in ["warning", "warn", "警告"]):
                warnings.append(line_stripped[:200])

        if len(output) > 10_000:
            output = output[:5_000] + "\n... [省略] ...\n" + output[-3_000:]

        return json.dumps({
            "file": rel_path,
            "check_type": check_type,
            "command": " ".join(cmd),
            "exit_code": result.returncode,
            "clean": result.returncode == 0 and not errors,
            "errors": errors[:20],
            "warnings": warnings[:20],
            "relevant_output": "\n".join(relevant_lines[:50]) if relevant_lines else "(无相关诊断)",
            "full_output": output if not relevant_lines else None,
        }, ensure_ascii=False, indent=2)

    except subprocess.TimeoutExpired:
        return json.dumps({"error": "诊断超时 (60s)", "command": " ".join(cmd)})
    except FileNotFoundError:
        return json.dumps({"error": f"工具未安装: {cmd[0]}"})


# ─── prompt_suggestion ───────────────────────────────────

@mcp.tool()
def prompt_suggestion(
    completed_task: str,
    project_dir: str = ".",
    context: str = "",
) -> str:
    """基于已完成的任务，生成后续步骤建议。

    分析任务类型和项目状态，推荐下一步操作。

    Args:
        completed_task: 刚完成的任务描述
        project_dir: 项目根目录
        context: 额外上下文（可选）
    """
    proj = Path(project_dir).expanduser().resolve()
    task_lower = completed_task.lower()

    suggestions = []

    # 基于任务类型的通用建议
    if any(k in task_lower for k in ["implement", "feature", "add", "实现", "添加", "新增"]):
        suggestions.extend([
            "🧪 为新功能编写单元测试",
            "📝 更新相关文档/README",
            "🔍 运行 regression_check 确保没有破坏现有功能",
            "💾 使用 safe_commit 提交变更",
            "👀 委托 code-reviewer 审查代码质量",
        ])

    if any(k in task_lower for k in ["fix", "bug", "debug", "修复", "调试"]):
        suggestions.extend([
            "🧪 编写回归测试防止 bug 复发",
            "🔍 检查类似模式是否在其他地方也存在",
            "📊 运行 coverage_report 确认测试覆盖",
        ])

    if any(k in task_lower for k in ["refactor", "重构", "优化"]):
        suggestions.extend([
            "🧪 运行全量测试确保行为不变",
            "📊 使用 analyze_impact 检查影响范围",
            "🔒 委托 security-reviewer 检查安全影响",
        ])

    if any(k in task_lower for k in ["test", "测试"]):
        suggestions.extend([
            "📊 运行 coverage_report 查看覆盖率",
            "🔍 检查是否遗漏了边界条件测试",
        ])

    if any(k in task_lower for k in ["deploy", "release", "发布", "部署"]):
        suggestions.extend([
            "📋 使用 release_notes 生成变更日志",
            "🔒 运行安全审查",
            "🧪 运行全量回归测试",
        ])

    # 项目状态相关建议
    if (proj / ".git").exists():
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(proj), capture_output=True, text=True, timeout=5,
        )
        if status_result.returncode == 0:
            changed_files = len([l for l in status_result.stdout.strip().split("\n") if l.strip()])
            if changed_files > 0:
                suggestions.append(f"💾 有 {changed_files} 个未提交文件，建议 safe_commit")

    # 通用建议
    if not suggestions:
        suggestions = [
            "🔍 使用 project_overview 了解项目全貌",
            "📊 运行 coverage_report 查看测试覆盖",
            "💾 使用 memory_save 记录关键决策",
        ]

    return json.dumps({
        "completed_task": completed_task,
        "suggestions": suggestions[:8],
        "hint": "选择最相关的建议执行，或向用户确认下一步",
    }, ensure_ascii=False, indent=2)


# ─── security_scan（移植自 Claude Code security-guidance 插件） ──────────────

SECURITY_RULES = [
    {
        "id": "github_actions_injection",
        "path_pattern": ".github/workflows/",
        "path_ext": (".yml", ".yaml"),
        "severity": "high",
        "message_zh": "GitHub Actions 工作流注入风险：不要在 run: 中直接使用不可信输入（issue title、PR body 等），应通过 env: 传递并加引号。",
        "message_en": "GitHub Actions workflow injection risk: Never use untrusted input directly in run: commands. Use env: with proper quoting instead.",
    },
    {
        "id": "command_injection",
        "substrings": ["child_process.exec", "exec(", "execSync(", "os.system", "subprocess.call(", "subprocess.run("],
        "severity": "high",
        "message_zh": "命令注入风险：使用 execFile/subprocess.run([...]) 代替 exec/os.system，避免 shell 注入。",
        "message_en": "Command injection risk: Use execFile/subprocess.run([...]) instead of exec/os.system to prevent shell injection.",
    },
    {
        "id": "eval_injection",
        "substrings": ["eval(", "new Function("],
        "severity": "high",
        "message_zh": "代码注入风险：eval() / new Function() 可执行任意代码。考虑 JSON.parse() 或其他安全替代方案。",
        "message_en": "Code injection risk: eval() / new Function() can execute arbitrary code. Consider JSON.parse() or safer alternatives.",
    },
    {
        "id": "xss_risk",
        "substrings": ["dangerouslySetInnerHTML", ".innerHTML =", ".innerHTML=", "document.write"],
        "severity": "high",
        "message_zh": "XSS 风险：使用 textContent 或 DOMPurify 清理 HTML，避免直接设置 innerHTML / document.write。",
        "message_en": "XSS risk: Use textContent or DOMPurify to sanitize HTML. Avoid setting innerHTML / document.write directly.",
    },
    {
        "id": "unsafe_deserialization",
        "substrings": ["pickle.load", "pickle.loads", "yaml.load(", "marshal.load"],
        "severity": "high",
        "message_zh": "不安全反序列化：pickle/yaml.load/marshal 可导致任意代码执行。使用 json 或 yaml.safe_load。",
        "message_en": "Unsafe deserialization: pickle/yaml.load/marshal can lead to arbitrary code execution. Use json or yaml.safe_load.",
    },
    {
        "id": "hardcoded_secret",
        "substrings": ["password =", "passwd =", "api_key =", "apikey =", "secret =", "token =", "AWS_SECRET", "PRIVATE_KEY"],
        "severity": "medium",
        "message_zh": "疑似硬编码密钥：密码、API Key、Token 等不应出现在代码中，应使用环境变量或密钥管理服务。",
        "message_en": "Possible hardcoded secret: Passwords, API keys, tokens should not be in code. Use environment variables or secret management.",
    },
    {
        "id": "sql_injection",
        "substrings": ["f\"SELECT", "f'SELECT", '+ "SELECT', "+ 'SELECT", ".format(", "% ("],
        "severity": "high",
        "message_zh": "SQL 注入风险：使用参数化查询代替字符串拼接 SQL。",
        "message_en": "SQL injection risk: Use parameterized queries instead of string concatenation for SQL.",
    },
    {
        "id": "weak_crypto",
        "substrings": ["hashlib.md5", "hashlib.sha1", "MD5(", "SHA1("],
        "severity": "medium",
        "message_zh": "弱加密算法：MD5/SHA1 不应用于安全场景，使用 SHA-256 或 bcrypt。",
        "message_en": "Weak cryptography: MD5/SHA1 should not be used for security. Use SHA-256 or bcrypt.",
    },
]


@mcp.tool()
def security_scan(file_path: str, content: str = "", language: str = "zh") -> str:
    """扫描文件内容中的安全风险模式。/ Scan file content for security risk patterns.

    基于 Claude Code security-guidance 插件的检测规则，覆盖命令注入、XSS、
    SQL 注入、不安全反序列化、硬编码密钥、弱加密等常见漏洞模式。

    Args:
        file_path: 要扫描的文件路径
        content: 文件内容（留空则自动读取文件）
        language: 输出语言 "zh"（默认）或 "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")
    findings = []
    target_path = os.path.abspath(file_path)

    if not content:
        try:
            with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except FileNotFoundError:
            return json.dumps({"error": f"文件不存在: {file_path}" if is_zh else f"File not found: {file_path}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    normalized_path = file_path.lstrip("/")

    for rule in SECURITY_RULES:
        matched = False
        # 路径匹配
        if "path_pattern" in rule:
            if rule["path_pattern"] in normalized_path:
                if "path_ext" in rule:
                    matched = any(normalized_path.endswith(ext) for ext in rule["path_ext"])
                else:
                    matched = True
        # 内容子串匹配
        if not matched and "substrings" in rule:
            for substr in rule["substrings"]:
                if substr in content:
                    # 找到匹配行号
                    for i, line in enumerate(content.split("\n"), 1):
                        if substr in line:
                            findings.append({
                                "rule": rule["id"],
                                "severity": rule["severity"],
                                "line": i,
                                "match": substr,
                                "message": rule["message_zh"] if is_zh else rule["message_en"],
                            })
                    matched = True
                    break
        # 路径匹配但无行号
        if matched and "path_pattern" in rule and not any(f["rule"] == rule["id"] for f in findings):
            findings.append({
                "rule": rule["id"],
                "severity": rule["severity"],
                "line": 0,
                "match": normalized_path,
                "message": rule["message_zh"] if is_zh else rule["message_en"],
            })

    high_count = sum(1 for f in findings if f["severity"] == "high")
    medium_count = sum(1 for f in findings if f["severity"] == "medium")
    summary = (f"发现 {len(findings)} 个安全风险（🔴 高危 {high_count} / 🟡 中危 {medium_count}）" if is_zh
               else f"Found {len(findings)} security issues (🔴 high {high_count} / 🟡 medium {medium_count})")

    return json.dumps({
        "file": file_path,
        "total": len(findings),
        "summary": summary,
        "findings": findings,
    }, ensure_ascii=False, indent=2)


# ─── hookify 规则引擎（适配自 Claude Code hookify 插件） ─────────────────────

@mcp.tool()
def hookify_evaluate(
    rules_json: str,
    event: str,
    tool_name: str = "",
    tool_input_json: str = "{}",
    language: str = "zh",
) -> str:
    """Evaluate hookify rules against an event. / 根据 hookify 规则评估事件。

    Adapted from Claude Code hookify plugin's rule engine. Supports rule-based
    blocking/warning on tool use, user prompt, stop events, etc.

    Args:
        rules_json: JSON array of rules. Each rule: {"name": str, "enabled": bool,
                    "event": str (PreToolUse|PostToolUse|Stop|UserPromptSubmit),
                    "tool_matcher": str ("Bash"|"Edit|Write"|"*"),
                    "action": "block"|"warn",
                    "conditions": [{"field": str, "operator": str, "pattern": str}],
                    "message": str}
        event: Hook event name (PreToolUse, PostToolUse, Stop, UserPromptSubmit)
        tool_name: Name of the tool being used (empty for Stop/UserPromptSubmit)
        tool_input_json: JSON string of tool input parameters
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    try:
        rules = json.loads(rules_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid rules JSON: {e}"}, ensure_ascii=False)

    try:
        tool_input = json.loads(tool_input_json) if tool_input_json else {}
    except json.JSONDecodeError:
        tool_input = {}

    blocking = []
    warnings = []

    for rule in rules:
        if not rule.get("enabled", True):
            continue

        if rule.get("event") and rule["event"] != event:
            continue

        matcher = rule.get("tool_matcher", "*")
        if matcher != "*" and tool_name not in matcher.split("|"):
            continue

        conditions = rule.get("conditions", [])
        if not conditions:
            continue

        all_match = True
        for cond in conditions:
            field = cond.get("field", "")
            operator = cond.get("operator", "contains")
            pattern = cond.get("pattern", "")

            value = tool_input.get(field, "")
            if isinstance(value, dict):
                value = json.dumps(value, ensure_ascii=False)
            elif not isinstance(value, str):
                value = str(value)

            if operator == "regex_match":
                try:
                    if not re.search(pattern, value, re.IGNORECASE):
                        all_match = False
                        break
                except re.error:
                    all_match = False
                    break
            elif operator == "contains":
                if pattern not in value:
                    all_match = False
                    break
            elif operator == "equals":
                if pattern != value:
                    all_match = False
                    break
            elif operator == "not_contains":
                if pattern in value:
                    all_match = False
                    break
            elif operator == "starts_with":
                if not value.startswith(pattern):
                    all_match = False
                    break
            elif operator == "ends_with":
                if not value.endswith(pattern):
                    all_match = False
                    break
            else:
                all_match = False
                break

        if all_match:
            entry = {"rule": rule.get("name", "unnamed"), "message": rule.get("message", "")}
            if rule.get("action") == "block":
                blocking.append(entry)
            else:
                warnings.append(entry)

    if blocking:
        label = "操作被阻止" if is_zh else "Operation blocked"
        return json.dumps({
            "decision": "block",
            "label": label,
            "matched_rules": blocking,
            "warning_rules": warnings,
        }, ensure_ascii=False, indent=2)

    if warnings:
        label = "操作允许但有警告" if is_zh else "Operation allowed with warnings"
        return json.dumps({
            "decision": "allow",
            "label": label,
            "warning_rules": warnings,
        }, ensure_ascii=False, indent=2)

    label = "无匹配规则，操作允许" if is_zh else "No rules matched, operation allowed"
    return json.dumps({"decision": "allow", "label": label}, ensure_ascii=False, indent=2)


@mcp.tool()
def hookify_validate_rules(rules_json: str, language: str = "zh") -> str:
    """Validate hookify rules for correctness. / 验证 hookify 规则的正确性。

    Args:
        rules_json: JSON array of rules to validate
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    try:
        rules = json.loads(rules_json)
    except json.JSONDecodeError as e:
        return json.dumps({"valid": False, "error": str(e)}, ensure_ascii=False)

    if not isinstance(rules, list):
        msg = "rules_json 必须是 JSON 数组" if is_zh else "rules_json must be a JSON array"
        return json.dumps({"valid": False, "error": msg}, ensure_ascii=False)

    issues: list[dict] = []
    valid_events = {"PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit", "SessionStart", "SessionEnd"}
    valid_operators = {"regex_match", "contains", "equals", "not_contains", "starts_with", "ends_with"}

    for i, rule in enumerate(rules):
        prefix = f"rules[{i}]"
        if not isinstance(rule, dict):
            issues.append({"rule": i, "issue": "must be an object" if not is_zh else "必须是对象"})
            continue
        if not rule.get("name"):
            issues.append({"rule": i, "issue": "missing name" if not is_zh else "缺少 name"})
        evt = rule.get("event", "")
        if evt and evt not in valid_events:
            issues.append({"rule": i, "issue": f"unknown event '{evt}', valid: {valid_events}"})
        for j, cond in enumerate(rule.get("conditions", [])):
            op = cond.get("operator", "")
            if op and op not in valid_operators:
                issues.append({"rule": i, "condition": j, "issue": f"unknown operator '{op}'"})
            if cond.get("operator") == "regex_match":
                try:
                    re.compile(cond.get("pattern", ""))
                except re.error as e:
                    issues.append({"rule": i, "condition": j, "issue": f"invalid regex: {e}"})

    if issues:
        return json.dumps({"valid": False, "issues": issues}, ensure_ascii=False, indent=2)
    msg = f"全部 {len(rules)} 条规则验证通过" if is_zh else f"All {len(rules)} rules are valid"
    return json.dumps({"valid": True, "message": msg, "rule_count": len(rules)}, ensure_ascii=False, indent=2)


# ─── 审计日志系统（借鉴 claude-code-rust security/audit） ─────────────────────

import time as _time
from collections import deque as _deque

_AUDIT_LOG: _deque = _deque(maxlen=1000)

AUDIT_EVENT_TYPES = [
    "tool_call", "permission_decision", "file_operation",
    "network_request", "dangerous_command", "sandbox_execution",
    "authentication", "system_event",
]


@mcp.tool()
def audit_log(
    event_type: str,
    details: str = "{}",
    level: str = "info",
    user_id: str = "default",
    language: str = "zh",
) -> str:
    """Log an audit event. / 记录审计事件。

    Adapted from claude-code-rust's AuditLogger with 8 event types.

    Args:
        event_type: One of tool_call/permission_decision/file_operation/network_request/
                    dangerous_command/sandbox_execution/authentication/system_event
        details: JSON string with event-specific details
        level: debug/info/warning/error/critical
        user_id: User identifier
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    if event_type not in AUDIT_EVENT_TYPES:
        event_type = "system_event"

    try:
        detail_obj = json.loads(details) if details else {}
    except json.JSONDecodeError:
        detail_obj = {"raw": details}

    import hashlib
    event = {
        "id": hashlib.md5(f"{event_type}{_time.time()}".encode()).hexdigest()[:12],
        "timestamp": _time.time(),
        "level": level,
        "event_type": event_type,
        "user_id": user_id,
        "details": detail_obj,
    }
    _AUDIT_LOG.append(event)

    msg = f"审计事件已记录 ({event_type})" if is_zh else f"Audit event logged ({event_type})"
    return json.dumps({"status": "ok", "message": msg, "event_id": event["id"],
                        "total_events": len(_AUDIT_LOG)}, ensure_ascii=False)


@mcp.tool()
def audit_query(
    event_type: str = "",
    level: str = "",
    limit: int = 50,
    language: str = "zh",
) -> str:
    """Query audit log events. / 查询审计日志。

    Args:
        event_type: Filter by event type (empty = all)
        level: Filter by level (empty = all)
        limit: Max events to return
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    results = list(_AUDIT_LOG)
    if event_type:
        results = [e for e in results if e["event_type"] == event_type]
    if level:
        results = [e for e in results if e["level"] == level]

    results = results[-limit:]
    title = f"审计日志（{len(results)} 条）" if is_zh else f"Audit log ({len(results)} events)"
    return json.dumps({"title": title, "count": len(results), "events": results},
                       ensure_ascii=False, indent=2)


# ─── 沙箱命令检查（借鉴 claude-code-rust security/sandbox） ──────────────────

DANGEROUS_COMMANDS = {
    "critical": ["rm -rf /", "mkfs", "dd if=", "> /dev/sd", ":(){ :|:& };:"],
    "high": ["chmod -R 777", "chown -R", "kill -9", "pkill", "shutdown", "reboot",
             "systemctl stop", "iptables -F"],
    "medium": ["curl | bash", "wget | sh", "eval", "exec", "sudo"],
}

SAFE_COMMANDS = [
    "ls", "cat", "head", "tail", "grep", "find", "echo", "pwd", "cd", "mkdir",
    "cp", "mv", "touch", "date", "whoami", "uname", "env", "which", "wc",
    "sort", "uniq", "diff", "git status", "git log", "git diff", "git branch",
    "npm list", "pip list", "cargo check", "python --version", "node --version",
]


@mcp.tool()
def sandbox_check(
    command: str,
    language: str = "zh",
) -> str:
    """Check if a command is safe to execute. / 检查命令是否安全可执行。

    Adapted from claude-code-rust's SandboxManager + CommandChecker.

    Args:
        command: Shell command to check
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    cmd_lower = command.lower().strip()
    first_token = cmd_lower.split()[0] if cmd_lower else ""

    for safe in SAFE_COMMANDS:
        if cmd_lower.startswith(safe):
            label = "安全" if is_zh else "Safe"
            return json.dumps({"danger_level": "safe", "label": label,
                                "requires_sandbox": False, "command": command},
                               ensure_ascii=False)

    for level in ["critical", "high", "medium"]:
        for pattern in DANGEROUS_COMMANDS.get(level, []):
            if pattern.lower() in cmd_lower:
                label = {"critical": "严重危险" if is_zh else "Critical",
                         "high": "高风险" if is_zh else "High risk",
                         "medium": "中风险" if is_zh else "Medium risk"}[level]
                return json.dumps({
                    "danger_level": level,
                    "label": label,
                    "requires_sandbox": True,
                    "matched_pattern": pattern,
                    "command": command,
                }, ensure_ascii=False, indent=2)

    label = "低风险" if is_zh else "Low risk"
    return json.dumps({"danger_level": "low", "label": label,
                        "requires_sandbox": False, "command": command},
                       ensure_ascii=False)


# ─── 上下文压缩器（借鉴 claude-code-rust query/compressor） ─────────────────

_ROLE_PRIORITY = {
    "system": 3,
    "user": 2,
    "assistant": 1,
    "tool": 0,
}


def _msg_importance(msg: dict) -> float:
    """计算消息重要度（综合 role、长度、位置信号）。"""
    role = msg.get("role", "")
    content = msg.get("content", "")

    base = _ROLE_PRIORITY.get(role, 0) / 3.0

    has_code = 1.0 if ("```" in content or "def " in content or "function " in content) else 0.0
    has_error = 1.0 if ("error" in content.lower() or "exception" in content.lower()) else 0.0

    length_signal = min(len(content) / 2000, 1.0)

    return base * 0.4 + has_code * 0.2 + has_error * 0.2 + length_signal * 0.2


def _extract_key_decisions(messages: list) -> list[str]:
    """从消息历史中提取关键决策和结论，用于摘要压缩。

    Extracts key decisions and conclusions from message history for summary compaction.
    """
    decisions = []
    for m in messages:
        content = m.get("content", "")
        role = m.get("role", "")
        if role != "assistant":
            continue
        # 提取包含决策信号的句子
        for line in content.split("\n"):
            line = line.strip()
            # 关键决策信号词（中英文）
            signals = ["决定", "选择", "方案", "结论", "总结", "完成", "修复", "实现",
                       "decided", "chose", "conclusion", "fixed", "implemented", "completed",
                       "approach", "solution"]
            if any(s in line.lower() for s in signals) and 20 < len(line) < 200:
                decisions.append(line)
    # 去重并限制数量
    seen = set()
    unique = []
    for d in decisions:
        key = d[:50]
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique[:10]


@mcp.tool()
def context_compress(
    messages_json: str,
    max_tokens: int = 100000,
    keep_recent: int = 5,
    strategy: str = "smart",
    language: str = "zh",
) -> str:
    """Compress conversation context with importance-aware strategy. / 智能压缩对话上下文。

    Three strategies adapted from OpenHarness services/compact + claude-code-rust ContextCompressor:
    - "smart":     Importance-weighted selection (keep high-signal messages)
    - "summarize": Extract key decisions → inject as summary block (OpenHarness compact style)
    - "simple":    Tail-truncation only (keep first + last N)

    Args:
        messages_json: JSON array of messages [{"role": "...", "content": "..."}]
        max_tokens: Maximum token budget
        keep_recent: Number of recent messages to always keep
        strategy: "smart" | "summarize" | "simple"
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    try:
        messages = json.loads(messages_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    if not isinstance(messages, list):
        return json.dumps({"error": "messages must be a JSON array"}, ensure_ascii=False)

    def estimate_tokens(text: str) -> int:
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        non_ascii = len(text) - ascii_chars
        return ascii_chars // 4 + non_ascii // 2

    total = sum(estimate_tokens(m.get("content", "")) for m in messages)
    if total <= max_tokens:
        msg = "无需压缩" if is_zh else "No compression needed"
        return json.dumps({"status": "ok", "message": msg,
                            "original_tokens": total, "compressed_tokens": total,
                            "messages": messages}, ensure_ascii=False, indent=2)

    if len(messages) <= keep_recent + 1:
        msg = "消息太少无法压缩" if is_zh else "Too few messages to compress"
        return json.dumps({"status": "ok", "message": msg, "messages": messages},
                           ensure_ascii=False, indent=2)

    # 第一条（系统提示）和最后 keep_recent 条始终保留
    first = messages[0]
    recent = messages[-keep_recent:]
    middle = messages[1:-keep_recent]

    if strategy == "summarize" and middle:
        # OpenHarness compact 风格：提取关键决策 → 注入摘要块替换中间消息
        decisions = _extract_key_decisions(middle)
        if is_zh:
            summary_lines = ["[对话历史摘要 / Conversation Summary]", ""]
            summary_lines += ["以下为已压缩的历史对话关键信息：", ""]
            for i, d in enumerate(decisions, 1):
                summary_lines.append(f"{i}. {d}")
            if not decisions:
                summary_lines.append("（历史对话已压缩，无关键决策记录）")
            dropped_count = len(middle)
            summary_lines += ["", f"[已压缩 {dropped_count} 条历史消息，以上为关键摘要]"]
        else:
            summary_lines = ["[Conversation History Summary]", ""]
            summary_lines += ["Key decisions and conclusions from compressed history:", ""]
            for i, d in enumerate(decisions, 1):
                summary_lines.append(f"{i}. {d}")
            if not decisions:
                summary_lines.append("(History compressed, no key decisions extracted)")
            dropped_count = len(middle)
            summary_lines += ["", f"[{dropped_count} messages compressed above]"]

        summary_msg = {
            "role": "system",
            "content": "\n".join(summary_lines),
        }
        kept = [first, summary_msg] + recent
        new_total = sum(estimate_tokens(m.get("content", "")) for m in kept)
        dropped = len(messages) - len(kept)
        msg = (f"摘要压缩完成：{total} → {new_total} tokens（替换 {dropped_count} 条为摘要块）" if is_zh
               else f"Summary compact: {total} → {new_total} tokens ({dropped_count} messages → summary block)")

    elif strategy == "smart" and middle:
        scored = [(i, _msg_importance(m), m) for i, m in enumerate(middle)]
        scored.sort(key=lambda x: x[1], reverse=True)

        kept_middle = []
        budget = max_tokens - estimate_tokens(first.get("content", "")) - \
                 sum(estimate_tokens(m.get("content", "")) for m in recent)

        for _, score, m in scored:
            tokens = estimate_tokens(m.get("content", ""))
            if budget >= tokens:
                kept_middle.append(m)
                budget -= tokens
            elif budget > 200:
                ratio = budget / tokens
                m_copy = dict(m)
                m_copy["content"] = m["content"][:int(len(m["content"]) * ratio)] + "\n... [truncated]"
                kept_middle.append(m_copy)
                budget = 0
            if budget <= 0:
                break

        kept = [first] + kept_middle + recent
        new_total = sum(estimate_tokens(m.get("content", "")) for m in kept)
        dropped = len(messages) - len(kept)
        msg = (f"智能压缩完成：{total} → {new_total} tokens（丢弃 {dropped} 条低重要度消息）" if is_zh
               else f"Smart compress: {total} → {new_total} tokens (dropped {dropped} low-importance messages)")

    else:
        # simple: 只保留首条 + 最后 keep_recent 条
        kept = [first] + recent
        new_total = sum(estimate_tokens(m.get("content", "")) for m in kept)
        dropped = len(messages) - len(kept)
        msg = (f"简单压缩完成：{total} → {new_total} tokens（丢弃 {dropped} 条中间消息）" if is_zh
               else f"Simple compress: {total} → {new_total} tokens (dropped {dropped} middle messages)")

    # 兜底：如果仍超预算，截断中间消息
    if new_total > max_tokens:
        for m in kept[1:-keep_recent] if len(kept) > keep_recent + 1 else kept[1:]:
            content = m.get("content", "")
            tokens = estimate_tokens(content)
            if tokens > max_tokens // (keep_recent + 1):
                ratio = (max_tokens // (keep_recent + 1)) / tokens
                m["content"] = content[:int(len(content) * ratio)] + "\n... [truncated]"
        new_total = sum(estimate_tokens(m.get("content", "")) for m in kept)

    return json.dumps({
        "status": "ok",
        "message": msg,
        "strategy": strategy,
        "original_tokens": total,
        "compressed_tokens": new_total,
        "original_count": len(messages),
        "compressed_count": len(kept),
        "dropped_count": len(messages) - len(kept),
        "messages": kept,
    }, ensure_ascii=False, indent=2)


# ─── 特性开关管理（借鉴 claude-code-rust features/FeatureManager） ───────────

DEFAULT_FEATURES = {
    "proactive": {"enabled": False, "experimental": True, "description": "主动建议功能"},
    "voice": {"enabled": False, "experimental": True, "description": "语音输入功能"},
    "coordinator": {"enabled": True, "experimental": False, "description": "多 Agent 协调器"},
    "security_scan": {"enabled": True, "experimental": False, "description": "安全扫描"},
    "hookify": {"enabled": True, "experimental": False, "description": "规则引擎"},
    "memory": {"enabled": True, "experimental": False, "description": "记忆系统"},
    "metrics": {"enabled": True, "experimental": False, "description": "性能指标收集"},
    "audit": {"enabled": True, "experimental": False, "description": "审计日志"},
    "i18n": {"enabled": True, "experimental": False, "description": "国际化支持"},
    "sandbox": {"enabled": True, "experimental": False, "description": "沙箱命令检查"},
}


@mcp.tool()
def feature_flags(
    action: str = "list",
    feature: str = "",
    language: str = "zh",
) -> str:
    """Manage feature flags. / 管理特性开关。

    Adapted from claude-code-rust's FeatureManager.

    Args:
        action: "list" / "enable" / "disable" / "toggle" / "reset"
        feature: Feature name (required for enable/disable/toggle)
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    if action == "list":
        return json.dumps({"features": DEFAULT_FEATURES}, ensure_ascii=False, indent=2)

    if not feature or feature not in DEFAULT_FEATURES:
        names = list(DEFAULT_FEATURES.keys())
        msg = f"未知特性 '{feature}'，可选: {names}" if is_zh else f"Unknown feature '{feature}', available: {names}"
        return json.dumps({"error": msg}, ensure_ascii=False)

    f = DEFAULT_FEATURES[feature]
    if action == "enable":
        f["enabled"] = True
    elif action == "disable":
        f["enabled"] = False
    elif action == "toggle":
        f["enabled"] = not f["enabled"]
    elif action == "reset":
        pass  # 保持当前值
    else:
        msg = "未知操作" if is_zh else "Unknown action"
        return json.dumps({"error": msg}, ensure_ascii=False)

    state = "启用" if f["enabled"] else "禁用"
    state_en = "enabled" if f["enabled"] else "disabled"
    msg = f"{feature} 已{state}" if is_zh else f"{feature} {state_en}"
    return json.dumps({"status": "ok", "message": msg,
                        "feature": feature, "enabled": f["enabled"]}, ensure_ascii=False)


# ─── 编辑器兼容检测（借鉴 claude-code-rust editor_compat） ───────────────────

@mcp.tool()
def editor_detect(language: str = "zh") -> str:
    """Detect current editor environment and capabilities. / 检测当前编辑器环境和能力。

    Adapted from claude-code-rust's EditorIntegrationManager.

    Args:
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    env = os.environ
    editor_info: dict = {"type": "unknown", "features": []}

    # Cursor / VSCode 检测
    if env.get("CURSOR_CHANNEL") or "cursor" in env.get("TERM_PROGRAM", "").lower():
        editor_info["type"] = "cursor"
        editor_info["features"] = ["mcp", "inline_chat", "composer", "agent_mode",
                                    "rules", "skills", "subagents"]
    elif env.get("VSCODE_PID") or env.get("TERM_PROGRAM") == "vscode":
        editor_info["type"] = "vscode"
        editor_info["features"] = ["extensions", "tasks", "terminal", "debug"]
    elif env.get("JETBRAINS_IDE"):
        editor_info["type"] = "jetbrains"
        editor_info["features"] = ["inspections", "refactoring", "vcs"]
    elif env.get("VIM") or env.get("NVIM"):
        editor_info["type"] = "vim/neovim"
        editor_info["features"] = ["lsp", "treesitter"]
    elif env.get("INSIDE_EMACS"):
        editor_info["type"] = "emacs"
        editor_info["features"] = ["lsp", "treemacs"]

    editor_info["shell"] = env.get("SHELL", "unknown")
    editor_info["term"] = env.get("TERM", "unknown")
    editor_info["cwd"] = os.getcwd()

    title = "编辑器检测结果" if is_zh else "Editor Detection Result"
    return json.dumps({"title": title, **editor_info}, ensure_ascii=False, indent=2)


# ─── 插件注册表（借鉴 claude-code-rust plugins/registry） ────────────────────

from pathlib import Path as _Path


def _parse_plugin_manifest(manifest_path: "_Path") -> dict:
    """解析 plugin.json 清单（兼容 .claude-plugin 和 OpenHarness 格式）。

    Parses plugin.json manifest compatible with .claude-plugin and OpenHarness formats.
    """
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return {}

    plugin_dir = manifest_path.parent
    hooks = data.get("hooks", [])
    mcp_file = data.get("mcp_file", "")
    skills_dir = data.get("skills_dir", "skills")
    commands_dir = data.get("commands_dir", "commands")

    return {
        "name": data.get("name", plugin_dir.name),
        "version": data.get("version", ""),
        "description": data.get("description", "")[:120],
        "enabled_by_default": data.get("enabled_by_default", True),
        "hooks_count": len(hooks),
        "hook_events": list({h.get("event", "") for h in hooks if h.get("event")}),
        "has_mcp": bool(mcp_file or (plugin_dir / ".mcp.json").exists()),
        "has_skills": (plugin_dir / skills_dir).is_dir(),
        "has_commands": (plugin_dir / commands_dir).is_dir(),
        "manifest_path": str(manifest_path),
    }


@mcp.tool()
def plugin_registry(
    action: str = "list",
    scan_dir: str = "",
    language: str = "zh",
) -> str:
    """Scan and list plugins, MCP servers, skills, rules, and agents. / 扫描并列出所有插件资产。

    Enhanced with OpenHarness-compatible plugin discovery:
    - Scans .claude-plugin/plugin.json (Claude Code plugin format)
    - Scans .openharness/plugins/ (OpenHarness plugin format)
    - Scans project mcp-servers/, skills/, rules/, agents/

    Args:
        action: "list" (scan and list all) or "validate" (check for issues)
        scan_dir: Project root to scan (defaults to cwd)
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")
    root = _Path(scan_dir).resolve() if scan_dir else _Path.cwd()

    registry: dict = {
        "plugins": [],       # .claude-plugin / OpenHarness 格式插件
        "mcp_servers": [],
        "skills": [],
        "rules": [],
        "agents": [],
    }
    issues: list[str] = []

    # ── 扫描 .claude-plugin 格式插件（OpenHarness 兼容）──────────────────────
    plugin_search_dirs = [
        root / ".claude-plugin",               # 项目根级插件
        root / ".openharness" / "plugins",     # OpenHarness 风格
        root / "plugins",                      # 通用位置
    ]
    # 用户目录插件（非项目级）
    user_plugin_dir = _Path.home() / ".openharness" / "plugins"
    if user_plugin_dir.is_dir():
        plugin_search_dirs.append(user_plugin_dir)

    for pdir in plugin_search_dirs:
        if not pdir.is_dir():
            continue
        # 两种清单位置：pdir/plugin.json 或 pdir/<name>/plugin.json
        for manifest_candidate in [
            pdir / "plugin.json",
            *(sub / "plugin.json" for sub in pdir.iterdir() if sub.is_dir()),
            *(sub / ".claude-plugin" / "plugin.json" for sub in pdir.iterdir() if sub.is_dir()),
        ]:
            if manifest_candidate.exists():
                info = _parse_plugin_manifest(manifest_candidate)
                if info:
                    info["source_dir"] = str(manifest_candidate.parent.relative_to(root)
                                             if manifest_candidate.parent.is_relative_to(root)
                                             else manifest_candidate.parent)
                    registry["plugins"].append(info)

    # ── 扫描 MCP Servers ──────────────────────────────────────────────────────
    for server_dir in [root / "mcp-servers", root / ".cursor" / "mcp-servers"]:
        if server_dir.is_dir():
            for child in sorted(server_dir.iterdir()):
                server_py = child / "server.py" if child.is_dir() else None
                if server_py and server_py.exists():
                    content = server_py.read_text(encoding="utf-8", errors="replace")
                    tool_count = content.count("@mcp.tool()")
                    prompt_count = content.count("@mcp.prompt()")
                    registry["mcp_servers"].append({
                        "name": child.name,
                        "path": str(child.relative_to(root)),
                        "tools": tool_count,
                        "prompts": prompt_count,
                    })

    # ── 扫描 Skills ───────────────────────────────────────────────────────────
    for skills_dir in [root / "skills", root / ".cursor" / "skills"]:
        if skills_dir.is_dir():
            for child in sorted(skills_dir.iterdir()):
                skill_file = child / "SKILL.md" if child.is_dir() else None
                if skill_file and skill_file.exists():
                    content = skill_file.read_text(encoding="utf-8", errors="replace")
                    # 从 YAML frontmatter 提取 description
                    desc = ""
                    title = child.name
                    in_front = False
                    for line in content.split("\n"):
                        if line.strip() == "---":
                            in_front = not in_front
                            continue
                        if in_front and line.startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip(">-").strip()
                        if line.startswith("# "):
                            title = line.lstrip("# ").strip()
                            break
                    registry["skills"].append({
                        "name": child.name,
                        "title": title,
                        "description": desc[:120],
                        "path": str(child.relative_to(root)),
                    })

    # ── 扫描 Rules ────────────────────────────────────────────────────────────
    for rules_dir in [root / "rules", root / ".cursor" / "rules"]:
        if rules_dir.is_dir():
            for f in sorted(rules_dir.glob("*.mdc")):
                content = f.read_text(encoding="utf-8", errors="replace")
                desc = ""
                for line in content.split("\n"):
                    if line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip().strip('"')
                        break
                always = "alwaysApply: true" in content
                registry["rules"].append({
                    "name": f.stem,
                    "description": desc[:100],
                    "always_apply": always,
                    "path": str(f.relative_to(root)),
                })

    # ── 扫描 Agents ───────────────────────────────────────────────────────────
    for agents_dir in [root / "agents", root / ".cursor" / "agents"]:
        if agents_dir.is_dir():
            for f in sorted(agents_dir.glob("*.md")):
                content = f.read_text(encoding="utf-8", errors="replace")
                name = f.stem
                desc, backend, tools_list = "", "", []
                in_front = False
                for line in content.split("\n"):
                    if line.strip() == "---":
                        in_front = not in_front
                        continue
                    if not in_front:
                        break
                    if line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip().strip(">-").strip()
                    elif line.startswith("backend_type:"):
                        backend = line.split(":", 1)[1].strip()
                    elif line.strip().startswith("- ") and "tools:" in "".join(
                        content.split("\n")[:content.split("\n").index(line)][-3:]
                    ):
                        tools_list.append(line.strip().lstrip("- "))
                registry["agents"].append({
                    "name": name,
                    "description": desc[:120],
                    "backend_type": backend or "subprocess",
                    "path": str(f.relative_to(root)),
                })

    # ── 验证模式 ──────────────────────────────────────────────────────────────
    if action == "validate":
        mcp_json = root / ".cursor" / "mcp.json"
        if mcp_json.exists():
            try:
                cfg = json.loads(mcp_json.read_text(encoding="utf-8"))
                registered = set(cfg.get("mcpServers", {}).keys())
                found = {s["name"] for s in registry["mcp_servers"]}
                for name in registered - found:
                    issues.append(f"mcp.json 注册了 '{name}' 但未找到 server.py" if is_zh
                                  else f"mcp.json registers '{name}' but server.py not found")
            except json.JSONDecodeError:
                issues.append("mcp.json 格式错误" if is_zh else "mcp.json is malformed")

        # 检查插件的 Hook 事件是否有对应的 Rule 支持
        for plugin in registry["plugins"]:
            for event in plugin.get("hook_events", []):
                if event in ("PRE_TOOL_USE", "POST_TOOL_USE"):
                    has_hook_rule = any("safety-hook" in r["name"] or "hook" in r["name"]
                                        for r in registry["rules"])
                    if not has_hook_rule:
                        issues.append(
                            f"插件 '{plugin['name']}' 使用了 {event} Hook，但未找到对应的 Hook Rule" if is_zh
                            else f"Plugin '{plugin['name']}' uses {event} hook but no hook rule found"
                        )

    summary = {
        "plugins": len(registry["plugins"]),
        "mcp_servers": len(registry["mcp_servers"]),
        "skills": len(registry["skills"]),
        "rules": len(registry["rules"]),
        "agents": len(registry["agents"]),
        "total_tools": sum(s["tools"] for s in registry["mcp_servers"]),
        "total_prompts": sum(s["prompts"] for s in registry["mcp_servers"]),
    }

    title = "插件注册表（OpenHarness 兼容）" if is_zh else "Plugin Registry (OpenHarness Compatible)"
    result: dict = {"title": title, "summary": summary, "registry": registry}
    if issues:
        result["issues"] = issues
    return json.dumps(result, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
