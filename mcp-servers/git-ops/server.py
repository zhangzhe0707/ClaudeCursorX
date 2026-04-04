"""
Git Operations MCP Server — 高级 Git 操作工具集。

对应 Claude Code 的 EnterWorktreeTool、/commit、/review 和 PR 相关能力，
将复杂的 Git 多步操作封装为安全的单次调用。

工具列表：
1. safe_commit    — 安全提交：检查 diff、排除 secrets、验证后提交
2. review_diff    — 审查当前分支与基准分支的差异，生成结构化报告
3. create_pr      — 创建 Pull Request（依赖 gh CLI）
4. stash_switch   — 安全切换分支（自动 stash 当前变更）
5. worktree_ops   — Git Worktree 操作（创建/列表/清理）
6. branch_status  — 分支全景：远程同步状态、未推送提交、冲突检测
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

# fastmcp 默认将 INFO 日志输出到 stderr，Cursor 会将其误标为 error，统一静默
logging.basicConfig(level=logging.WARNING)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("请先安装依赖: pip install mcp fastmcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("git-ops")

SECRET_PATTERNS = [
    re.compile(r'(?:password|passwd|pwd)\s*[:=]\s*["\'][^"\']+["\']', re.IGNORECASE),
    re.compile(r'(?:api[_-]?key|apikey)\s*[:=]\s*["\'][^"\']+["\']', re.IGNORECASE),
    re.compile(r'(?:secret|token)\s*[:=]\s*["\'][A-Za-z0-9+/=_-]{16,}["\']', re.IGNORECASE),
    re.compile(r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----'),
    re.compile(r'AKIA[0-9A-Z]{16}'),  # AWS Access Key
    re.compile(r'ghp_[A-Za-z0-9_]{36}'),  # GitHub PAT
    re.compile(r'sk-[A-Za-z0-9]{32,}'),  # OpenAI API Key
]

SENSITIVE_FILES = {
    ".env", ".env.local", ".env.production", ".env.staging",
    "credentials.json", "secrets.yaml", "secrets.yml",
    "id_rsa", "id_ed25519", "id_ecdsa",
    ".npmrc", ".pypirc",
}


def _git(args: list[str], cwd: str, timeout: int = 30) -> dict:
    """执行 git 命令并返回结构化结果。"""
    cmd = ["git"] + args
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "ok": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"超时 ({timeout}s)", "ok": False}
    except FileNotFoundError:
        return {"exit_code": -1, "stdout": "", "stderr": "git 未安装", "ok": False}


def _gh(args: list[str], cwd: str, timeout: int = 30) -> dict:
    """执行 gh CLI 命令并返回结构化结果。"""
    cmd = ["gh"] + args
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "ok": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "stdout": "", "stderr": f"超时 ({timeout}s)", "ok": False}
    except FileNotFoundError:
        return {"exit_code": -1, "stdout": "", "stderr": "gh CLI 未安装。请先安装: https://cli.github.com/", "ok": False}


def _check_secrets_in_diff(diff_text: str) -> list[dict]:
    """检查 diff 中是否包含 secrets/credentials。"""
    warnings = []
    for i, line in enumerate(diff_text.split("\n")):
        if not line.startswith("+"):
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(line):
                warnings.append({
                    "line": i + 1,
                    "pattern": pattern.pattern[:50],
                    "content": line[:100].strip(),
                })
    return warnings


def _check_sensitive_files(files: list[str]) -> list[str]:
    """检查是否包含敏感文件。"""
    found = []
    for f in files:
        name = Path(f).name
        if name in SENSITIVE_FILES:
            found.append(f)
        if name.endswith(".pem") or name.endswith(".key"):
            found.append(f)
    return found


@mcp.tool()
def safe_commit(
    message: str = "",
    files: str = "",
    project_dir: str = ".",
    amend: bool = False,
    allow_empty: bool = False,
) -> str:
    """安全提交：检查 diff、排除 secrets、验证后提交。

    在提交前自动执行安全检查：
    - 检查 diff 中是否包含密钥/密码
    - 检查是否包含敏感文件（.env, credentials.json 等）
    - 如果发现风险，报告但不阻止（由 Agent 决策）

    Args:
        message: 提交信息（留空则自动生成）
        files: 要提交的文件（逗号分隔），留空则提交所有已暂存的文件
        project_dir: 项目根目录
        amend: 是否修改最后一次提交
        allow_empty: 是否允许空提交
    """
    proj = Path(project_dir).expanduser().resolve()
    cwd = str(proj)

    # 如果指定了文件，先 add
    if files:
        file_list = [f.strip() for f in files.split(",") if f.strip()]
        sensitive = _check_sensitive_files(file_list)
        if sensitive:
            return json.dumps({
                "error": "检测到敏感文件，已阻止提交",
                "sensitive_files": sensitive,
                "recommendation": "请从提交列表中移除这些文件，或添加到 .gitignore",
            }, ensure_ascii=False, indent=2)

        for f in file_list:
            add_result = _git(["add", f], cwd)
            if not add_result["ok"]:
                return json.dumps({"error": f"git add 失败: {f}", "detail": add_result["stderr"]})

    # 检查暂存区状态
    status = _git(["status", "--porcelain"], cwd)
    staged = _git(["diff", "--cached", "--name-only"], cwd)

    if not staged["stdout"] and not allow_empty and not amend:
        return json.dumps({
            "error": "暂存区没有文件。请先 git add 或指定 files 参数",
            "status": status["stdout"][:2000],
        }, ensure_ascii=False)

    # 安全检查：diff 中的 secrets
    diff_result = _git(["diff", "--cached"], cwd)
    secret_warnings = _check_secrets_in_diff(diff_result["stdout"])

    # 安全检查：敏感文件
    staged_files = staged["stdout"].split("\n") if staged["stdout"] else []
    sensitive_files = _check_sensitive_files(staged_files)

    # 自动生成 commit message
    if not message:
        message = _auto_commit_message(diff_result["stdout"], staged_files)

    # 构建 commit 命令
    commit_args = ["commit", "-m", message]
    if amend:
        commit_args = ["commit", "--amend", "-m", message]
    if allow_empty:
        commit_args.append("--allow-empty")

    commit_result = _git(commit_args, cwd)

    result = {
        "success": commit_result["ok"],
        "message": message,
        "files_committed": staged_files,
        "output": commit_result["stdout"] or commit_result["stderr"],
    }

    if secret_warnings:
        result["warnings"] = {
            "secrets_detected": len(secret_warnings),
            "details": secret_warnings[:5],
            "note": "已提交但检测到可能的 secrets，请确认是否安全",
        }

    if sensitive_files:
        result["warnings"] = result.get("warnings", {})
        result["warnings"]["sensitive_files"] = sensitive_files

    return json.dumps(result, ensure_ascii=False, indent=2)


def _auto_commit_message(diff: str, files: list[str]) -> str:
    """根据 diff 内容自动生成简单的 commit message。"""
    if not files:
        return "chore: empty commit"

    added = diff.count("\n+") - diff.count("\n+++")
    removed = diff.count("\n-") - diff.count("\n---")

    file_types = set(Path(f).suffix for f in files if f)
    dirs = set(str(Path(f).parent) for f in files if f)

    if len(files) == 1:
        action = "update"
        if added > removed * 2:
            action = "add"
        elif removed > added * 2:
            action = "remove"
        return f"{action}: {files[0]}"

    primary_dir = min(dirs, key=len) if dirs else "."
    return f"update: modify {len(files)} files in {primary_dir} (+{added}/-{removed})"


@mcp.tool()
def review_diff(
    project_dir: str = ".",
    base_branch: str = "main",
    stat_only: bool = False,
) -> str:
    """审查当前分支与基准分支的差异，生成结构化审查报告。

    Args:
        project_dir: 项目根目录
        base_branch: 基准分支
        stat_only: 只返回统计信息不返回具体 diff
    """
    proj = Path(project_dir).expanduser().resolve()
    cwd = str(proj)

    # 获取当前分支
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    current_branch = branch["stdout"] if branch["ok"] else "unknown"

    # 获取 merge-base
    merge_base = _git(["merge-base", base_branch, "HEAD"], cwd)
    if not merge_base["ok"]:
        return json.dumps({
            "error": f"无法找到与 {base_branch} 的共同祖先",
            "detail": merge_base["stderr"],
            "hint": f"确保 {base_branch} 分支存在且已 fetch",
        }, ensure_ascii=False)

    base_ref = merge_base["stdout"][:12]

    # 统计信息
    stat = _git(["diff", "--stat", f"{base_branch}...HEAD"], cwd)
    numstat = _git(["diff", "--numstat", f"{base_branch}...HEAD"], cwd)

    files_changed: list[dict] = []
    total_added = 0
    total_removed = 0

    if numstat["ok"] and numstat["stdout"]:
        for line in numstat["stdout"].split("\n"):
            parts = line.split("\t")
            if len(parts) == 3:
                added = int(parts[0]) if parts[0] != "-" else 0
                removed = int(parts[1]) if parts[1] != "-" else 0
                total_added += added
                total_removed += removed
                files_changed.append({
                    "file": parts[2],
                    "added": added,
                    "removed": removed,
                    "net": added - removed,
                })

    # 提交历史
    log = _git(["log", "--oneline", f"{base_branch}...HEAD"], cwd)
    commits = log["stdout"].split("\n") if log["ok"] and log["stdout"] else []

    # Secret 检查
    full_diff = _git(["diff", f"{base_branch}...HEAD"], cwd) if not stat_only else {"stdout": ""}
    secrets = _check_secrets_in_diff(full_diff["stdout"]) if full_diff["stdout"] else []

    # 大文件检查
    large_changes = [f for f in files_changed if f["added"] + f["removed"] > 200]

    # 风险评估
    risk = "low"
    risk_factors = []
    if secrets:
        risk = "critical"
        risk_factors.append(f"检测到 {len(secrets)} 个可能的 secrets")
    if len(files_changed) > 20:
        risk = max(risk, "high")
        risk_factors.append(f"修改了 {len(files_changed)} 个文件（过多）")
    if total_added + total_removed > 1000:
        risk = max(risk, "medium")
        risk_factors.append(f"变更量大：+{total_added}/-{total_removed}")
    if large_changes:
        risk_factors.append(f"{len(large_changes)} 个文件变更超过 200 行")

    result = {
        "current_branch": current_branch,
        "base_branch": base_branch,
        "merge_base": base_ref,
        "summary": {
            "commits": len(commits),
            "files_changed": len(files_changed),
            "lines_added": total_added,
            "lines_removed": total_removed,
            "net_change": total_added - total_removed,
        },
        "risk_assessment": {
            "level": risk,
            "factors": risk_factors,
        },
        "commits": commits[:20],
        "files": sorted(files_changed, key=lambda x: -(x["added"] + x["removed"]))[:30],
        "secrets_detected": len(secrets),
    }

    if not stat_only and full_diff["stdout"]:
        diff_text = full_diff["stdout"]
        if len(diff_text) > 30_000:
            diff_text = diff_text[:15_000] + "\n\n... [中间省略] ...\n\n" + diff_text[-10_000:]
        result["diff"] = diff_text

    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def create_pr(
    title: str,
    body: str = "",
    base: str = "main",
    draft: bool = False,
    project_dir: str = ".",
) -> str:
    """创建 GitHub Pull Request。需要 gh CLI 已安装并认证。

    自动执行：推送当前分支 → 创建 PR。

    Args:
        title: PR 标题
        body: PR 描述（Markdown 格式）
        base: 目标分支
        draft: 是否创建草稿 PR
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    cwd = str(proj)

    # 获取当前分支
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if not branch["ok"]:
        return json.dumps({"error": "无法获取当前分支"})

    current_branch = branch["stdout"]
    if current_branch in ("main", "master"):
        return json.dumps({
            "error": f"当前在 {current_branch} 分支上，请先切换到功能分支",
            "hint": "git checkout -b feature/xxx",
        }, ensure_ascii=False)

    # 推送到远程
    push = _git(["push", "-u", "origin", current_branch], cwd, timeout=60)
    if not push["ok"] and "Everything up-to-date" not in push["stderr"]:
        return json.dumps({
            "error": "git push 失败",
            "detail": push["stderr"],
        }, ensure_ascii=False)

    # 如果没有 body，自动生成
    if not body:
        log = _git(["log", "--oneline", f"{base}...HEAD"], cwd)
        commits = log["stdout"].split("\n") if log["ok"] and log["stdout"] else []
        stat = _git(["diff", "--stat", f"{base}...HEAD"], cwd)

        body_parts = ["## 变更摘要\n"]
        if commits:
            body_parts.append("### 提交记录\n")
            for c in commits[:15]:
                body_parts.append(f"- {c}")
        if stat["ok"]:
            body_parts.append(f"\n### 文件变更\n```\n{stat['stdout'][:3000]}\n```")
        body = "\n".join(body_parts)

    # 创建 PR
    pr_args = ["pr", "create",
                "--title", title,
                "--body", body,
                "--base", base]
    if draft:
        pr_args.append("--draft")

    pr_result = _gh(pr_args, cwd, timeout=30)

    if pr_result["ok"]:
        pr_url = pr_result["stdout"].strip()
        return json.dumps({
            "success": True,
            "pr_url": pr_url,
            "branch": current_branch,
            "base": base,
            "title": title,
            "draft": draft,
        }, ensure_ascii=False, indent=2)
    else:
        return json.dumps({
            "success": False,
            "error": pr_result["stderr"],
            "hint": "确保 gh CLI 已安装并通过 `gh auth login` 认证",
        }, ensure_ascii=False, indent=2)


