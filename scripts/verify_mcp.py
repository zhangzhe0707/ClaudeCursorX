#!/usr/bin/env python3
"""
MCP Server 功能验证脚本

验证策略（分三层）：
  1. 语法层  — python -m py_compile，确保无语法错误
  2. 协议层  — MCP initialize + tools/list 握手，确认工具注册正确
  3. 功能层  — 直接 import + 调用工具函数，验证核心逻辑（绕过协议层竞争条件）

用法:
    python scripts/verify_mcp.py               # 验证所有服务器
    python scripts/verify_mcp.py agent-tools   # 只验证指定服务器
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
SERVERS_DIR = ROOT / "mcp-servers"

# (tool_name, arguments_dict, 期望结果中包含的 key 或 substring)
SMOKE_TESTS: dict[str, list[tuple[str, dict, str]]] = {
    "agent-tools": [
        ("token_count",      {"text": "hello world 这是测试"},         "tokens"),
        ("permission_check", {"tool_name": "shell", "tool_input": "ls -la"}, "allow"),
        ("permission_check", {"tool_name": "shell", "tool_input": "rm -rf /"}, "ask"),
        ("metrics_record",   {"event": "verify_run", "value": 1.0},   "ok"),
        ("metrics_report",   {},                                        "metrics"),
        ("memory_save",      {"content": "验证测试记忆", "memory_type": "knowledge", "importance": 0.3}, "ok"),
        ("memory_search",    {"query": "验证测试"},                    "results"),
        ("memory_consolidate", {},                                      "ok"),
    ],
    "code-intel": [
        ("project_overview",  {"project_dir": str(ROOT)},              "root"),
        ("memory_save",       {"key": "verify-test", "content": "验证写入", "project_dir": str(ROOT)}, "success"),
        ("memory_search",     {"query": "verify", "project_dir": str(ROOT)}, "entries"),
        ("memory_read",       {"project_dir": str(ROOT)},              "exists"),
    ],
    "dev-utils": [
        ("tool_search",       {"query": "memory"},                     "matched"),
        ("plugin_registry",   {"action": "list", "scan_dir": str(ROOT)}, "mcp_servers"),
        ("sandbox_check",     {"command": "ls -la /tmp"},              "safe"),
        ("sandbox_check",     {"command": "rm -rf /"},                 "critical"),
        ("feature_flags",     {"action": "list"},                      "features"),
        ("editor_detect",     {},                                       "shell"),
        ("context_compress",  {
            "messages_json": json.dumps([
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "写一个排序算法"},
                {"role": "assistant", "content": "好的，这是快速排序：\n```python\ndef qsort(a): return a if len(a)<=1 else qsort([x for x in a[1:] if x<=a[0]])+[a[0]]+qsort([x for x in a[1:] if x>a[0]])\n```"},
                {"role": "user", "content": "再给归并排序"},
            ]),
            "max_tokens": 150,
            "strategy": "smart",
        }, "status"),
        ("security_scan",     {"file_path": "test.py", "content": "password = 'abc123'", "language": "zh"}, "findings"),
        ("audit_log",         {"event_type": "tool_call", "details": "verify test"}, "ok"),
        ("audit_query",       {"limit": 5},                           "events"),
    ],
    "git-ops": [
        # branch_status 会调用 git fetch（网络操作），超时改用 safe_commit dry-run
        ("branch_status",    {"project_dir": str(ROOT), "fetch_first": False},  "current_branch"),
        ("review_diff",      {"project_dir": str(ROOT), "base_branch": "master"}, "commits"),
    ],
    "test-runner": [
        ("test_skeleton",    {"file_path": "mcp-servers/agent-tools/server.py", "project_dir": str(ROOT)}, "source_file"),
    ],
}

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m⚠\033[0m"


# ─── Layer 1: 语法检查 ────────────────────────────────────────────────────────

def check_syntax(server_py: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(server_py)],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        return False, result.stderr.strip()[-300:]
    return True, "OK"


# ─── Layer 2: MCP 协议工具列表 ────────────────────────────────────────────────

def _parse_jsonrpc_stream(text: str) -> dict[int, dict]:
    """从 stdout 流中解析所有 JSON-RPC 响应（支持多行 JSON）。"""
    decoder = json.JSONDecoder()
    responses: dict[int, dict] = {}
    pos = 0
    while pos < len(text):
        while pos < len(text) and text[pos] not in '{[':
            pos += 1
        if pos >= len(text):
            break
        try:
            obj, end = decoder.raw_decode(text, pos)
            pos = end
            if isinstance(obj, dict):
                rid = obj.get("id")
                if rid is not None and rid != 0:
                    responses[rid] = obj
        except json.JSONDecodeError:
            pos += 1
    return responses


def list_tools_via_protocol(server_py: Path) -> list[str] | None:
    """通过 MCP stdio 协议获取工具列表（含 initialize 握手）。"""
    stdin_data = (
        '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{'
        '"protocolVersion":"2024-11-05","capabilities":{"tools":{}},'
        '"clientInfo":{"name":"verify","version":"1.0"}}}\n'
        '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n'
        '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n'
    )
    try:
        proc = subprocess.run(
            [sys.executable, str(server_py)],
            input=stdin_data, capture_output=True, text=True, timeout=20,
        )
        responses = _parse_jsonrpc_stream(proc.stdout)
        resp = responses.get(1, {})
        return [t["name"] for t in resp.get("result", {}).get("tools", [])]
    except Exception:
        return None


# ─── Layer 3: 直接函数调用（最可靠）────────────────────────────────────────────

_CALL_TEMPLATE = textwrap.dedent("""\
    import sys, json, os
    sys.path.insert(0, {server_dir!r})
    os.chdir({root!r})

    # 阻止 mcp.run() 在 import 时启动
    import unittest.mock as _mock
    _patcher = _mock.patch('fastmcp.FastMCP.run', return_value=None)
    _patcher.start()

    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location('_server', {server_path!r})
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)

    _fn = getattr(_mod, {tool_name!r})
    _result = _fn(**{arguments!r})
    print(json.dumps({{"result": _result}}, ensure_ascii=False))
