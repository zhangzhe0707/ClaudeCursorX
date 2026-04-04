# Plugin Development — 插件开发技能

> 借鉴 claude-code-rust 的 Plugin trait + PluginSandbox + MessageBus + DependencyResolver 模型

Use this skill when the user wants to create a new MCP server, Cursor Skill, Rule, or Agent definition for the ClaudeCursorX project.

## Plugin Architecture (from claude-code-rust)

### Component Model

```
Plugin = Metadata + State + Lifecycle + Capabilities

Metadata:
  name, version, author, description, dependencies, capabilities

State Machine:
  Unloaded → Loading → Loaded → Running → Unloading → Unloaded
                                    ↓
                                  Error

Capabilities (whitelist):
  FILE_READ, FILE_WRITE, NETWORK, COMMAND_EXEC, 
  STORAGE, SYSTEM_INFO, CONFIG_ACCESS
```

### For ClaudeCursorX, this maps to:

| claude-code-rust | ClaudeCursorX Equivalent |
|-----------------|--------------------------|
| Plugin (dynamic .so) | MCP Server (Python) |
| Plugin Capability | MCP Tool permission scope |
| Plugin Hook | Rule (alwaysApply) |
| Plugin Message | MCP Prompt |
| Plugin Sandbox | sandbox_check tool |

## Creating a New MCP Server

1. **Structure**: Create `mcp-servers/<name>/server.py`
2. **Template**:

```python
"""
<Name> MCP Server — <one-line description>.
"""
from __future__ import annotations
import json, logging, os, sys
logging.basicConfig(level=logging.WARNING)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("请先安装依赖: pip install mcp fastmcp", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("<name>")

@mcp.tool()
def my_tool(param: str, language: str = "zh") -> str:
    """Tool description. / 工具描述。"""
    is_zh = language.strip().lower() not in ("en", "english")
    # implementation
    return json.dumps({"result": "..."}, ensure_ascii=False)

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

3. **Register** in `.cursor/mcp.json`:
```json
{
  "<name>": {
    "command": "python",
    "args": [".cursor/mcp-servers/<name>/server.py"]
  }
}
```

## Creating a New Skill

1. **Structure**: Create `skills/<name>/SKILL.md`
2. **Frontmatter**: None needed (Cursor auto-detects)
3. **Content**: Clear trigger conditions, step-by-step instructions, examples

## Creating a New Rule

1. **Structure**: Create `rules/<name>.mdc`
2. **Frontmatter**: `description`, `globs` (optional), `alwaysApply`
3. **Content**: Imperative instructions for the AI

## Creating a New Agent

1. **Structure**: Create `agents/<name>.md`
2. **Frontmatter**: `name`, `description` (trigger conditions)
3. **Content**: Role prompt, when invoked, output format, rules

## Quality Checklist

- [ ] Bilingual support (language parameter for MCP tools)
- [ ] Error handling (try/except for all external calls)
- [ ] JSON output format (consistent structure)
- [ ] Logging (use logging.WARNING to avoid Cursor stderr issues)
- [ ] Documentation (docstring with Args section)
