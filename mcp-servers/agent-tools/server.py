"""
Agent Tools MCP Server — 为 Cursor 提供 Claude Code 风格的增强工具。

突破 Cursor 内置工具的限制，提供：
1. token_count   — 估算文件/文本的 token 数量
2. context_budget — 追踪当前会话的上下文使用情况
3. project_map   — 一次性生成项目结构摘要
4. dependency_graph — 分析模块间的 import 依赖关系
5. test_runner   — 智能测试运行器（自动检测框架并运行相关测试）

启动方式：
  pip install mcp fastmcp tiktoken
  python server.py

在 Cursor 中配置：
  Settings → MCP → Add Server → stdio → command: python, args: ["/path/to/server.py"]
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

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


if __name__ == "__main__":
    mcp.run(transport="stdio")