@mcp.tool()
def stash_switch(
    target_branch: str,
    create_new: bool = False,
    project_dir: str = ".",
) -> str:
    """安全切换分支：自动 stash 当前变更，切换后提示恢复。

    Args:
        target_branch: 目标分支名
        create_new: 是否创建新分支
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    cwd = str(proj)

    # 记录当前分支
    current = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    from_branch = current["stdout"] if current["ok"] else "unknown"

    # 检查工作区状态
    status = _git(["status", "--porcelain"], cwd)
    has_changes = bool(status["stdout"].strip())
    stashed = False

    if has_changes:
        stash_msg = f"auto-stash: switching from {from_branch} to {target_branch}"
        stash_result = _git(["stash", "push", "-m", stash_msg], cwd)
        if stash_result["ok"]:
            stashed = True
        else:
            return json.dumps({
                "error": "stash 失败",
                "detail": stash_result["stderr"],
                "hint": "可能有冲突，请手动处理",
            }, ensure_ascii=False)

    # 切换/创建分支
    if create_new:
        switch = _git(["checkout", "-b", target_branch], cwd)
    else:
        # 先尝试 fetch
        _git(["fetch", "origin", target_branch], cwd, timeout=15)
        switch = _git(["checkout", target_branch], cwd)

    if not switch["ok"]:
        # 切换失败，恢复 stash
        if stashed:
            _git(["stash", "pop"], cwd)
        return json.dumps({
            "error": f"切换到 {target_branch} 失败",
            "detail": switch["stderr"],
            "stash_restored": stashed,
        }, ensure_ascii=False)

    return json.dumps({
        "success": True,
        "from_branch": from_branch,
        "to_branch": target_branch,
        "created_new": create_new,
        "changes_stashed": stashed,
        "restore_hint": f"返回时运行: stash_switch('{from_branch}') 然后 git stash pop" if stashed else None,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def worktree_ops(
    action: str = "list",
    branch: str = "",
    path: str = "",
    project_dir: str = ".",
) -> str:
    """Git Worktree 操作：创建/列表/清理隔离的工作目录。

    Worktree 允许同时在多个分支上工作，不需要 stash/切换。

    Args:
        action: 操作类型 — "list"(列表), "create"(创建), "remove"(删除), "prune"(清理)
        branch: 分支名（create 时需要）
        path: Worktree 路径（create/remove 时需要，留空自动生成）
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    cwd = str(proj)

    if action == "list":
        result = _git(["worktree", "list", "--porcelain"], cwd)
        worktrees = []
        current: dict = {}
        for line in result["stdout"].split("\n"):
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[9:]}
            elif line.startswith("HEAD "):
                current["head"] = line[5:12]
            elif line.startswith("branch "):
                current["branch"] = line[7:].replace("refs/heads/", "")
            elif line == "bare":
                current["bare"] = True
            elif line == "detached":
                current["detached"] = True
        if current:
            worktrees.append(current)

        return json.dumps({
            "action": "list",
            "worktrees": worktrees,
            "count": len(worktrees),
        }, ensure_ascii=False, indent=2)

    elif action == "create":
        if not branch:
            return json.dumps({"error": "创建 worktree 需要指定 branch"})

        if not path:
            worktree_dir = proj.parent / f"{proj.name}-worktrees"
            worktree_dir.mkdir(exist_ok=True)
            path = str(worktree_dir / branch.replace("/", "-"))

        create_args = ["worktree", "add"]
        # 检查分支是否存在
        branch_check = _git(["rev-parse", "--verify", branch], cwd)
        if branch_check["ok"]:
            create_args.extend([path, branch])
        else:
            create_args.extend(["-b", branch, path])

        result = _git(create_args, cwd)
        return json.dumps({
            "action": "create",
            "success": result["ok"],
            "path": path,
            "branch": branch,
            "output": result["stdout"] or result["stderr"],
        }, ensure_ascii=False, indent=2)

    elif action == "remove":
        if not path:
            return json.dumps({"error": "删除 worktree 需要指定 path"})
        result = _git(["worktree", "remove", path, "--force"], cwd)
        return json.dumps({
            "action": "remove",
            "success": result["ok"],
            "path": path,
            "output": result["stdout"] or result["stderr"],
        }, ensure_ascii=False, indent=2)

    elif action == "prune":
        result = _git(["worktree", "prune"], cwd)
        return json.dumps({
            "action": "prune",
            "success": result["ok"],
            "output": result["stdout"] or result["stderr"],
        }, ensure_ascii=False, indent=2)

    else:
        return json.dumps({"error": f"未知操作: {action}。支持: list, create, remove, prune"})


