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


if __name__ == "__main__":
    mcp.run(transport="stdio")