""")


def call_tool_direct(server_py: Path, tool_name: str, arguments: dict, timeout: int = 30) -> tuple[bool, str]:
    """直接 import 服务器模块并调用工具函数，绕过 MCP 协议层。"""
    code = _CALL_TEMPLATE.format(
        server_dir=str(server_py.parent),
        root=str(ROOT),
        server_path=str(server_py),
        tool_name=tool_name,
        arguments=arguments,
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        return False, "超时"
    except Exception as e:
        return False, str(e)

    if proc.returncode != 0:
        err = proc.stderr.strip()[-300:]
        return False, f"进程异常: {err}"

    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            wrapper = json.loads(line)
            if "result" in wrapper:
                raw = wrapper["result"]
                # 工具返回值可能是 JSON 字符串，也可能是 dict
                if isinstance(raw, str):
                    try:
                        inner = json.loads(raw)
                        if isinstance(inner, dict) and "error" in inner:
                            return False, f"工具错误: {inner['error']}"
                        summary = json.dumps(inner, ensure_ascii=False)
                    except json.JSONDecodeError:
                        summary = raw
                else:
                    summary = json.dumps(raw, ensure_ascii=False)
                return True, summary[:150]
        except json.JSONDecodeError:
            pass

    stdout_snippet = proc.stdout.strip()[:200]
    return False, f"无法解析输出: {stdout_snippet!r}"


# ─── 主验证流程 ────────────────────────────────────────────────────────────────

def verify_server(name: str, server_py: Path, tests: list[tuple[str, dict, str]]) -> dict:
    print(f"\n{'='*64}")
    print(f"  {name}  ({server_py.relative_to(ROOT)})")
    print(f"{'='*64}")

    result = {"server": name, "pass": 0, "fail": 0}

    # Layer 1: 语法
    ok, msg = check_syntax(server_py)
    print(f"  {PASS if ok else FAIL} [语法]  {msg}")
    if not ok:
        result["fail"] += len(tests)
        return result

    # Layer 2: 工具列表（协议层）
    declared = server_py.read_text(encoding="utf-8").count("@mcp.tool()")
    tools = list_tools_via_protocol(server_py)
    if tools is not None:
        icon = "✓" if len(tools) >= declared - 2 else "⚠"
        print(f"  {icon} [协议]  声明 {declared} 个 @mcp.tool()，协议返回 {len(tools)} 个")
        if tools:
            print(f"           └─ {', '.join(tools)}")
    else:
        print(f"  {WARN} [协议]  工具列表获取失败（声明 {declared} 个）")

    print()

    # Layer 3: 功能调用
    for tool_name, args, expect in tests:
        t0 = time.time()
        ok, summary = call_tool_direct(server_py, tool_name, args)
        elapsed = time.time() - t0

        # 可选：验证期望关键字
        if ok and expect and expect not in summary:
            ok = False
            summary = f"期望含 '{expect}'，实际: {summary[:120]}"

        status = PASS if ok else FAIL
        print(f"  {status} {tool_name:<34} ({elapsed:.1f}s)")
        print(f"       └─ {summary[:120]}")
        if ok:
            result["pass"] += 1
        else:
            result["fail"] += 1

    return result


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else None
    all_results = []

    servers_to_test: dict[str, tuple[Path, list]] = {}
    for name, tests in SMOKE_TESTS.items():
        if target and target != name:
            continue
        server_py = SERVERS_DIR / name / "server.py"
        if not server_py.exists():
            print(f"{WARN} 跳过 {name}：找不到 {server_py}")
            continue
        servers_to_test[name] = (server_py, tests)

    if not servers_to_test:
        print(f"没有找到要测试的服务器（target={target}）")
        sys.exit(1)

    print(f"\nClaudeCursorX MCP Server 验证  ({len(servers_to_test)} 个服务器)")
    print(f"项目根目录: {ROOT}")

    for name, (server_py, tests) in servers_to_test.items():
        r = verify_server(name, server_py, tests)
        all_results.append(r)

    total_pass = sum(r["pass"] for r in all_results)
    total_fail = sum(r["fail"] for r in all_results)
    total = total_pass + total_fail

    print(f"\n{'='*64}")
    print(f"  汇总结果: {total_pass}/{total} 通过  "
          f"{'✓ 全部通过' if total_fail == 0 else f'✗ {total_fail} 个失败'}")
    print(f"{'='*64}")
    for r in all_results:
        icon = "✓" if r["fail"] == 0 else "✗"
        print(f"  {icon} {r['server']:<24} {r['pass']}/{r['pass']+r['fail']} 通过")

    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