@mcp.tool()
def branch_status(project_dir: str = ".", fetch_first: bool = True) -> str:
    """分支全景状态：远程同步、未推送提交、本地分支清单。

    Args:
        project_dir: 项目根目录
        fetch_first: 是否先执行 git fetch
    """
    proj = Path(project_dir).expanduser().resolve()
    cwd = str(proj)

    if fetch_first:
        _git(["fetch", "--all", "--prune"], cwd, timeout=30)

    # 当前分支
    current = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    current_branch = current["stdout"] if current["ok"] else "unknown"

    # 所有本地分支及追踪状态
    branches_raw = _git(["branch", "-vv", "--no-color"], cwd)
    branches = []
    for line in branches_raw["stdout"].split("\n"):
        line = line.strip()
        if not line:
            continue
        is_current = line.startswith("*")
        line = line.lstrip("* ")
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue

        branch_name = parts[0]
        commit_hash = parts[1]
        rest = parts[2] if len(parts) > 2 else ""

        tracking = ""
        ahead = 0
        behind = 0
        if rest.startswith("["):
            bracket_end = rest.index("]") if "]" in rest else len(rest)
            tracking_info = rest[1:bracket_end]
            tracking = tracking_info.split(":")[0]
            if "ahead" in tracking_info:
                m = re.search(r'ahead (\d+)', tracking_info)
                if m:
                    ahead = int(m.group(1))
            if "behind" in tracking_info:
                m = re.search(r'behind (\d+)', tracking_info)
                if m:
                    behind = int(m.group(1))

        branches.append({
            "name": branch_name,
            "current": is_current,
            "commit": commit_hash[:8],
            "tracking": tracking,
            "ahead": ahead,
            "behind": behind,
            "status": (
                "up-to-date" if tracking and ahead == 0 and behind == 0
                else f"ahead {ahead}" if ahead and not behind
                else f"behind {behind}" if behind and not ahead
                else f"diverged (ahead {ahead}, behind {behind})" if ahead and behind
                else "local-only" if not tracking
                else "unknown"
            ),
        })

    # 未推送提交
    unpushed = _git(["log", "--oneline", "@{upstream}..HEAD"], cwd)
    unpushed_commits = unpushed["stdout"].split("\n") if unpushed["ok"] and unpushed["stdout"] else []

    # stash 列表
    stash = _git(["stash", "list"], cwd)
    stashes = stash["stdout"].split("\n") if stash["ok"] and stash["stdout"] else []

    # 最近的标签
    tag = _git(["describe", "--tags", "--abbrev=0"], cwd)
    latest_tag = tag["stdout"] if tag["ok"] else None

    return json.dumps({
        "current_branch": current_branch,
        "branches": branches[:30],
        "total_branches": len(branches),
        "unpushed_commits": unpushed_commits[:10],
        "stashes": stashes[:10],
        "latest_tag": latest_tag,
        "summary": (
            f"当前在 {current_branch}, "
            f"{len(branches)} 个本地分支, "
            f"{len(unpushed_commits)} 个未推送提交, "
            f"{len(stashes)} 个 stash"
        ),
    }, ensure_ascii=False, indent=2)


