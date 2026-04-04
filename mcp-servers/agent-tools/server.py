"""
Agent Tools MCP Server — 为 Cursor 提供 Claude Code 风格的增强工具。

突破 Cursor 内置工具的限制，提供：
1. token_count      — 估算文件/文本的 token 数量
2. context_budget   — 追踪当前会话的上下文使用情况
3. project_map      — 一次性生成项目结构摘要
4. dependency_graph — 分析模块间的 import 依赖关系
5. permission_check — 工具权限检查（借鉴 claude-code-rust 双层权限模型）
6. metrics_record   — 性能指标记录（文件持久化，借鉴 claude-code-rust MetricsCollector）
7. metrics_report   — 性能报告生成（从持久化数据读取）
8. memory_save      — 跨会话记忆保存（文件持久化至 ~/.cursor/memory/memories.json）
9. memory_search    — 记忆搜索（支持类型/标签/重要度过滤）
10. memory_consolidate — 记忆整合（Jaccard 相似度去重，持久化）

启动方式：
  pip install mcp fastmcp tiktoken
  python server.py

在 Cursor 中配置：
  Settings → MCP → Add Server → stdio → command: python, args: ["/path/to/server.py"]
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

try:
    import tiktoken
    _encoder = tiktoken.encoding_for_model("gpt-4")
    def count_tokens(text: str) -> int:
        return len(_encoder.encode(text))
except ImportError:
    def count_tokens(text: str) -> int:
        """粗略估算：1 token ≈ 4 字符（英文）/ 2 字符（中文）"""
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        non_ascii = len(text) - ascii_chars
        return ascii_chars // 4 + non_ascii // 2

mcp = FastMCP("agent-tools")


@mcp.tool()
def token_count(text: str = "", file_path: str = "") -> str:
    """估算文本或文件的 token 数量。帮助 Agent 做上下文预算决策。

    Args:
        text: 要计算的文本内容（与 file_path 二选一）
        file_path: 要计算的文件路径（与 text 二选一）
    """
    if file_path:
        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            return json.dumps({"error": f"文件不存在: {file_path}"})
        if path.stat().st_size > 1_000_000:
            return json.dumps({"error": "文件超过 1MB，跳过计算"})
        text = path.read_text(encoding="utf-8", errors="replace")

    if not text:
        return json.dumps({"error": "请提供 text 或 file_path"})

    tokens = count_tokens(text)
    lines = text.count("\n") + 1
    chars = len(text)

    return json.dumps({
        "tokens": tokens,
        "lines": lines,
        "characters": chars,
        "recommendation": (
            "直接读取" if tokens < 1000
            else "建议用 Grep 定位后按区域读取" if tokens < 5000
            else "建议用 SemanticSearch 或 Grep，避免整文件读取"
        ),
    })


@mcp.tool()
def project_map(root_dir: str = ".", max_depth: int = 3, show_sizes: bool = False) -> str:
    """生成项目目录结构摘要，一次性了解整个项目布局。

    Args:
        root_dir: 项目根目录路径
        max_depth: 最大扫描深度
        show_sizes: 是否显示文件行数
    """
    root = Path(root_dir).expanduser().resolve()
    if not root.is_dir():
        return json.dumps({"error": f"目录不存在: {root_dir}"})

    IGNORE_DIRS = {
        "node_modules", ".git", "dist", "build", "__pycache__",
        ".next", ".nuxt", "target", "vendor", ".venv", "venv",
        "coverage", ".tox", ".mypy_cache", ".pytest_cache",
    }
    IGNORE_FILES = {".DS_Store", "Thumbs.db", "package-lock.json", "bun.lock", "yarn.lock"}

    tree_lines: list[str] = []
    file_count = 0
    dir_count = 0
    lang_stats: dict[str, int] = {}

    def walk(path: Path, prefix: str, depth: int):
        nonlocal file_count, dir_count
        if depth > max_depth:
            return

        try:
            entries = sorted(path.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return

        dirs = [e for e in entries if e.is_dir() and e.name not in IGNORE_DIRS and not e.name.startswith(".")]
        files = [e for e in entries if e.is_file() and e.name not in IGNORE_FILES]

        for i, d in enumerate(dirs):
            is_last = (i == len(dirs) - 1) and len(files) == 0
            connector = "└── " if is_last else "├── "
            tree_lines.append(f"{prefix}{connector}{d.name}/")
            dir_count += 1
            extension = "    " if is_last else "│   "
            walk(d, prefix + extension, depth + 1)

        for i, f in enumerate(files):
            is_last = i == len(files) - 1
            connector = "└── " if is_last else "├── "
            size_info = ""
            if show_sizes:
                try:
                    line_count = sum(1 for _ in f.open("r", encoding="utf-8", errors="replace"))
                    size_info = f" ({line_count} lines)"
                except Exception:
                    size_info = ""
            tree_lines.append(f"{prefix}{connector}{f.name}{size_info}")
            file_count += 1

            ext = f.suffix.lower()
            if ext:
                lang_stats[ext] = lang_stats.get(ext, 0) + 1

    walk(root, "", 0)

    top_langs = sorted(lang_stats.items(), key=lambda x: -x[1])[:10]

    return json.dumps({
        "root": str(root),
        "tree": "\n".join(tree_lines[:500]),
        "summary": {
            "directories": dir_count,
            "files": file_count,
            "top_extensions": {ext: cnt for ext, cnt in top_langs},
        },
        "truncated": len(tree_lines) > 500,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def dependency_graph(file_path: str, max_depth: int = 2) -> str:
    """分析一个文件的 import/require 依赖关系，返回依赖树。

    Args:
        file_path: 要分析的入口文件
        max_depth: 依赖追踪深度
    """
    root = Path(file_path).expanduser().resolve()
    if not root.exists():
        return json.dumps({"error": f"文件不存在: {file_path}"})

    IMPORT_PATTERNS = [
        re.compile(r'''import\s+.*?\s+from\s+['"]([^'"]+)['"]'''),
        re.compile(r'''import\s*\(\s*['"]([^'"]+)['"]\s*\)'''),
        re.compile(r'''require\s*\(\s*['"]([^'"]+)['"]\s*\)'''),
        re.compile(r'''from\s+(\S+)\s+import'''),
    ]

    visited: set[str] = set()
    graph: dict[str, list[str]] = {}

    def extract_imports(path: Path) -> list[str]:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []
        imports = []
        for pattern in IMPORT_PATTERNS:
            imports.extend(pattern.findall(content))
        return imports

    def resolve_import(imp: str, from_file: Path) -> Path | None:
        if imp.startswith("."):
            base = from_file.parent / imp
            for ext in ["", ".ts", ".tsx", ".js", ".jsx", ".py", "/index.ts", "/index.js"]:
                candidate = Path(str(base) + ext)
                if candidate.exists():
                    return candidate.resolve()
        return None

    def walk(path: Path, depth: int):
        key = str(path)
        if key in visited or depth > max_depth:
            return
        visited.add(key)

        imports = extract_imports(path)
        resolved: list[str] = []
        for imp in imports:
            target = resolve_import(imp, path)
            if target:
                resolved.append(str(target))
                walk(target, depth + 1)
            else:
                resolved.append(f"[external] {imp}")

        graph[key] = resolved

    walk(root, 0)

    return json.dumps({
        "entry": str(root),
        "graph": graph,
        "total_files": len(graph),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def test_runner(
    file_path: str = "",
    test_name: str = "",
    project_dir: str = ".",
) -> str:
    """智能测试运行器：自动检测项目测试框架并运行相关测试。

    Args:
        file_path: 要测试的源文件路径（自动推断对应的测试文件）
        test_name: 运行特定测试名称（可选）
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()

    framework = _detect_test_framework(proj)
    if not framework:
        return json.dumps({"error": "未检测到测试框架。支持: jest, vitest, pytest, go test, cargo test"})

    cmd = _build_test_command(framework, file_path, test_name, proj)
    if not cmd:
        return json.dumps({"error": f"无法为 {framework} 构建测试命令"})

    try:
        result = subprocess.run(
            cmd, cwd=str(proj), capture_output=True, text=True, timeout=120,
        )
        output = result.stdout + result.stderr
        if len(output) > 10000:
            output = output[:5000] + "\n\n... [truncated] ...\n\n" + output[-3000:]

        return json.dumps({
            "framework": framework,
            "command": " ".join(cmd),
            "exit_code": result.returncode,
            "passed": result.returncode == 0,
            "output": output,
        }, ensure_ascii=False)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "测试超时（120s）", "command": " ".join(cmd)})
    except Exception as e:
        return json.dumps({"error": str(e), "command": " ".join(cmd)})


