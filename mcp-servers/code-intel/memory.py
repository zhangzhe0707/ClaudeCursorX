"""
Memory Module — 记忆提取与持久化。

为 code-intel MCP Server 添加跨会话记忆能力，对应 Claude Code 的
extractMemories/ + .claude/CLAUDE.md 机制。

追加到 code-intel server 中使用。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path


def memory_save(
    key: str,
    content: str,
    category: str = "decision",
    project_dir: str = ".",
) -> str:
    """保存一条记忆/决策到持久化文件。

    记忆会追加到 .cursor/memory.md，可跨会话持久存在。
    Agent 在新会话开始时应读取此文件恢复上下文。

    Args:
        key: 记忆标题/关键词（简短）
        content: 记忆内容（详细描述）
        category: 分类 — decision(决策), pattern(模式), convention(约定), bug(已知问题), context(上下文)
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    memory_file = proj / ".cursor" / "memory.md"

    valid_categories = {"decision", "pattern", "convention", "bug", "context"}
    if category not in valid_categories:
        return json.dumps({
            "error": f"无效分类: {category}",
            "valid": list(valid_categories),
        })

    # 确保目录存在
    memory_file.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    emoji = {
        "decision": "🔧", "pattern": "📐", "convention": "📏",
        "bug": "🐛", "context": "📋",
    }.get(category, "📝")

    entry = f"\n### {emoji} [{category}] {key}\n"
    entry += f"*{timestamp}*\n\n"
    entry += f"{content}\n\n---\n"

    # 如果文件不存在，写入头部
    if not memory_file.exists():
        header = "# Project Memory\n\n"
        header += "> 跨会话持久化记忆。每次新会话开始时读取此文件恢复上下文。\n"
        header += "> 由 memory_save 工具自动维护。\n\n---\n"
        memory_file.write_text(header + entry, encoding="utf-8")
    else:
        with memory_file.open("a", encoding="utf-8") as f:
            f.write(entry)

    # 统计
    total_entries = memory_file.read_text(encoding="utf-8").count("### ")

    return json.dumps({
        "success": True,
        "file": str(memory_file.relative_to(proj)),
        "key": key,
        "category": category,
        "total_entries": total_entries,
    }, ensure_ascii=False, indent=2)


def memory_search(
    query: str = "",
    category: str = "",
    project_dir: str = ".",
) -> str:
    """搜索项目记忆库。

    Args:
        query: 搜索关键词（在标题和内容中匹配）
        category: 按分类过滤
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    memory_file = proj / ".cursor" / "memory.md"

    if not memory_file.exists():
        return json.dumps({
            "entries": [],
            "message": "记忆文件不存在。使用 memory_save 创建第一条记忆。",
        })

    content = memory_file.read_text(encoding="utf-8")

    # 解析条目
    entries = []
    blocks = content.split("### ")[1:]  # 跳过头部
    for block in blocks:
        lines = block.strip().split("\n")
        if not lines:
            continue
        title_line = lines[0].strip()
        # 提取分类和标题
        cat_match = re.match(r'[^\[]*\[(\w+)\]\s*(.*)', title_line)
        if cat_match:
            entry_cat = cat_match.group(1)
            entry_key = cat_match.group(2)
        else:
            entry_cat = "unknown"
            entry_key = title_line

        # 提取时间戳
        timestamp = ""
        body_lines = []
        for line in lines[1:]:
            if line.startswith("*") and line.endswith("*"):
                timestamp = line.strip("* ")
            elif line.strip() != "---":
                body_lines.append(line)

        body = "\n".join(body_lines).strip()

        entries.append({
            "category": entry_cat,
            "key": entry_key,
            "timestamp": timestamp,
            "content": body[:300],
        })

    # 过滤
    if category:
        entries = [e for e in entries if e["category"] == category]
    if query:
        query_lower = query.lower()
        entries = [e for e in entries
                   if query_lower in e["key"].lower()
                   or query_lower in e["content"].lower()]

    return json.dumps({
        "total": len(entries),
        "entries": entries[:30],
    }, ensure_ascii=False, indent=2)


def memory_read(project_dir: str = ".") -> str:
    """读取完整的项目记忆文件，用于新会话开始时恢复上下文。

    Args:
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    memory_file = proj / ".cursor" / "memory.md"

    if not memory_file.exists():
        return json.dumps({
            "exists": False,
            "message": "记忆文件不存在。",
        })

    content = memory_file.read_text(encoding="utf-8")
    entry_count = content.count("### ")

    if len(content) > 30_000:
        content = content[:15_000] + "\n\n... [中间省略] ...\n\n" + content[-10_000:]

    return json.dumps({
        "exists": True,
        "entries": entry_count,
        "content": content,
    }, ensure_ascii=False)