# ─── MCP Prompts（移植自 Claude Code commit-commands 插件） ────────────────────

@mcp.prompt()
def quick_commit(language: str = "zh") -> str:
    """Create a git commit based on current changes. / 基于当前变更创建 git commit。

    Args:
        language: "zh" (default) or "en"
    """
    if language.strip().lower() not in ("en", "english"):
        return """请基于当前变更创建一个 git commit。

执行步骤：
1. 运行 `git status` 查看所有变更
2. 运行 `git diff HEAD` 查看具体差异
3. 运行 `git log --oneline -5` 参考最近的提交风格
4. 将相关文件加入暂存区（`git add`）
5. 用简洁有意义的提交信息提交（`git commit`）

提交信息规范：
- 第一行简短概括（不超过 50 字符），使用动词开头（如 feat/fix/docs/chore）
- 如有必要，空一行后写详细说明
- 参考项目已有的 commit 风格"""
    else:
        return """Please create a git commit based on current changes.

Steps:
1. Run `git status` to see all changes
2. Run `git diff HEAD` to see specific diffs
3. Run `git log --oneline -5` to reference recent commit style
4. Stage relevant files (`git add`)
5. Commit with a concise, meaningful message (`git commit`)

Commit message conventions:
- First line: short summary (<=50 chars), start with verb (feat/fix/docs/chore)
- If needed, add a blank line then detailed description
- Follow the project's existing commit style"""