def _detect_test_framework(proj: Path) -> str | None:
    if (proj / "vitest.config.ts").exists() or (proj / "vitest.config.js").exists():
        return "vitest"
    if (proj / "jest.config.ts").exists() or (proj / "jest.config.js").exists():
        return "jest"
    pkg = proj / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text())
            scripts = data.get("scripts", {})
            if "vitest" in scripts.get("test", ""):
                return "vitest"
            if "jest" in scripts.get("test", ""):
                return "jest"
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "vitest" in deps:
                return "vitest"
            if "jest" in deps:
                return "jest"
        except Exception:
            pass
    if (proj / "pytest.ini").exists() or (proj / "pyproject.toml").exists() or (proj / "setup.py").exists():
        return "pytest"
    if (proj / "go.mod").exists():
        return "go"
    if (proj / "Cargo.toml").exists():
        return "cargo"
    return None


def _build_test_command(
    framework: str, file_path: str, test_name: str, proj: Path,
) -> list[str] | None:
    test_file = _find_test_file(file_path, proj) if file_path else ""

    if framework == "vitest":
        cmd = ["npx", "vitest", "run"]
        if test_file:
            cmd.append(test_file)
        if test_name:
            cmd.extend(["-t", test_name])
        return cmd

    if framework == "jest":
        cmd = ["npx", "jest", "--no-coverage"]
        if test_file:
            cmd.extend(["--testPathPattern", test_file])
        if test_name:
            cmd.extend(["-t", test_name])
        return cmd

    if framework == "pytest":
        cmd = ["python", "-m", "pytest", "-x", "-v"]
        if test_file:
            cmd.append(test_file)
        if test_name:
            cmd.extend(["-k", test_name])
        return cmd

    if framework == "go":
        pkg = "./..."
        if file_path:
            pkg = "./" + str(Path(file_path).parent)
        cmd = ["go", "test", "-v", pkg]
        if test_name:
            cmd.extend(["-run", test_name])
        return cmd

    if framework == "cargo":
        cmd = ["cargo", "test"]
        if test_name:
            cmd.append(test_name)
        cmd.append("--")
        cmd.append("--nocapture")
        return cmd

    return None


