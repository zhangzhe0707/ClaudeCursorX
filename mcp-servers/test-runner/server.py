"""
Test Runner MCP Server — 智能测试工具集。

对应 Claude Code Worker Agent 的测试执行能力，将"推断测试文件 → 检测框架 →
构建命令 → 执行 → 解析结果"整合为一步调用。

工具列表：
1. smart_test        — 根据修改的文件自动选择并运行最相关的测试
2. regression_check  — 基于 git diff 分析回归风险，运行受影响的测试
3. test_skeleton     — 为源文件生成测试骨架（框架、mock 结构）
4. test_report       — 解析测试输出，生成结构化通过/失败报告
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

mcp = FastMCP("test-runner")

# ─── 框架检测 ──────────────────────────────────────────────

FRAMEWORK_CONFIGS = {
    "vitest": {
        "markers": ["vitest.config.ts", "vitest.config.js", "vitest.config.mts"],
        "pkg_keys": ["vitest"],
        "script_hints": ["vitest"],
        "run_cmd": ["npx", "vitest", "run", "--reporter=verbose"],
        "file_flag": None,  # vitest 直接追加文件路径
        "name_flag": ["-t"],
        "ext": [".ts", ".tsx", ".js", ".jsx"],
    },
    "jest": {
        "markers": ["jest.config.ts", "jest.config.js", "jest.config.cjs"],
        "pkg_keys": ["jest"],
        "script_hints": ["jest"],
        "run_cmd": ["npx", "jest", "--no-coverage", "--verbose"],
        "file_flag": ["--testPathPattern"],
        "name_flag": ["-t"],
        "ext": [".ts", ".tsx", ".js", ".jsx"],
    },
    "pytest": {
        "markers": ["pytest.ini", "pyproject.toml", "setup.cfg", "conftest.py"],
        "pkg_keys": [],
        "script_hints": [],
        "run_cmd": ["python", "-m", "pytest", "-x", "-v", "--tb=short"],
        "file_flag": None,
        "name_flag": ["-k"],
        "ext": [".py"],
    },
    "go": {
        "markers": ["go.mod"],
        "pkg_keys": [],
        "script_hints": [],
        "run_cmd": ["go", "test", "-v", "-count=1"],
        "file_flag": None,
        "name_flag": ["-run"],
        "ext": [".go"],
    },
    "cargo": {
        "markers": ["Cargo.toml"],
        "pkg_keys": [],
        "script_hints": [],
        "run_cmd": ["cargo", "test"],
        "file_flag": None,
        "name_flag": None,
        "ext": [".rs"],
    },
}


def _detect_framework(proj: Path) -> str | None:
    """优先级：vitest > jest > pytest > go > cargo。"""
    for name, cfg in FRAMEWORK_CONFIGS.items():
        for marker in cfg["markers"]:
            if (proj / marker).exists():
                return name

    pkg_path = proj / "package.json"
    if pkg_path.exists():
        try:
            pkg = json.loads(pkg_path.read_text())
            scripts = pkg.get("scripts", {})
            test_script = scripts.get("test", "")
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

            for name, cfg in FRAMEWORK_CONFIGS.items():
                if any(h in test_script for h in cfg.get("script_hints", [])):
                    return name
                if any(k in deps for k in cfg.get("pkg_keys", [])):
                    return name
        except Exception:
            pass

    # pytest: 检查有没有 test_*.py 文件
    for f in proj.rglob("test_*.py"):
        if "__pycache__" not in str(f):
            return "pytest"

    return None


def _find_test_files(source_path: str, proj: Path, framework: str) -> list[str]:
    """根据源文件路径推断对应的测试文件。"""
    if not source_path:
        return []
    src = Path(source_path)
    if src.is_absolute():
        src = src.relative_to(proj) if src.is_relative_to(proj) else src
    stem = src.stem
    ext = src.suffix

    # 多种命名约定
    patterns = [
        f"{stem}.test{ext}",
        f"{stem}.spec{ext}",
        f"test_{stem}{ext}",
        f"{stem}_test{ext}",
        f"{stem}Test{ext}",
        f"test_{stem}.py",
        f"{stem}_test.go",
    ]

    found = []
    for tf in proj.rglob("*"):
        if not tf.is_file():
            continue
        if any(p in str(tf) for p in ["node_modules", "__pycache__", ".git", "dist"]):
            continue
        if tf.name in patterns:
            rel = str(tf.relative_to(proj)) if tf.is_relative_to(proj) else str(tf)
            found.append(rel)

    # 也检查 __tests__ 目录
    tests_dir = src.parent / "__tests__"
    for pattern_name in [f"{stem}{ext}", f"{stem}.test{ext}", f"{stem}.spec{ext}"]:
        candidate = proj / tests_dir / pattern_name
        if candidate.exists():
            rel = str(candidate.relative_to(proj))
            if rel not in found:
                found.append(rel)

    return found


def _run_command(cmd: list[str], cwd: str, timeout: int = 120) -> dict:
    """执行命令并返回结构化结果。"""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        stdout = result.stdout
        stderr = result.stderr
        output = stdout + stderr

        if len(output) > 15_000:
            output = output[:7_000] + "\n\n... [中间省略] ...\n\n" + output[-5_000:]

        return {
            "exit_code": result.returncode,
            "passed": result.returncode == 0,
            "output": output,
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "passed": False, "output": f"测试超时（{timeout}s）"}
    except FileNotFoundError as e:
        return {"exit_code": -1, "passed": False, "output": f"命令不存在: {e}"}


def _build_cmd(framework: str, cfg: dict, test_file: str = "", test_name: str = "") -> list[str]:
    """根据框架配置构建测试命令。"""
    cmd = list(cfg["run_cmd"])

    if test_file:
        if cfg["file_flag"]:
            cmd.extend(cfg["file_flag"])
        cmd.append(test_file)

    if test_name and cfg.get("name_flag"):
        cmd.extend(cfg["name_flag"])
        cmd.append(test_name)

    if framework == "cargo" and (test_file or test_name):
        cmd.extend(["--", "--nocapture"])

    return cmd


# ─── 工具实现 ──────────────────────────────────────────────

@mcp.tool()
def smart_test(
    file_path: str = "",
    test_name: str = "",
    project_dir: str = ".",
    timeout: int = 120,
) -> str:
    """根据修改的文件自动选择并运行最相关的测试。

    自动完成：检测框架 → 推断测试文件 → 构建命令 → 执行 → 解析结果。

    Args:
        file_path: 修改的源文件路径（自动推断对应测试文件）
        test_name: 运行特定测试名称（可选）
        project_dir: 项目根目录
        timeout: 超时秒数
    """
    proj = Path(project_dir).expanduser().resolve()
    framework = _detect_framework(proj)
    if not framework:
        return json.dumps({
            "error": "未检测到测试框架",
            "hint": "支持: vitest, jest, pytest, go test, cargo test",
            "checked_path": str(proj),
        })

    cfg = FRAMEWORK_CONFIGS[framework]

    # 查找测试文件
    test_files = _find_test_files(file_path, proj, framework) if file_path else []

    if file_path and not test_files:
        # 如果文件本身就是测试文件
        if any(k in file_path.lower() for k in ["test", "spec"]):
            test_files = [file_path]
        else:
            return json.dumps({
                "framework": framework,
                "source_file": file_path,
                "error": "未找到对应的测试文件",
                "searched_patterns": [
                    f"{Path(file_path).stem}.test{Path(file_path).suffix}",
                    f"{Path(file_path).stem}.spec{Path(file_path).suffix}",
                    f"test_{Path(file_path).stem}{Path(file_path).suffix}",
                ],
                "recommendation": f"建议创建测试文件: {Path(file_path).stem}.test{Path(file_path).suffix}",
            })

    results = []
    if test_files:
        for tf in test_files[:3]:
            cmd = _build_cmd(framework, cfg, tf, test_name)
            run_result = _run_command(cmd, str(proj), timeout)
            results.append({
                "test_file": tf,
                "command": " ".join(cmd),
                **run_result,
            })
    else:
        cmd = _build_cmd(framework, cfg, test_name=test_name)
        run_result = _run_command(cmd, str(proj), timeout)
        results.append({
            "test_file": "(all tests)",
            "command": " ".join(cmd),
            **run_result,
        })

    all_passed = all(r["passed"] for r in results)

    return json.dumps({
        "framework": framework,
        "source_file": file_path or "(none)",
        "tests_run": len(results),
        "all_passed": all_passed,
        "results": results,
    }, ensure_ascii=False)


@mcp.tool()
def regression_check(project_dir: str = ".", base_branch: str = "main", timeout: int = 180) -> str:
    """基于 git diff 分析哪些文件被修改，自动运行受影响的测试。

    Args:
        project_dir: 项目根目录
        base_branch: 基准分支（默认 main）
        timeout: 超时秒数
    """
    proj = Path(project_dir).expanduser().resolve()

    framework = _detect_framework(proj)
    if not framework:
        return json.dumps({"error": "未检测到测试框架"})

    cfg = FRAMEWORK_CONFIGS[framework]

    # 获取 git diff 的文件列表
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", base_branch],
            cwd=str(proj), capture_output=True, text=True, timeout=10,
        )
        if diff_result.returncode != 0:
            # 尝试 HEAD 与工作区的 diff
            diff_result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=str(proj), capture_output=True, text=True, timeout=10,
            )
    except Exception as e:
        return json.dumps({"error": f"git diff 失败: {e}"})

    changed_files = [f.strip() for f in diff_result.stdout.strip().split("\n") if f.strip()]

    if not changed_files:
        # 也检查暂存区
        try:
            staged = subprocess.run(
                ["git", "diff", "--name-only", "--cached"],
                cwd=str(proj), capture_output=True, text=True, timeout=10,
            )
            changed_files = [f.strip() for f in staged.stdout.strip().split("\n") if f.strip()]
        except Exception:
            pass

    if not changed_files:
        return json.dumps({
            "message": "没有检测到文件变更",
            "base_branch": base_branch,
        })

    # 分类变更文件
    source_changes = []
    test_changes = []
    config_changes = []

    for f in changed_files:
        if any(k in f.lower() for k in ["test", "spec", "__tests__"]):
            test_changes.append(f)
        elif any(k in f.lower() for k in ["config", ".json", ".yaml", ".toml", ".lock"]):
            config_changes.append(f)
        elif any(f.endswith(ext) for ext in cfg["ext"]):
            source_changes.append(f)

    # 为每个变更的源文件找测试
    affected_tests: list[str] = list(test_changes)
    for src in source_changes:
        tests = _find_test_files(src, proj, framework)
        for t in tests:
            if t not in affected_tests:
                affected_tests.append(t)

    # 运行受影响的测试
    results = []
    if affected_tests:
        for tf in affected_tests[:10]:
            cmd = _build_cmd(framework, cfg, tf)
            run_result = _run_command(cmd, str(proj), timeout)
            results.append({
                "test_file": tf,
                "command": " ".join(cmd),
                **run_result,
            })
    elif config_changes and not source_changes:
        # 配置变更，跑全部测试
        cmd = _build_cmd(framework, cfg)
        run_result = _run_command(cmd, str(proj), timeout)
        results.append({
            "test_file": "(full suite — config change detected)",
            "command": " ".join(cmd),
            **run_result,
        })

    all_passed = all(r["passed"] for r in results) if results else True
    failed = [r for r in results if not r["passed"]]

    risk_level = "low"
    if config_changes:
        risk_level = "medium"
    if len(source_changes) > 5:
        risk_level = "high"
    if failed:
        risk_level = "critical"

    return json.dumps({
        "framework": framework,
        "base_branch": base_branch,
        "changes": {
            "source_files": source_changes[:20],
            "test_files": test_changes[:20],
            "config_files": config_changes[:10],
            "total": len(changed_files),
        },
        "risk_level": risk_level,
        "tests_run": len(results),
        "all_passed": all_passed,
        "failures": [{
            "test_file": r["test_file"],
            "output": r["output"][-2000:],
        } for r in failed],
        "results": results,
    }, ensure_ascii=False)


@mcp.tool()
def test_skeleton(file_path: str, project_dir: str = ".", style: str = "auto") -> str:
    """为源文件生成测试骨架代码（框架配置、import、mock 结构、测试用例占位）。

    不运行任何测试，仅生成代码模板，交给 Agent 填充具体逻辑。

    Args:
        file_path: 要生成测试的源文件路径
        project_dir: 项目根目录
        style: 测试风格 — "auto"(自动检测), "unit", "integration"
    """
    proj = Path(project_dir).expanduser().resolve()
    src = Path(file_path).expanduser().resolve()
    if not src.exists():
        return json.dumps({"error": f"文件不存在: {file_path}"})

    content = src.read_text(encoding="utf-8", errors="replace")
    lang = _detect_lang(src)
    framework = _detect_framework(proj) or ("pytest" if lang == "python" else "vitest")

    # 提取公共 API
    exports = _extract_public_api(content, lang)
    if not exports:
        return json.dumps({
            "error": "未检测到可测试的公共 API",
            "hint": "确保文件有 export/def/func 声明",
        })

    # 生成骨架
    rel = str(src.relative_to(proj)) if src.is_relative_to(proj) else str(src)

    if framework in ("vitest", "jest"):
        skeleton = _gen_ts_skeleton(rel, exports, framework)
    elif framework == "pytest":
        skeleton = _gen_python_skeleton(rel, exports)
    elif framework == "go":
        skeleton = _gen_go_skeleton(rel, exports)
    else:
        skeleton = _gen_ts_skeleton(rel, exports, "vitest")

    # 推荐的测试文件路径
    test_path = _suggest_test_path(rel, framework)

    return json.dumps({
        "source_file": rel,
        "framework": framework,
        "test_file_path": test_path,
        "exports_found": len(exports),
        "skeleton": skeleton,
        "next_steps": [
            f"将骨架代码保存到 {test_path}",
            "填充每个 test/it 块中的具体断言逻辑",
            "根据需要添加 mock/stub",
            f"运行: smart_test(file_path='{rel}')",
        ],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def test_report(test_output: str, framework: str = "auto") -> str:
    """解析测试输出文本，生成结构化的通过/失败报告。

    Args:
        test_output: 测试命令的原始输出文本
        framework: 测试框架名称（auto 自动检测）
    """
    if framework == "auto":
        if "PASS" in test_output and ("vitest" in test_output.lower() or "jest" in test_output.lower()):
            framework = "vitest/jest"
        elif "PASSED" in test_output or "FAILED" in test_output and "pytest" in test_output.lower():
            framework = "pytest"
        elif "--- PASS:" in test_output or "--- FAIL:" in test_output:
            framework = "go"
        else:
            framework = "generic"

    passed_tests: list[str] = []
    failed_tests: list[str] = []
    skipped_tests: list[str] = []
    errors: list[str] = []

    if framework in ("vitest/jest", "vitest", "jest"):
        for line in test_output.split("\n"):
            line = line.strip()
            if line.startswith("✓") or line.startswith("√") or "PASS" in line:
                passed_tests.append(line[:120])
            elif line.startswith("✕") or line.startswith("×") or "FAIL" in line:
                failed_tests.append(line[:120])
            elif "skip" in line.lower() or "todo" in line.lower():
                skipped_tests.append(line[:120])

    elif framework == "pytest":
        for line in test_output.split("\n"):
            line = line.strip()
            if " PASSED" in line:
                passed_tests.append(line[:120])
            elif " FAILED" in line:
                failed_tests.append(line[:120])
            elif " SKIPPED" in line:
                skipped_tests.append(line[:120])
            elif "ERROR" in line and "::" in line:
                errors.append(line[:120])

    elif framework == "go":
        for line in test_output.split("\n"):
            line = line.strip()
            if line.startswith("--- PASS:"):
                passed_tests.append(line[:120])
            elif line.startswith("--- FAIL:"):
                failed_tests.append(line[:120])
            elif line.startswith("--- SKIP:"):
                skipped_tests.append(line[:120])

    else:
        for line in test_output.split("\n"):
            line = line.strip()
            lower = line.lower()
            if "pass" in lower and ("test" in lower or "::" in line):
                passed_tests.append(line[:120])
            elif "fail" in lower and ("test" in lower or "::" in line):
                failed_tests.append(line[:120])

    total = len(passed_tests) + len(failed_tests) + len(skipped_tests)

    return json.dumps({
        "framework": framework,
        "summary": {
            "total": total,
            "passed": len(passed_tests),
            "failed": len(failed_tests),
            "skipped": len(skipped_tests),
            "errors": len(errors),
            "pass_rate": f"{len(passed_tests) / total * 100:.1f}%" if total > 0 else "N/A",
        },
        "all_passed": len(failed_tests) == 0 and len(errors) == 0,
        "failed_tests": failed_tests[:20],
        "errors": errors[:10],
        "passed_tests": passed_tests[:30],
        "skipped_tests": skipped_tests[:10],
    }, ensure_ascii=False, indent=2)


# ─── 辅助函数 ──────────────────────────────────────────────

def _detect_lang(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}:
        return "typescript"
    if ext == ".py":
        return "python"
    if ext == ".go":
        return "go"
    if ext == ".rs":
        return "rust"
    return "unknown"


def _extract_public_api(content: str, lang: str) -> list[dict]:
    """提取文件中的公共 API（函数、类、常量）。"""
    exports = []

    if lang == "typescript":
        # export function/class/const
        for m in re.finditer(
            r'export\s+(?:default\s+)?(?:async\s+)?(function|class|const|let|type|interface)\s+(\w+)',
            content
        ):
            exports.append({"kind": m.group(1), "name": m.group(2)})
        # export { ... }
        for m in re.finditer(r'export\s*\{([^}]+)\}', content):
            for name in re.split(r'[,\s]+', m.group(1)):
                name = name.strip().split(" as ")[0].strip()
                if name and name.isidentifier():
                    exports.append({"kind": "re-export", "name": name})

    elif lang == "python":
        for m in re.finditer(r'^(def|class)\s+(\w+)', content, re.MULTILINE):
            if not m.group(2).startswith("_"):
                exports.append({"kind": m.group(1), "name": m.group(2)})

    elif lang == "go":
        for m in re.finditer(r'^func\s+(?:\(.*?\)\s+)?(\w+)', content, re.MULTILINE):
            name = m.group(1)
            if name[0].isupper():
                exports.append({"kind": "func", "name": name})
        for m in re.finditer(r'^type\s+(\w+)', content, re.MULTILINE):
            name = m.group(1)
            if name[0].isupper():
                exports.append({"kind": "type", "name": name})

    return exports


def _gen_ts_skeleton(rel_path: str, exports: list[dict], framework: str) -> str:
    """生成 TypeScript 测试骨架。"""
    import_names = [e["name"] for e in exports if e["kind"] not in ("type", "interface", "re-export")]
    type_names = [e["name"] for e in exports if e["kind"] in ("type", "interface")]

    import_path = rel_path.rsplit(".", 1)[0]
    if import_path.startswith("src/"):
        import_path = "@/" + import_path[4:]
    else:
        import_path = "./" + import_path

    lines = []
    desc_fn = "describe" if framework in ("vitest", "jest") else "describe"
    expect_import = f"import {{ describe, it, expect }} from '{framework}'" if framework == "vitest" else ""

    if expect_import:
        lines.append(expect_import)
    if import_names:
        lines.append(f"import {{ {', '.join(import_names)} }} from '{import_path}'")
    if type_names:
        lines.append(f"import type {{ {', '.join(type_names)} }} from '{import_path}'")
    lines.append("")

    # 为每个导出生成 describe 块
    for exp in exports:
        if exp["kind"] in ("type", "interface"):
            continue
        name = exp["name"]
        lines.append(f"{desc_fn}('{name}', () => {{")
        lines.append(f"  it('should work with valid input', () => {{")
        lines.append(f"    // TODO: 测试正常输入")
        lines.append(f"    // const result = {name}(...)")
        lines.append(f"    // expect(result).toBe(...)")
        lines.append(f"  }})")
        lines.append(f"")
        lines.append(f"  it('should handle edge cases', () => {{")
        lines.append(f"    // TODO: 测试边界条件")
        lines.append(f"  }})")
        lines.append(f"")
        lines.append(f"  it('should throw on invalid input', () => {{")
        lines.append(f"    // TODO: 测试异常输入")
        lines.append(f"    // expect(() => {name}(null)).toThrow()")
        lines.append(f"  }})")
        lines.append(f"}})")
        lines.append(f"")

    return "\n".join(lines)


def _gen_python_skeleton(rel_path: str, exports: list[dict]) -> str:
    """生成 Python pytest 测试骨架。"""
    module_path = rel_path.replace("/", ".").rsplit(".", 1)[0]

    lines = []
    lines.append("import pytest")
    names = [e["name"] for e in exports]
    if names:
        lines.append(f"from {module_path} import {', '.join(names)}")
    lines.append("")
    lines.append("")

    for exp in exports:
        name = exp["name"]
        if exp["kind"] == "class":
            lines.append(f"class Test{name}:")
            lines.append(f"    def test_init(self):")
            lines.append(f"        # TODO: 测试初始化")
            lines.append(f"        pass")
            lines.append(f"")
            lines.append(f"    def test_basic_behavior(self):")
            lines.append(f"        # TODO: 测试基本行为")
            lines.append(f"        pass")
        else:
            lines.append(f"def test_{name}_basic():")
            lines.append(f"    # TODO: 测试正常输入")
            lines.append(f"    # result = {name}(...)")
            lines.append(f"    # assert result == expected")
            lines.append(f"    pass")
            lines.append(f"")
            lines.append(f"def test_{name}_edge_cases():")
            lines.append(f"    # TODO: 测试边界条件")
            lines.append(f"    pass")
            lines.append(f"")
            lines.append(f"def test_{name}_error():")
            lines.append(f"    # TODO: 测试异常输入")
            lines.append(f"    # with pytest.raises(ValueError):")
            lines.append(f"    #     {name}(invalid_input)")
            lines.append(f"    pass")
        lines.append("")

    return "\n".join(lines)


def _gen_go_skeleton(rel_path: str, exports: list[dict]) -> str:
    """生成 Go 测试骨架。"""
    pkg_name = Path(rel_path).parent.name or "main"

    lines = []
    lines.append(f"package {pkg_name}")
    lines.append("")
    lines.append('import (')
    lines.append('\t"testing"')
    lines.append(")")
    lines.append("")

    for exp in exports:
        if exp["kind"] != "func":
            continue
        name = exp["name"]
        lines.append(f"func Test{name}(t *testing.T) {{")
        lines.append(f"\tt.Run(\"basic\", func(t *testing.T) {{")
        lines.append(f"\t\t// TODO: 测试正常输入")
        lines.append(f"\t}})")
        lines.append(f"")
        lines.append(f"\tt.Run(\"edge_case\", func(t *testing.T) {{")
        lines.append(f"\t\t// TODO: 测试边界条件")
        lines.append(f"\t}})")
        lines.append(f"}}")
        lines.append("")

    return "\n".join(lines)


def _suggest_test_path(source_rel: str, framework: str) -> str:
    """推荐测试文件的路径。"""
    src = Path(source_rel)
    stem = src.stem
    ext = src.suffix

    if framework in ("vitest", "jest"):
        return str(src.parent / f"{stem}.test{ext}")
    elif framework == "pytest":
        return str(Path("tests") / f"test_{stem}{ext}")
    elif framework == "go":
        return str(src.parent / f"{stem}_test{ext}")
    elif framework == "cargo":
        return str(src.parent / f"{stem}_test{ext}")
    return str(src.parent / f"{stem}.test{ext}")


@mcp.tool()
def coverage_report(
    project_dir: str = ".",
    file_path: str = "",
    timeout: int = 180,
) -> str:
    """运行测试并生成覆盖率报告。

    自动检测框架和覆盖率工具，运行覆盖率收集，返回结构化结果。

    Args:
        project_dir: 项目根目录
        file_path: 只分析特定文件的覆盖率（可选）
        timeout: 超时秒数
    """
    proj = Path(project_dir).expanduser().resolve()
    framework = _detect_framework(proj)
    if not framework:
        return json.dumps({"error": "未检测到测试框架"})

    cmd: list[str] = []
    coverage_dir = ""

    if framework in ("vitest", "jest"):
        if framework == "vitest":
            cmd = ["npx", "vitest", "run", "--coverage", "--reporter=verbose"]
        else:
            cmd = ["npx", "jest", "--coverage", "--verbose"]
        if file_path:
            cmd.append(file_path)
        coverage_dir = str(proj / "coverage")

    elif framework == "pytest":
        cmd = ["python", "-m", "pytest", "--cov", "-v", "--tb=short"]
        if file_path:
            src_module = file_path.replace("/", ".").rsplit(".", 1)[0]
            cmd.extend(["--cov=" + src_module])
        else:
            cmd.append("--cov=.")
        cmd.append("--cov-report=term-missing")

    elif framework == "go":
        pkg = "./..."
        if file_path:
            pkg = "./" + str(Path(file_path).parent)
        cmd = ["go", "test", "-v", "-coverprofile=coverage.out", pkg]

    elif framework == "cargo":
        cmd = ["cargo", "test"]
        # cargo-tarpaulin 是可选的覆盖率工具
        tarpaulin_check = subprocess.run(
            ["cargo", "tarpaulin", "--version"],
            cwd=str(proj), capture_output=True, timeout=5,
        )
        if tarpaulin_check.returncode == 0:
            cmd = ["cargo", "tarpaulin", "--out", "Stdout"]

    run_result = _run_command(cmd, str(proj), timeout)

    # 解析覆盖率数据
    coverage_data = _parse_coverage(run_result["output"], framework)

    # 尝试读取覆盖率摘要文件
    summary_file = None
    if framework in ("vitest", "jest") and coverage_dir:
        for candidate in ["coverage-summary.json", "coverage/coverage-summary.json"]:
            p = proj / candidate
            if p.exists():
                try:
                    summary_file = json.loads(p.read_text())
                except Exception:
                    pass

    if framework == "go":
        go_cov_file = proj / "coverage.out"
        if go_cov_file.exists():
            func_result = subprocess.run(
                ["go", "tool", "cover", "-func=coverage.out"],
                cwd=str(proj), capture_output=True, text=True, timeout=15,
            )
            if func_result.returncode == 0:
                coverage_data["func_coverage"] = func_result.stdout[-3000:]

    result = {
        "framework": framework,
        "command": " ".join(cmd),
        "tests_passed": run_result["passed"],
        "coverage": coverage_data,
    }

    if summary_file:
        total = summary_file.get("total", {})
        result["coverage_summary"] = {
            k: v.get("pct", "N/A") for k, v in total.items()
            if isinstance(v, dict) and "pct" in v
        }

    if not run_result["passed"]:
        result["test_output"] = run_result["output"][-3000:]

    return json.dumps(result, ensure_ascii=False, indent=2)


def _parse_coverage(output: str, framework: str) -> dict:
    """从测试输出中解析覆盖率数据。"""
    data: dict = {"raw_summary": ""}

    if framework == "pytest":
        # pytest-cov 输出格式: Name Stmts Miss Cover Missing
        lines = output.split("\n")
        file_coverage = []
        total_line = ""
        in_coverage = False
        for line in lines:
            if "Name" in line and "Stmts" in line and "Cover" in line:
                in_coverage = True
                continue
            if in_coverage and line.startswith("TOTAL"):
                total_line = line
                in_coverage = False
                continue
            if in_coverage and line.strip() and not line.startswith("-"):
                parts = line.split()
                if len(parts) >= 4:
                    file_coverage.append({
                        "file": parts[0],
                        "statements": parts[1],
                        "missing": parts[2],
                        "coverage": parts[3],
                    })

        if total_line:
            parts = total_line.split()
            if len(parts) >= 4:
                data["total_coverage"] = parts[3]
                data["total_statements"] = parts[1]
                data["total_missing"] = parts[2]

        data["files"] = file_coverage[:30]

    elif framework in ("vitest", "jest"):
        lines = output.split("\n")
        file_coverage = []
        for line in lines:
            if "%" in line and "|" in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 4:
                    file_coverage.append({
                        "file": parts[0],
                        "stmts": parts[1] if len(parts) > 1 else "",
                        "branch": parts[2] if len(parts) > 2 else "",
                        "funcs": parts[3] if len(parts) > 3 else "",
                        "lines": parts[4] if len(parts) > 4 else "",
                    })
            if "All files" in line and "%" in line:
                data["raw_summary"] = line.strip()

        data["files"] = file_coverage[:30]

    elif framework == "go":
        for line in output.split("\n"):
            if "coverage:" in line and "%" in line:
                data["raw_summary"] = line.strip()
                m = re.search(r'coverage:\s*([\d.]+)%', line)
                if m:
                    data["total_coverage"] = m.group(1) + "%"
                break

    return data


if __name__ == "__main__":
    mcp.run(transport="stdio")