# ─── 团队记忆同步 ────────────────────────────────────────
# 替代 Claude Code 的 teamMemorySync 服务。
# 原理：用 Git 仓库中的 .cursor/team-memory/ 目录作为共享介质，
# 团队成员通过 git pull/push 自动同步。

import subprocess as _subprocess

def team_memory_sync(
    action: str = "status",
    project_dir: str = ".",
) -> str:
    """团队记忆同步：通过 Git 在团队成员间共享项目知识。

    利用 .cursor/team-memory/ 目录存放团队共享记忆，
    通过 git commit/push/pull 实现同步（替代 Claude Code 的服务端 API）。

    Args:
        action: 操作 — "status"(状态), "pull"(拉取), "push"(推送), "list"(列表)
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    team_dir = proj / ".cursor" / "team-memory"
    cwd = str(proj)

    if action == "status":
        if not team_dir.exists():
            return json.dumps({
                "initialized": False,
                "message": "团队记忆目录不存在。使用 team_memory_save 创建第一条记忆后自动初始化。",
                "hint": "确保 .cursor/team-memory/ 已加入 Git 跟踪（不在 .gitignore 中）",
            }, ensure_ascii=False, indent=2)

        # 统计文件
        entries = list(team_dir.glob("*.md"))
        # 检查 git 跟踪状态
        git_status = _subprocess.run(
            ["git", "status", "--porcelain", str(team_dir.relative_to(proj))],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        untracked = [l for l in git_status.stdout.strip().split("\n") if l.strip().startswith("?")]
        modified = [l for l in git_status.stdout.strip().split("\n") if l.strip() and not l.strip().startswith("?")]

        return json.dumps({
            "initialized": True,
            "directory": str(team_dir.relative_to(proj)),
            "entries": len(entries),
            "git_status": {
                "untracked": len(untracked),
                "modified": len(modified),
            },
            "sync_hint": "运行 team_memory_sync(action='push') 推送到远程",
        }, ensure_ascii=False, indent=2)

    elif action == "pull":
        result = _subprocess.run(
            ["git", "pull", "--rebase", "origin"],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        return json.dumps({
            "success": result.returncode == 0,
            "output": result.stdout.strip() or result.stderr.strip(),
            "hint": "团队记忆已通过 git pull 同步到本地",
        }, ensure_ascii=False, indent=2)

    elif action == "push":
        team_dir.mkdir(parents=True, exist_ok=True)
        rel = str(team_dir.relative_to(proj))

        # git add
        _subprocess.run(["git", "add", rel], cwd=cwd, capture_output=True, timeout=5)
        # 检查是否有变更
        diff = _subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        team_changes = [f for f in diff.stdout.strip().split("\n") if f.strip() and "team-memory" in f]

        if not team_changes:
            return json.dumps({
                "success": True,
                "message": "没有待推送的团队记忆变更",
            }, ensure_ascii=False, indent=2)

        # commit + push
        commit = _subprocess.run(
            ["git", "commit", "-m", f"chore: sync team memory ({len(team_changes)} files)"],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
        if commit.returncode != 0:
            return json.dumps({
                "success": False,
                "error": commit.stderr.strip(),
            }, ensure_ascii=False, indent=2)

        push = _subprocess.run(
            ["git", "push"],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        return json.dumps({
            "success": push.returncode == 0,
            "files_pushed": len(team_changes),
            "output": push.stdout.strip() or push.stderr.strip(),
        }, ensure_ascii=False, indent=2)

    elif action == "list":
        if not team_dir.exists():
            return json.dumps({"entries": [], "message": "团队记忆目录不存在"})

        entries = []
        for f in sorted(team_dir.glob("*.md")):
            if f.name == "INDEX.md":
                continue
            content = f.read_text(encoding="utf-8", errors="replace")
            title = content.split("\n")[0].lstrip("# ").strip() if content else f.stem
            entries.append({
                "file": f.name,
                "title": title,
                "size": f.stat().st_size,
            })

        return json.dumps({
            "directory": str(team_dir.relative_to(proj)),
            "entries": entries,
            "total": len(entries),
        }, ensure_ascii=False, indent=2)

    return json.dumps({"error": f"未知操作: {action}。支持: status, pull, push, list"})


def team_memory_save(
    key: str,
    content: str,
    category: str = "convention",
    project_dir: str = ".",
) -> str:
    """保存一条团队共享记忆（通过 Git 同步）。

    与个人 memory_save 不同，团队记忆保存在 .cursor/team-memory/ 目录，
    提交到 Git 后所有团队成员可通过 git pull 获取。

    Args:
        key: 记忆标题/关键词
        content: 记忆内容
        category: 分类 — convention(团队约定), architecture(架构决策), api(API 契约), workflow(工作流), gotcha(陷阱)
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    team_dir = proj / ".cursor" / "team-memory"
    team_dir.mkdir(parents=True, exist_ok=True)

    valid_categories = {"convention", "architecture", "api", "workflow", "gotcha"}
    if category not in valid_categories:
        return json.dumps({
            "error": f"无效分类: {category}",
            "valid": list(valid_categories),
        })

    # 文件名安全化
    safe_key = re.sub(r'[^\w\-]', '-', key.lower()).strip('-')[:50]
    filename = f"{category}-{safe_key}.md"
    filepath = team_dir / filename

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Secret 检查（对应 Claude Code 的 teamMemSecretGuard）
    secret_patterns = [
        re.compile(r'(?:password|passwd|pwd)\s*[:=]\s*\S+', re.IGNORECASE),
        re.compile(r'(?:api[_-]?key|token|secret)\s*[:=]\s*["\']?\S{16,}', re.IGNORECASE),
        re.compile(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----'),
        re.compile(r'AKIA[0-9A-Z]{16}'),
        re.compile(r'ghp_[A-Za-z0-9_]{36}'),
        re.compile(r'sk-[A-Za-z0-9]{32,}'),
    ]
    for pattern in secret_patterns:
        if pattern.search(content):
            return json.dumps({
                "error": "检测到可能的 secret/密钥，已阻止写入团队记忆",
                "hint": "团队记忆会通过 Git 同步，不应包含密钥。请使用个人 memory_save 代替。",
            }, ensure_ascii=False, indent=2)

    md_content = f"---\ncategory: {category}\nauthor: auto\ndate: {timestamp}\n---\n\n"
    md_content += f"# {key}\n\n{content}\n"

    filepath.write_text(md_content, encoding="utf-8")

    # 更新索引
    index_file = team_dir / "INDEX.md"
    index_line = f"- [{key}]({filename}) — {category}\n"
    if index_file.exists():
        existing = index_file.read_text(encoding="utf-8")
        # 去重
        if filename not in existing:
            with index_file.open("a", encoding="utf-8") as f:
                f.write(index_line)
    else:
        index_file.write_text(
            "# Team Memory Index\n\n> 团队共享知识库。通过 Git 同步。\n\n" + index_line,
            encoding="utf-8",
        )

    return json.dumps({
        "success": True,
        "file": str(filepath.relative_to(proj)),
        "key": key,
        "category": category,
        "sync_hint": "运行 team_memory_sync(action='push') 推送到远程分享给团队",
    }, ensure_ascii=False, indent=2)