def _find_test_file(source_path: str, proj: Path) -> str:
    """根据源文件路径推断对应的测试文件。"""
    if not source_path:
        return ""
    src = Path(source_path)
    stem = src.stem
    ext = src.suffix

    candidates = [
        src.parent / f"{stem}.test{ext}",
        src.parent / f"{stem}.spec{ext}",
        src.parent / f"test_{stem}{ext}",
        src.parent / "__tests__" / f"{stem}{ext}",
        src.parent / "__tests__" / f"{stem}.test{ext}",
        proj / "tests" / f"test_{stem}{ext}",
        proj / "tests" / f"{stem}_test{ext}",
    ]

    for candidate in candidates:
        full = (proj / candidate) if not candidate.is_absolute() else candidate
        if full.exists():
            return str(candidate)
    return ""


# ─── 工具权限系统（借鉴 claude-code-rust Tool trait 权限模型） ────────────────

TOOL_PERMISSION_RULES: dict = {
    "always_allow": [],
    "always_deny": [],
    "always_ask": [],
}

DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"mkfs\.",
    r"dd\s+if=",
    r">\s*/dev/sd",
    r"chmod\s+-R\s+777",
    r"curl\s+.*\|\s*(bash|sh)",
    r"wget\s+.*\|\s*(bash|sh)",
]