@mcp.prompt()
def commit_push_pr(language: str = "zh") -> str:
    """Commit, push, and create a PR in one go. / 一键提交、推送并创建 PR。

    Args:
        language: "zh" (default) or "en"
    """
    if language.strip().lower() not in ("en", "english"):
        return """请完成以下完整的 Git 工作流：

1. **检查状态**：运行 `git status` 和 `git diff HEAD`
2. **创建分支**：如果在 main/master 上，先创建新的功能分支
3. **提交变更**：暂存并提交所有相关变更
4. **推送分支**：`git push -u origin <branch>`
5. **创建 PR**：使用 `gh pr create` 创建 Pull Request

注意事项：
- 分支命名：feature/<功能名> 或 fix/<问题名>
- PR 标题应简洁描述变更目的
- PR 描述应包含变更摘要和测试计划
- 确保不提交敏感文件（.env、credentials 等）"""
    else:
        return """Please complete the following Git workflow:

1. **Check status**: Run `git status` and `git diff HEAD`
2. **Create branch**: If on main/master, create a new feature branch first
3. **Commit changes**: Stage and commit all relevant changes
4. **Push branch**: `git push -u origin <branch>`
5. **Create PR**: Use `gh pr create` to create a Pull Request

Notes:
- Branch naming: feature/<name> or fix/<issue>
- PR title should concisely describe the change
- PR description should include summary and test plan
- Ensure no sensitive files are committed (.env, credentials, etc.)"""


