"""
Agent Tools MCP Server — 为 Cursor 提供独有的增强工具。

精简后保留 4 个核心工具（与其他 MCP 服务器无重叠）：
1. token_count      — 估算文件/文本的 token 数量
2. permission_check — 工具权限检查（多层权限模型）
3. metrics_record   — 性能指标记录（文件持久化）
4. metrics_report   — 性能报告生成

已移除（由专职 MCP 服务器覆盖）：
- dependency_graph  → code-intel（更完整）
- test_runner       → test-runner/smart_test（更成体系）
- project_map       → code-intel/project_overview（更深入）
- memory_save/search/consolidate → code-intel/memory（避免同名冲突）

启动方式：
  pip install mcp fastmcp tiktoken
  python server.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time as _time
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("请先安装依赖: pip install mcp fastmcp", file=sys.stderr)
    sys.exit(1)

_tiktoken_encoder = None

def _get_tiktoken_encoder():
    """延迟初始化 tiktoken，避免模块加载时网络 I/O 阻塞 MCP 握手。"""
    global _tiktoken_encoder
    if _tiktoken_encoder is None:
        try:
            import tiktoken
            _tiktoken_encoder = tiktoken.encoding_for_model("gpt-4")
        except Exception:
            _tiktoken_encoder = False
    return _tiktoken_encoder

def count_tokens(text: str) -> int:
    encoder = _get_tiktoken_encoder()
    if encoder:
        return len(encoder.encode(text))
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii = len(text) - ascii_chars
    return ascii_chars // 4 + non_ascii // 2

mcp = FastMCP("agent-tools")


# ─── Tool 1: token_count ─────────────────────────────────────────────────────

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


# ─── Tool 2: permission_check ────────────────────────────────────────────────

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


def _check_path_globs(tool_input: str, allowed_globs: list[str], denied_globs: list[str]) -> tuple[str, str]:
    """检查路径是否匹配 glob 规则，返回 (decision, reason)。"""
    from fnmatch import fnmatch

    paths = []
    for token in tool_input.replace(",", " ").split():
        if "/" in token or token.endswith((".py", ".ts", ".js", ".json", ".yml", ".yaml",
                                           ".env", ".key", ".pem", ".lock")):
            paths.append(token)

    for path in paths:
        basename = os.path.basename(path)
        for pat in denied_globs:
            if fnmatch(path, pat) or fnmatch(basename, pat):
                return "deny", f"path '{path}' matches deny rule '{pat}'"

    if allowed_globs and paths:
        for path in paths:
            basename = os.path.basename(path)
            if not any(fnmatch(path, pat) or fnmatch(basename, pat) for pat in allowed_globs):
                return "ask", f"path '{path}' not in allowed_paths"

    return "allow", ""


@mcp.tool()
def permission_check(
    tool_name: str,
    tool_input: str = "",
    mode: str = "default",
    agent_name: str = "",
    allowed_paths: str = "",
    disallowed_tools: str = "",
    language: str = "zh",
) -> str:
    """Multi-layer permission check before tool execution. / 多层权限叠加检查。

    Implements OpenHarness-style multi-layer permission model:
      Layer 1: Global always_deny / always_allow rules
      Layer 2: Mode-based restrictions (plan → read-only, bypass → skip checks)
      Layer 3: Agent-level disallowed_tools list
      Layer 4: Path glob allow/deny rules
      Layer 5: Dangerous pattern scan (regex)

    Args:
        tool_name: Name of the tool to check
        tool_input: Input content to check (e.g. shell command or file path)
        mode: Permission mode — "default" | "plan" (read-only) | "bypass" (skip checks)
        agent_name: Agent requesting the tool (for agent-level restrictions)
        allowed_paths: Comma-separated glob patterns for allowed paths (e.g. "src/**,tests/**")
        disallowed_tools: Comma-separated tool names this agent cannot use
        language: "zh" (default) or "en"
    """
    is_zh = language.strip().lower() not in ("en", "english")

    def _result(decision: str, reason: str, layer: str, extra: dict | None = None) -> str:
        r: dict = {"decision": decision, "reason": reason, "layer": layer}
        if extra:
            r.update(extra)
        return json.dumps(r, ensure_ascii=False, indent=2)

    # Layer 1: Global always_deny / always_allow
    for rule in TOOL_PERMISSION_RULES.get("always_deny", []):
        if re.match(rule, tool_name):
            label = "拒绝（全局 always_deny 规则）" if is_zh else "Denied (global always_deny rule)"
            return _result("deny", label, "global_always_deny")

    for rule in TOOL_PERMISSION_RULES.get("always_allow", []):
        if re.match(rule, tool_name):
            label = "允许（全局 always_allow 规则）" if is_zh else "Allowed (global always_allow rule)"
            return _result("allow", label, "global_always_allow")

    # Layer 2: Mode-based restrictions
    if mode == "bypass":
        label = "允许（bypass 模式，跳过所有检查）" if is_zh else "Allowed (bypass mode, all checks skipped)"
        return _result("allow", label, "mode_bypass")

    WRITE_TOOLS = {"Write", "StrReplace", "Delete", "EditNotebook"}

    if mode == "plan" and tool_name in WRITE_TOOLS:
        label = (f"拒绝（plan 模式禁止写操作：{tool_name}）" if is_zh
                 else f"Denied (plan mode disallows write tool: {tool_name})")
        return _result("deny", label, "mode_plan")

    # Layer 3: Agent-level disallowed_tools
    if disallowed_tools:
        denied_set = {t.strip() for t in disallowed_tools.split(",") if t.strip()}
        if tool_name in denied_set:
            agent_label = f" (agent: {agent_name})" if agent_name else ""
            label = (f"拒绝（Agent 禁用工具列表{agent_label}）" if is_zh
                     else f"Denied (agent disallowed_tools{agent_label})")
            return _result("deny", label, "agent_disallowed_tools")

    # Layer 4: Path glob rules
    SENSITIVE_PATH_PATTERNS = [
        "*.env", ".env*", "*credentials*", "*secret*", "*.key", "*.pem",
        "*.p12", "*.pfx", "id_rsa", "id_ed25519",
    ]
    WARN_PATH_PATTERNS = ["*.lock", "package-lock.json", "yarn.lock", "go.sum"]

    allowed_glob_list = [p.strip() for p in allowed_paths.split(",") if p.strip()]
    denied_glob_list = SENSITIVE_PATH_PATTERNS.copy()

    if tool_name in WRITE_TOOLS:
        path_decision, path_reason = _check_path_globs(
            tool_input, allowed_glob_list, denied_glob_list
        )
        if path_decision == "deny":
            label = (f"拒绝（敏感路径：{path_reason}）" if is_zh
                     else f"Denied (sensitive path: {path_reason})")
            return _result("deny", label, "path_deny")

        _, warn_reason = _check_path_globs(tool_input, [], WARN_PATH_PATTERNS)
        if warn_reason:
            label = (f"需要确认（修改锁文件：{warn_reason}）" if is_zh
                     else f"Requires confirmation (modifying lock file: {warn_reason})")
            return _result("ask", label, "path_warn")

        if path_decision == "ask":
            label = (f"需要确认（路径超出允许范围：{path_reason}）" if is_zh
                     else f"Requires confirmation (path outside allowed range: {path_reason})")
            return _result("ask", label, "path_outside_allowed")

    # Layer 5: Dangerous pattern scan
    danger_hits = []
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, tool_input, re.IGNORECASE):
            danger_hits.append(pat)

    if danger_hits:
        label = "需要确认（检测到危险命令模式）" if is_zh else "Requires approval (dangerous command pattern detected)"
        return _result("ask", label, "dangerous_pattern",
                       {"matched_patterns": danger_hits})

    label = "允许（通过所有权限层检查）" if is_zh else "Allowed (passed all permission layers)"
    return _result("allow", label, "default")


# ─── Tool 3 & 4: metrics_record / metrics_report ─────────────────────────────

_METRICS_DIR = Path(os.environ.get("METRICS_DIR", Path.home() / ".cursor" / "metrics"))
_METRICS_FILE = _METRICS_DIR / "metrics.json"
_METRICS_START = _time.time()


def _load_metrics() -> dict:
    if _METRICS_FILE.exists():
        try:
            return json.loads(_METRICS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"counters": {}, "start_time": _METRICS_START}


def _save_metrics(data: dict) -> None:
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


if __name__ == "__main__":
    mcp.run(transport="stdio")