@mcp.tool()
def permission_check(
    tool_name: str,
    tool_input: str = "",
    mode: str = "default",
    language: str = "zh",
) -> str:
    """Check tool permission before execution. / 执行工具前检查权限。

    Adapted from claude-code-rust's dual-layer permission model
    (tools::permissions + security::permissions).

    Args:
        tool_name: Name of the tool to check
        tool_input: Input content to check (e.g. shell command)
        mode: Permission mode — "default", "bypass", "plan"
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    for rule in TOOL_PERMISSION_RULES.get("always_allow", []):
        if re.match(rule, tool_name):
            label = "允许（always_allow 规则）" if is_zh else "Allowed (always_allow rule)"
            return json.dumps({"decision": "allow", "reason": label}, ensure_ascii=False)

    for rule in TOOL_PERMISSION_RULES.get("always_deny", []):
        if re.match(rule, tool_name):
            label = "拒绝（always_deny 规则）" if is_zh else "Denied (always_deny rule)"
            return json.dumps({"decision": "deny", "reason": label}, ensure_ascii=False)

    danger_hits = []
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, tool_input, re.IGNORECASE):
            danger_hits.append(pat)

    if danger_hits:
        label = "需要确认（检测到危险模式）" if is_zh else "Requires approval (dangerous pattern detected)"
        return json.dumps({
            "decision": "ask",
            "reason": label,
            "matched_patterns": danger_hits,
        }, ensure_ascii=False, indent=2)

    if mode == "bypass":
        label = "允许（bypass 模式）" if is_zh else "Allowed (bypass mode)"
        return json.dumps({"decision": "allow", "reason": label}, ensure_ascii=False)

    label = "允许" if is_zh else "Allowed"
    return json.dumps({"decision": "allow", "reason": label}, ensure_ascii=False)


# ─── 性能指标收集（文件持久化，借鉴 claude-code-rust performance/metrics） ────

import time as _time

_METRICS_DIR = Path(os.environ.get("METRICS_DIR", Path.home() / ".cursor" / "metrics"))
_METRICS_FILE = _METRICS_DIR / "metrics.json"
_METRICS_START = _time.time()


def _load_metrics() -> dict:
    """从 JSON 文件加载指标数据。"""
    if _METRICS_FILE.exists():
        try:
            return json.loads(_METRICS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"counters": {}, "start_time": _METRICS_START}


def _save_metrics(data: dict) -> None:
    """将指标数据写回 JSON 文件。"""
    _METRICS_DIR.mkdir(parents=True, exist_ok=True)
    _METRICS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@mcp.tool()
def metrics_record(
    event: str,
    value: float = 1.0,
    attributes: str = "{}",
    language: str = "zh",
) -> str:
    """Record a performance metric (persisted to file). / 记录性能指标（持久化到文件）。

    Args:
        event: Metric event name (e.g. "tool_call", "api_latency_ms", "token_usage")
        value: Metric value (default 1.0 for counters)
        attributes: JSON string of key-value attributes
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    try:
        attrs = json.loads(attributes) if attributes else {}
    except json.JSONDecodeError:
        attrs = {}

    data = _load_metrics()
    counters = data.setdefault("counters", {})
    if event not in counters:
        counters[event] = {"total": 0.0, "count": 0, "history": []}

    counters[event]["total"] += value
    counters[event]["count"] += 1
    counters[event]["history"].append({
        "value": value,
        "time": _time.time(),
        "attributes": attrs,
    })
    if len(counters[event]["history"]) > 200:
        counters[event]["history"] = counters[event]["history"][-200:]

    _save_metrics(data)

    msg = f"已记录 {event}={value}" if is_zh else f"Recorded {event}={value}"
    return json.dumps({"status": "ok", "message": msg, "event": event,
                        "file": str(_METRICS_FILE),
                        "total": counters[event]["total"],
                        "count": counters[event]["count"]}, ensure_ascii=False)


