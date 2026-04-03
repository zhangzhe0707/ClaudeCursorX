"""
Code Intelligence MCP Server — 代码深度分析工具集。

对应 Claude Code 的 Agent 代码分析能力，将需要多轮 Grep + Read + 推理的
"数据收集"部分预计算好，Agent 只需一次调用即可获取完整分析。

工具列表：
1. analyze_impact    — 分析修改文件的影响范围（反向依赖追踪）
2. module_summary    — 生成模块摘要：公共 API、类型、依赖
3. find_test_coverage — 找到覆盖指定文件的所有测试
4. symbol_references — 追踪符号的所有引用链
5. dependency_graph  — 多语言依赖图分析
6. project_overview  — 项目全景扫描（技术栈、入口点、架构层）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("请先安装依赖: pip install mcp fastmcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("code-intel")

IGNORE_DIRS = {
    "node_modules", ".git", "dist", "build", "__pycache__",
    ".next", ".nuxt", "target", "vendor", ".venv", "venv",
    "coverage", ".tox", ".mypy_cache", ".pytest_cache",
    ".cache", ".turbo", ".parcel-cache", "out",
}

SOURCE_EXTS = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".go", ".rs", ".java", ".kt", ".rb",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift",
    ".vue", ".svelte",
}

IMPORT_PATTERNS = {
    "typescript": [
        re.compile(r'''import\s+.*?\s+from\s+['"]([^'"]+)['"]'''),
        re.compile(r'''import\s*\(\s*['"]([^'"]+)['"]\s*\)'''),
        re.compile(r'''require\s*\(\s*['"]([^'"]+)['"]\s*\)'''),
        re.compile(r'''export\s+.*?\s+from\s+['"]([^'"]+)['"]'''),
    ],
    "python": [
        re.compile(r'^import\s+([\w.]+)', re.MULTILINE),
        re.compile(r'^from\s+([\w.]+)\s+import', re.MULTILINE),
    ],
    "go": [
        re.compile(r'"([^"]+)"'),
    ],
    "rust": [
        re.compile(r'use\s+([\w:]+)'),
    ],
}

EXPORT_PATTERNS = {
    "typescript": [
        re.compile(r'export\s+(?:default\s+)?(?:function|class|const|let|var|type|interface|enum)\s+(\w+)', re.MULTILINE),
        re.compile(r'export\s*\{([^}]+)\}', re.MULTILINE),
    ],
    "python": [
        re.compile(r'^(?:def|class)\s+(\w+)', re.MULTILINE),
        re.compile(r'^(\w+)\s*=', re.MULTILINE),
    ],
}


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


def _safe_read(path: Path, max_bytes: int = 512_000) -> str:
    try:
        if path.stat().st_size > max_bytes:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _extract_imports(path: Path) -> list[str]:
    content = _safe_read(path)
    if not content:
        return []
    lang = _detect_lang(path)
    patterns = IMPORT_PATTERNS.get(lang, IMPORT_PATTERNS["typescript"])
    imports = []
    for p in patterns:
        imports.extend(p.findall(content))
    return imports


def _resolve_import(imp: str, from_file: Path) -> Path | None:
    """尝试将相对 import 路径解析为实际文件。"""
    if not imp.startswith("."):
        return None
    base = from_file.parent / imp
    for ext in ["", ".ts", ".tsx", ".js", ".jsx", ".py", "/index.ts", "/index.js", "/index.tsx"]:
        candidate = Path(str(base) + ext)
        if candidate.exists():
            return candidate.resolve()
    return None


def _collect_source_files(root: Path, max_files: int = 5000) -> list[Path]:
    """递归收集所有源代码文件。"""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")]
        for f in filenames:
            p = Path(dirpath) / f
            if p.suffix.lower() in SOURCE_EXTS:
                files.append(p)
                if len(files) >= max_files:
                    return files
    return files


def _rg_search(pattern: str, root: str, extra_args: list[str] | None = None) -> str:
    """调用 ripgrep 进行高效搜索。"""
    cmd = ["rg", "--no-heading", "--line-number", "--max-count", "200"]
    if extra_args:
        cmd.extend(extra_args)
    cmd.extend([pattern, root])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout[:50_000]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


@mcp.tool()
def analyze_impact(file_path: str, project_dir: str = ".") -> str:
    """分析修改一个文件会影响到哪些其他文件（反向依赖追踪）。

    帮助在修改代码前了解"爆炸半径"——谁依赖了这个文件，改了可能会破坏什么。

    Args:
        file_path: 要分析的文件路径
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    target = Path(file_path).expanduser().resolve()
    if not target.exists():
        return json.dumps({"error": f"文件不存在: {file_path}"})

    target_stem = target.stem
    target_rel = str(target.relative_to(proj)) if target.is_relative_to(proj) else target.name
    target_no_ext = target_rel.rsplit(".", 1)[0] if "." in target_rel else target_rel

    search_patterns = [target_stem]
    if "/" in target_no_ext:
        search_patterns.append(target_no_ext)
    dotslash = "./" + target_no_ext.replace("\\", "/")
    search_patterns.append(dotslash.split("/")[-1])

    direct_importers: dict[str, list[str]] = {}
    source_files = _collect_source_files(proj)

    for sf in source_files:
        if sf.resolve() == target:
            continue
        imports = _extract_imports(sf)
        for imp in imports:
            resolved = _resolve_import(imp, sf)
            if resolved and resolved == target:
                rel = str(sf.relative_to(proj)) if sf.is_relative_to(proj) else str(sf)
                direct_importers[rel] = direct_importers.get(rel, [])
                direct_importers[rel].append(imp)
                break
            if any(p in imp for p in search_patterns):
                rel = str(sf.relative_to(proj)) if sf.is_relative_to(proj) else str(sf)
                direct_importers[rel] = direct_importers.get(rel, [])
                direct_importers[rel].append(imp)
                break

    # 按目录分组
    by_dir: dict[str, list[str]] = defaultdict(list)
    for f in direct_importers:
        d = str(Path(f).parent)
        by_dir[d].append(Path(f).name)

    # 识别测试文件
    test_files = [f for f in direct_importers if any(k in f.lower() for k in ["test", "spec", "__tests__"])]
    source_importers = [f for f in direct_importers if f not in test_files]

    return json.dumps({
        "target": target_rel,
        "impact_summary": {
            "total_importers": len(direct_importers),
            "source_files": len(source_importers),
            "test_files": len(test_files),
        },
        "risk_level": (
            "low" if len(source_importers) <= 2
            else "medium" if len(source_importers) <= 10
            else "high"
        ),
        "source_importers": source_importers[:30],
        "test_importers": test_files[:20],
        "by_directory": {k: v for k, v in sorted(by_dir.items())[:20]},
        "recommendation": (
            "改动影响范围小，可以直接修改" if len(source_importers) <= 2
            else "中等影响，建议同时检查下游文件" if len(source_importers) <= 10
            else "高影响范围！建议谨慎修改，先跑相关测试"
        ),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def module_summary(directory: str, project_dir: str = ".") -> str:
    """生成一个目录/模块的完整摘要：公共 API、类型定义、依赖关系、文件概览。

    用一次调用取代多次 Read + Grep，快速了解一个模块做了什么。

    Args:
        directory: 要分析的目录路径
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    target_dir = Path(directory).expanduser().resolve()
    if not target_dir.is_dir():
        return json.dumps({"error": f"目录不存在: {directory}"})

    files: list[dict] = []
    exports: list[dict] = []
    all_imports: list[str] = []
    internal_imports: list[str] = []
    external_imports: set[str] = set()
    total_lines = 0

    for f in sorted(target_dir.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in SOURCE_EXTS:
            continue
        rel_parts = f.relative_to(target_dir).parts
        if any(p in IGNORE_DIRS for p in rel_parts):
            continue

        content = _safe_read(f)
        if not content:
            continue

        lines = content.count("\n") + 1
        total_lines += lines
        rel = str(f.relative_to(target_dir))
        lang = _detect_lang(f)

        files.append({
            "path": rel,
            "lines": lines,
            "language": lang,
        })

        # 提取导出
        for pattern in EXPORT_PATTERNS.get(lang, []):
            for match in pattern.finditer(content):
                text = match.group(1)
                for name in re.split(r'[,\s]+', text):
                    name = name.strip().rstrip(",")
                    if name and name[0].isalpha():
                        exports.append({"name": name, "file": rel})

        # 提取 import
        for imp in _extract_imports(f):
            all_imports.append(imp)
            if imp.startswith("."):
                internal_imports.append(imp)
            else:
                pkg = imp.split("/")[0].lstrip("@")
                if imp.startswith("@"):
                    pkg = imp.split("/")[0] + "/" + (imp.split("/")[1] if len(imp.split("/")) > 1 else "")
                external_imports.add(pkg)

    # 识别入口文件
    index_files = [f for f in files if "index" in f["path"].lower()]

    return json.dumps({
        "directory": str(target_dir.relative_to(proj)) if target_dir.is_relative_to(proj) else str(target_dir),
        "overview": {
            "total_files": len(files),
            "total_lines": total_lines,
            "index_files": [f["path"] for f in index_files],
        },
        "files": files[:50],
        "public_api": exports[:50],
        "dependencies": {
            "external": sorted(external_imports)[:30],
            "internal_import_count": len(internal_imports),
        },
        "truncated": len(files) > 50,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def find_test_coverage(file_path: str, project_dir: str = ".") -> str:
    """找到覆盖指定源文件的所有测试文件和测试用例。

    Args:
        file_path: 源文件路径
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    src = Path(file_path).expanduser().resolve()
    if not src.exists():
        return json.dumps({"error": f"文件不存在: {file_path}"})

    stem = src.stem
    ext = src.suffix
    rel = str(src.relative_to(proj)) if src.is_relative_to(proj) else str(src)

    # 推断可能的测试文件名模式
    candidates = [
        f"{stem}.test{ext}",
        f"{stem}.spec{ext}",
        f"test_{stem}{ext}",
        f"{stem}_test{ext}",
        f"test_{stem}.py",
        f"{stem}_test.go",
    ]

    found_tests: list[dict] = []

    # 方法 1: 文件名匹配
    for tf in _collect_source_files(proj):
        if tf.name in candidates:
            test_rel = str(tf.relative_to(proj)) if tf.is_relative_to(proj) else str(tf)
            content = _safe_read(tf)
            test_names = _extract_test_names(content, _detect_lang(tf))
            found_tests.append({
                "file": test_rel,
                "test_count": len(test_names),
                "test_names": test_names[:20],
                "match_type": "filename_pattern",
            })

    # 方法 2: ripgrep 搜索 import 引用
    rg_result = _rg_search(stem, str(proj),
        ["--glob", f"*test*", "--glob", f"*spec*", "--glob", f"*Test*", "-l"])
    if rg_result:
        for line in rg_result.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                test_path = Path(line)
                test_rel = str(test_path.relative_to(proj)) if test_path.is_relative_to(proj) else line
            except ValueError:
                test_rel = line
            if test_rel not in [t["file"] for t in found_tests]:
                found_tests.append({
                    "file": test_rel,
                    "match_type": "import_reference",
                })

    return json.dumps({
        "source_file": rel,
        "tests_found": len(found_tests),
        "coverage_status": (
            "well-tested" if len(found_tests) >= 2
            else "has-tests" if len(found_tests) == 1
            else "no-tests-found"
        ),
        "tests": found_tests[:20],
        "recommendation": (
            "测试覆盖充分" if len(found_tests) >= 2
            else "有基础测试覆盖" if len(found_tests) == 1
            else f"未找到测试文件。建议创建 {candidates[0]}"
        ),
    }, ensure_ascii=False, indent=2)


def _extract_test_names(content: str, lang: str) -> list[str]:
    """从测试文件内容中提取测试名称。"""
    tests = []
    patterns = [
        re.compile(r'''(?:it|test|describe)\s*\(\s*['"]([^'"]+)['"]'''),
        re.compile(r'''def\s+(test_\w+)'''),
        re.compile(r'''func\s+(Test\w+)'''),
        re.compile(r'''#\[test\]\s*fn\s+(\w+)''', re.DOTALL),
    ]
    for p in patterns:
        tests.extend(p.findall(content))
    return tests


@mcp.tool()
def symbol_references(symbol: str, project_dir: str = ".", file_types: str = "") -> str:
    """追踪一个符号（函数名/类名/变量名）在项目中的所有引用。

    返回定义位置、使用位置、测试位置，帮助理解符号的使用全景。

    Args:
        symbol: 要追踪的符号名称
        project_dir: 项目根目录
        file_types: 限制搜索的文件类型（如 "ts,tsx"），留空搜索所有
    """
    proj = Path(project_dir).expanduser().resolve()

    extra_args = []
    if file_types:
        for ft in file_types.split(","):
            ft = ft.strip().lstrip(".")
            extra_args.extend(["--glob", f"*.{ft}"])

    rg_output = _rg_search(rf'\b{re.escape(symbol)}\b', str(proj), extra_args)
    if not rg_output:
        return json.dumps({
            "symbol": symbol,
            "total_references": 0,
            "message": f"未找到符号 '{symbol}' 的引用",
        })

    definitions: list[dict] = []
    usages: list[dict] = []
    test_refs: list[dict] = []

    # 定义模式
    def_patterns = [
        re.compile(rf'(?:export\s+)?(?:function|class|const|let|var|type|interface|enum)\s+{re.escape(symbol)}\b'),
        re.compile(rf'(?:def|class)\s+{re.escape(symbol)}\b'),
        re.compile(rf'func\s+{re.escape(symbol)}\b'),
        re.compile(rf'fn\s+{re.escape(symbol)}\b'),
    ]

    for line in rg_output.strip().split("\n")[:200]:
        match = re.match(r'^(.+?):(\d+):(.*)$', line)
        if not match:
            continue
        fpath, lineno, content = match.group(1), match.group(2), match.group(3).strip()

        try:
            rel = str(Path(fpath).relative_to(proj))
        except ValueError:
            rel = fpath

        is_test = any(k in rel.lower() for k in ["test", "spec", "__tests__"])
        is_def = any(p.search(content) for p in def_patterns)

        entry = {"file": rel, "line": int(lineno), "content": content[:120]}

        if is_def:
            definitions.append(entry)
        elif is_test:
            test_refs.append(entry)
        else:
            usages.append(entry)

    return json.dumps({
        "symbol": symbol,
        "total_references": len(definitions) + len(usages) + len(test_refs),
        "definitions": definitions[:10],
        "usages": usages[:30],
        "test_references": test_refs[:15],
        "summary": (
            f"定义: {len(definitions)} 处, "
            f"使用: {len(usages)} 处, "
            f"测试: {len(test_refs)} 处"
        ),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def dependency_graph(file_path: str, project_dir: str = ".", max_depth: int = 3) -> str:
    """分析文件的 import 依赖关系，返回依赖树和统计。

    比 agent-tools 中的版本增强：支持更多语言、更深追踪、循环检测。

    Args:
        file_path: 入口文件路径
        project_dir: 项目根目录
        max_depth: 最大追踪深度
    """
    proj = Path(project_dir).expanduser().resolve()
    root = Path(file_path).expanduser().resolve()
    if not root.exists():
        return json.dumps({"error": f"文件不存在: {file_path}"})

    visited: set[str] = set()
    graph: dict[str, dict] = {}
    circular: list[tuple[str, str]] = []

    def walk(path: Path, depth: int, chain: set[str]):
        key = str(path)
        if depth > max_depth:
            return
        if key in visited:
            return
        visited.add(key)

        imports = _extract_imports(path)
        deps: list[dict] = []

        for imp in imports:
            resolved = _resolve_import(imp, path)
            if resolved:
                rkey = str(resolved)
                if rkey in chain:
                    circular.append((key, rkey))
                    deps.append({"module": imp, "resolved": rkey, "circular": True})
                else:
                    deps.append({"module": imp, "resolved": rkey})
                    walk(resolved, depth + 1, chain | {key})
            else:
                deps.append({"module": imp, "external": True})

        try:
            rel = str(path.relative_to(proj))
        except ValueError:
            rel = key

        graph[rel] = {
            "imports": len(deps),
            "external": sum(1 for d in deps if d.get("external")),
            "internal": sum(1 for d in deps if not d.get("external")),
            "deps": deps[:30],
        }

    walk(root, 0, set())

    return json.dumps({
        "entry": str(root.relative_to(proj)) if root.is_relative_to(proj) else str(root),
        "total_files_analyzed": len(graph),
        "circular_dependencies": [
            {"from": c[0], "to": c[1]} for c in circular[:10]
        ],
        "graph": graph,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
def project_overview(project_dir: str = ".") -> str:
    """项目全景扫描：自动检测技术栈、入口点、架构层、代码量。

    一次调用了解项目全貌，替代手动浏览目录 + 读配置文件。

    Args:
        project_dir: 项目根目录
    """
    proj = Path(project_dir).expanduser().resolve()
    if not proj.is_dir():
        return json.dumps({"error": f"目录不存在: {project_dir}"})

    result: dict = {
        "root": str(proj),
        "tech_stack": [],
        "entry_points": [],
        "architecture": {},
        "code_stats": {},
        "config_files": [],
    }

    # 技术栈检测
    stack_markers = {
        "package.json": "Node.js",
        "tsconfig.json": "TypeScript",
        "pyproject.toml": "Python",
        "setup.py": "Python",
        "requirements.txt": "Python",
        "go.mod": "Go",
        "Cargo.toml": "Rust",
        "pom.xml": "Java/Maven",
        "build.gradle": "Java/Gradle",
        "Gemfile": "Ruby",
        "Dockerfile": "Docker",
        "docker-compose.yml": "Docker Compose",
        "docker-compose.yaml": "Docker Compose",
        ".github/workflows": "GitHub Actions",
        "next.config.js": "Next.js",
        "next.config.ts": "Next.js",
        "vite.config.ts": "Vite",
        "vite.config.js": "Vite",
        "nuxt.config.ts": "Nuxt",
        "tailwind.config.js": "Tailwind CSS",
        "tailwind.config.ts": "Tailwind CSS",
        "prisma/schema.prisma": "Prisma",
        ".env": "dotenv",
    }
    for marker, tech in stack_markers.items():
        if (proj / marker).exists():
            result["tech_stack"].append(tech)

    # 检查 package.json 获取更多信息
    pkg_path = proj / "package.json"
    if pkg_path.exists():
        try:
            pkg = json.loads(pkg_path.read_text())
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            frameworks = {
                "react": "React", "vue": "Vue", "svelte": "Svelte",
                "express": "Express", "fastify": "Fastify", "koa": "Koa",
                "vitest": "Vitest", "jest": "Jest", "mocha": "Mocha",
            }
            for dep, name in frameworks.items():
                if dep in deps:
                    result["tech_stack"].append(name)

            if "main" in pkg:
                result["entry_points"].append({"type": "package.main", "path": pkg["main"]})
            if "scripts" in pkg:
                result["scripts"] = {k: v for k, v in list(pkg["scripts"].items())[:15]}
        except Exception:
            pass

    # 入口点检测
    common_entries = [
        "src/main.ts", "src/main.tsx", "src/index.ts", "src/index.tsx",
        "src/app.ts", "src/app.tsx", "src/server.ts",
        "main.py", "app.py", "manage.py", "src/main.py",
        "main.go", "cmd/main.go",
        "src/main.rs", "src/lib.rs",
    ]
    for entry in common_entries:
        if (proj / entry).exists():
            result["entry_points"].append({"type": "source_entry", "path": entry})

    # 代码统计
    lang_stats: dict[str, dict] = defaultdict(lambda: {"files": 0, "lines": 0})
    for f in _collect_source_files(proj):
        ext = f.suffix.lower()
        lang_stats[ext]["files"] += 1
        try:
            lang_stats[ext]["lines"] += sum(1 for _ in f.open("r", encoding="utf-8", errors="replace"))
        except Exception:
            pass

    result["code_stats"] = {
        ext: stats for ext, stats in sorted(lang_stats.items(), key=lambda x: -x[1]["lines"])[:10]
    }
    result["total_lines"] = sum(s["lines"] for s in lang_stats.values())
    result["total_files"] = sum(s["files"] for s in lang_stats.values())

    # 架构层检测（顶层目录分类）
    arch_hints = {
        "src": "source", "lib": "library", "pkg": "packages",
        "api": "api", "routes": "routing", "controllers": "controllers",
        "models": "models", "services": "services", "utils": "utilities",
        "hooks": "hooks", "components": "components", "pages": "pages",
        "views": "views", "middleware": "middleware", "config": "config",
        "tests": "tests", "test": "tests", "__tests__": "tests",
        "scripts": "scripts", "tools": "tools", "docs": "documentation",
        "public": "static_assets", "assets": "assets", "styles": "styles",
        "types": "type_definitions", "interfaces": "interfaces",
    }
    for d in sorted(proj.iterdir()):
        if d.is_dir() and d.name in arch_hints:
            file_count = sum(1 for _ in d.rglob("*") if _.is_file() and _.suffix in SOURCE_EXTS)
            if file_count > 0:
                result["architecture"][d.name] = {
                    "role": arch_hints[d.name],
                    "source_files": file_count,
                }

    # 配置文件列表
    config_patterns = {"*.json", "*.yaml", "*.yml", "*.toml", "*.ini", "*.cfg", "*.conf"}
    for f in sorted(proj.iterdir()):
        if f.is_file() and any(f.name.endswith(p.lstrip("*")) for p in config_patterns):
            result["config_files"].append(f.name)
    result["config_files"] = result["config_files"][:20]

    return json.dumps(result, ensure_ascii=False, indent=2)


# ─── 记忆系统 ────────────────────────────────────────────
# 从 memory.py 模块导入记忆功能并注册为 MCP 工具

from pathlib import Path as _Path
_memory_module_path = _Path(__file__).parent / "memory.py"
if _memory_module_path.exists():
    from memory import memory_save as _memory_save
    from memory import memory_search as _memory_search
    from memory import memory_read as _memory_read

    @mcp.tool()
    def memory_save(key: str, content: str, category: str = "decision", project_dir: str = ".") -> str:
        """保存记忆/决策到项目持久化文件 (.cursor/memory.md)。

        记忆可跨会话存在，新会话开始时用 memory_read 恢复上下文。

        Args:
            key: 记忆标题（简短）
            content: 记忆内容（详细描述）
            category: 分类 — decision(决策), pattern(模式), convention(约定), bug(已知问题), context(上下文)
            project_dir: 项目根目录
        """
        return _memory_save(key, content, category, project_dir)

    @mcp.tool()
    def memory_search(query: str = "", category: str = "", project_dir: str = ".") -> str:
        """搜索项目记忆库。

        Args:
            query: 搜索关键词
            category: 按分类过滤（decision/pattern/convention/bug/context）
            project_dir: 项目根目录
        """
        return _memory_search(query, category, project_dir)

    @mcp.tool()
    def memory_read(project_dir: str = ".") -> str:
        """读取完整的项目记忆文件，用于新会话开始时恢复上下文。

        Args:
            project_dir: 项目根目录
        """
        return _memory_read(project_dir)

    # ─── 团队记忆工具 ──────────────────────────────────────
    from memory import team_memory_sync as _team_memory_sync
    from memory import team_memory_save as _team_memory_save

    @mcp.tool()
    def team_memory_sync(action: str = "status", project_dir: str = ".") -> str:
        """团队记忆同步：通过 Git 在团队成员间共享项目知识。

        利用 .cursor/team-memory/ 目录 + Git 实现团队记忆的 pull/push 同步，
        替代 Claude Code 的服务端 API 同步方案。

        Args:
            action: 操作 — "status"(状态), "pull"(拉取远程), "push"(推送到远程), "list"(列出所有)
            project_dir: 项目根目录
        """
        return _team_memory_sync(action, project_dir)

    @mcp.tool()
    def team_memory_save(key: str, content: str, category: str = "convention", project_dir: str = ".") -> str:
        """保存一条团队共享记忆（通过 Git 同步给所有成员）。

        记忆保存在 .cursor/team-memory/，提交 Git 后团队成员可通过 pull 获取。
        内置 Secret 扫描，阻止密钥泄露。

        Args:
            key: 记忆标题
            content: 记忆内容
            category: 分类 — convention(团队约定), architecture(架构决策), api(API契约), workflow(工作流), gotcha(陷阱)
            project_dir: 项目根目录
        """
        return _team_memory_save(key, content, category, project_dir)


if __name__ == "__main__":
    mcp.run(transport="stdio")