@mcp.prompt()
def clean_gone_branches(language: str = "zh") -> str:
    """Clean up local branches deleted from remote. / 清理远程已删除的本地分支。

    Args:
        language: "zh" (default) or "en"
    """
    if language.strip().lower() not in ("en", "english"):
        return """请清理所有标记为 [gone] 的本地分支（远程已删除但本地仍存在的分支）。

执行步骤：
1. 运行 `git branch -v` 列出所有分支及状态
2. 运行 `git worktree list` 检查关联的 worktree
3. 对每个标记为 [gone] 的分支：
   - 如有关联 worktree，先 `git worktree remove --force` 移除
   - 然后 `git branch -D` 删除分支
4. 汇报清理结果

注意：不要删除当前所在的分支。如果没有 [gone] 分支，报告无需清理。"""
    else:
        return """Please clean up all local branches marked as [gone] (deleted from remote but still exist locally).

Steps:
1. Run `git branch -v` to list all branches with status
2. Run `git worktree list` to check associated worktrees
3. For each branch marked [gone]:
   - If it has a worktree, remove it with `git worktree remove --force`
   - Then delete the branch with `git branch -D`
4. Report cleanup results

Note: Do not delete the currently checked-out branch. If no [gone] branches exist, report that no cleanup was needed."""


if __name__ == "__main__":
    mcp.run(transport="stdio")