@mcp.tool()
def metrics_report(language: str = "zh") -> str:
    """Generate a performance report (from persisted data). / 生成性能报告。

    Args:
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")
    data = _load_metrics()
    uptime = _time.time() - data.get("start_time", _METRICS_START)

    report = {}
    for name, counter in data.get("counters", {}).items():
        avg = counter["total"] / counter["count"] if counter["count"] > 0 else 0
        report[name] = {
            "total": counter["total"],
            "count": counter["count"],
            "average": round(avg, 3),
        }

    title = "性能报告" if is_zh else "Performance Report"
    return json.dumps({
        "title": title,
        "uptime_seconds": round(uptime, 1),
        "file": str(_METRICS_FILE),
        "metrics": report,
    }, ensure_ascii=False, indent=2)


# ─── 记忆管理系统（文件持久化，统一 code-intel/memory.py 与本模块） ────────────

import hashlib as _hashlib
from pathlib import Path as _Path

MEMORY_TYPES = ["session", "conversation", "knowledge", "preference", "task", "error", "insight"]

_MEMORY_DIR = _Path(os.environ.get("MEMORY_DIR", _Path.home() / ".cursor" / "memory"))
_MEMORY_FILE = _MEMORY_DIR / "memories.json"
_MAX_MEMORIES = 500


def _load_memories() -> list[dict]:
    """从 JSON 文件加载记忆。"""
    if not _MEMORY_FILE.exists():
        return []
    try:
        return json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_memories(entries: list[dict]) -> None:
    """将记忆写回 JSON 文件。"""
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    _MEMORY_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


@mcp.tool()
def memory_save(
    content: str,
    memory_type: str = "knowledge",
    importance: float = 0.5,
    tags: str = "",
    language: str = "zh",
) -> str:
    """Save a memory entry with file persistence. / 保存记忆条目（文件持久化，跨会话不丢失）。

    Args:
        content: Memory content
        memory_type: Type — session/conversation/knowledge/preference/task/error/insight
        importance: 0.0-1.0, higher = more important
        tags: Comma-separated tags
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    if memory_type not in MEMORY_TYPES:
        memory_type = "knowledge"

    entry_id = _hashlib.md5(f"{content}{_time.time()}".encode()).hexdigest()[:12]
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    entry = {
        "id": entry_id,
        "type": memory_type,
        "content": content,
        "importance": max(0.0, min(1.0, importance)),
        "tags": tag_list,
        "timestamp": _time.time(),
    }

    entries = _load_memories()
    entries.append(entry)

    # 超出上限时按 importance 最低淘汰
    if len(entries) > _MAX_MEMORIES:
        entries.sort(key=lambda e: e["importance"])
        entries = entries[-(int(_MAX_MEMORIES)):]

    _save_memories(entries)

    msg = f"记忆已持久化保存 (id={entry_id})" if is_zh else f"Memory persisted (id={entry_id})"
    return json.dumps({"status": "ok", "message": msg, "id": entry_id,
                        "file": str(_MEMORY_FILE),
                        "total_memories": len(entries)}, ensure_ascii=False)


@mcp.tool()
def memory_search(
    query: str = "",
    memory_type: str = "",
    tags: str = "",
    min_importance: float = 0.0,
    limit: int = 20,
    language: str = "zh",
) -> str:
    """Search persisted memories. / 搜索持久化记忆。

    Args:
        query: Text substring to search for
        memory_type: Filter by type (empty = all)
        tags: Comma-separated tags to filter
        min_importance: Minimum importance threshold
        limit: Max results to return
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")
    tag_filter = {t.strip() for t in tags.split(",") if t.strip()} if tags else set()

    entries = _load_memories()
    results = []
    for entry in entries:
        if memory_type and entry.get("type") != memory_type:
            continue
        if entry.get("importance", 0) < min_importance:
            continue
        if query and query.lower() not in entry.get("content", "").lower():
            continue
        if tag_filter and not tag_filter.intersection(entry.get("tags", [])):
            continue
        results.append(entry)

    results.sort(key=lambda e: e.get("importance", 0), reverse=True)
    results = results[:limit]

    title = f"找到 {len(results)} 条记忆" if is_zh else f"Found {len(results)} memories"
    return json.dumps({"title": title, "count": len(results), "results": results},
                       ensure_ascii=False, indent=2)


@mcp.tool()
def memory_consolidate(language: str = "zh") -> str:
    """Consolidate memories: remove low-value duplicates (persisted). / 整合记忆：删除低价值重复项。

    Args:
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    entries = _load_memories()
    before = len(entries)
    if before == 0:
        msg = "无记忆可整合" if is_zh else "No memories to consolidate"
        return json.dumps({"status": "ok", "message": msg}, ensure_ascii=False)

    to_remove: set[int] = set()
    for i, a in enumerate(entries):
        if i in to_remove:
            continue
        words_a = set(a.get("content", "").lower().split())
        for j in range(i + 1, len(entries)):
            if j in to_remove:
                continue
            b = entries[j]
            if a.get("type") != b.get("type"):
                continue
            words_b = set(b.get("content", "").lower().split())
            inter = len(words_a & words_b)
            union = len(words_a | words_b)
            if union > 0 and inter / union > 0.7:
                victim = i if a.get("importance", 0) <= b.get("importance", 0) else j
                to_remove.add(victim)

    kept = [e for idx, e in enumerate(entries) if idx not in to_remove]
    _save_memories(kept)

    after = len(kept)
    msg = (f"整合完成：{before} → {after}（移除 {before - after} 条重复）" if is_zh
           else f"Consolidation done: {before} → {after} (removed {before - after} duplicates)")
    return json.dumps({"status": "ok", "message": msg,
                        "before": before, "after": after,
                        "removed": before - after}, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
