#!/usr/bin/env bash
set -euo pipefail

# ClaudeCursorX installer
# Installs MCP Servers, Skills, Rules, and Subagents into a target project's .cursor/ directory.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${1:-.}"
MODE="copy"

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS] [TARGET_PROJECT_DIR]

Install ClaudeCursorX into a Cursor project.

Arguments:
  TARGET_PROJECT_DIR   Target project directory (default: current directory)

Options:
  --link               Use symlinks instead of copying (auto-updates when toolkit changes)
  --copy               Copy files (default, standalone, no dependency on toolkit location)
  --mcp-only           Only install MCP Servers
  --skills-only        Only install Skills
  --rules-only         Only install Rules
  --agents-only        Only install Subagents
  --no-deps            Skip Python dependency installation
  -h, --help           Show this help message

Examples:
  ./install.sh ~/my-project              # Install everything into ~/my-project
  ./install.sh --link ~/my-project       # Symlink mode (keeps in sync)
  ./install.sh --mcp-only ~/my-project   # Only install MCP Servers
EOF
    exit 0
}

INSTALL_MCP=true
INSTALL_SKILLS=true
INSTALL_RULES=true
INSTALL_AGENTS=true
INSTALL_DEPS=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --link)    MODE="link"; shift ;;
        --copy)    MODE="copy"; shift ;;
        --mcp-only)
            INSTALL_SKILLS=false; INSTALL_RULES=false; INSTALL_AGENTS=false; shift ;;
        --skills-only)
            INSTALL_MCP=false; INSTALL_RULES=false; INSTALL_AGENTS=false; shift ;;
        --rules-only)
            INSTALL_MCP=false; INSTALL_SKILLS=false; INSTALL_AGENTS=false; shift ;;
        --agents-only)
            INSTALL_MCP=false; INSTALL_SKILLS=false; INSTALL_RULES=false; shift ;;
        --no-deps) INSTALL_DEPS=false; shift ;;
        -h|--help) usage ;;
        -*)        echo "Unknown option: $1"; usage ;;
        *)         TARGET_DIR="$1"; shift ;;
    esac
done

TARGET_DIR="$(cd "$TARGET_DIR" 2>/dev/null && pwd)" || {
    echo "Error: Target directory '$1' does not exist."
    exit 1
}

echo "========================================"
echo "  ClaudeCursorX installer"
echo "========================================"
echo "  Source:  $SCRIPT_DIR"
echo "  Target:  $TARGET_DIR"
echo "  Mode:    $MODE"
echo "========================================"
echo ""

install_item() {
    local src="$1"
    local dst="$2"

    mkdir -p "$(dirname "$dst")"

    if [[ "$MODE" == "link" ]]; then
        if [[ -e "$dst" || -L "$dst" ]]; then
            rm -rf "$dst"
        fi
        ln -s "$src" "$dst"
        echo "  LINK  $dst -> $src"
    else
        if [[ -d "$src" ]]; then
            cp -r "$src" "$dst"
        else
            cp "$src" "$dst"
        fi
        echo "  COPY  $dst"
    fi
}

install_dir() {
    local src_dir="$1"
    local dst_dir="$2"
    local label="$3"

    echo "Installing $label..."

    if [[ "$MODE" == "link" ]]; then
        for item in "$src_dir"/*/; do
            [[ -d "$item" ]] || continue
            local name=$(basename "$item")
            install_item "$item" "$dst_dir/$name"
        done
        for item in "$src_dir"/*; do
            [[ -f "$item" ]] || continue
            install_item "$item" "$dst_dir/$(basename "$item")"
        done
    else
        mkdir -p "$dst_dir"
        for item in "$src_dir"/*; do
            local name=$(basename "$item")
            install_item "$item" "$dst_dir/$name"
        done
    fi
    echo ""
}

# --- MCP Servers ---
if [[ "$INSTALL_MCP" == true ]]; then
    echo "Installing MCP Servers..."
    for server_dir in "$SCRIPT_DIR"/mcp-servers/*/; do
        [[ -d "$server_dir" ]] || continue
        server_name=$(basename "$server_dir")
        dst_server="$TARGET_DIR/.cursor/mcp-servers/$server_name"
        mkdir -p "$dst_server"
        for f in "$server_dir"*.py; do
            [[ -f "$f" ]] || continue
            install_item "$f" "$dst_server/$(basename "$f")"
        done
    done

    # Generate mcp.json
    MCP_JSON="$TARGET_DIR/.cursor/mcp.json"
    if [[ -f "$MCP_JSON" ]]; then
        echo "  SKIP  $MCP_JSON (already exists, not overwriting)"
        echo "        Compare with: $SCRIPT_DIR/templates/mcp.json"
    else
        cp "$SCRIPT_DIR/templates/mcp.json" "$MCP_JSON"
        echo "  COPY  $MCP_JSON"
    fi
    echo ""
fi

# --- Skills ---
if [[ "$INSTALL_SKILLS" == true ]]; then
    install_dir "$SCRIPT_DIR/skills" "$TARGET_DIR/.cursor/skills" "Skills"
fi

# --- Rules ---
if [[ "$INSTALL_RULES" == true ]]; then
    echo "Installing Rules..."
    mkdir -p "$TARGET_DIR/.cursor/rules"
    for f in "$SCRIPT_DIR"/rules/*.mdc; do
        [[ -f "$f" ]] || continue
        install_item "$f" "$TARGET_DIR/.cursor/rules/$(basename "$f")"
    done
    echo ""
fi

# --- Agents ---
if [[ "$INSTALL_AGENTS" == true ]]; then
    echo "Installing Subagents..."
    mkdir -p "$TARGET_DIR/.cursor/agents"
    for f in "$SCRIPT_DIR"/agents/*.md; do
        [[ -f "$f" ]] || continue
        install_item "$f" "$TARGET_DIR/.cursor/agents/$(basename "$f")"
    done
    echo ""
fi

# --- Python Dependencies ---
if [[ "$INSTALL_DEPS" == true && "$INSTALL_MCP" == true ]]; then
    echo "Installing Python dependencies..."
    if command -v pip &>/dev/null; then
        pip install -r "$SCRIPT_DIR/requirements.txt" --quiet
        echo "  Done."
    elif command -v pip3 &>/dev/null; then
        pip3 install -r "$SCRIPT_DIR/requirements.txt" --quiet
        echo "  Done."
    else
        echo "  WARNING: pip not found. Please install dependencies manually:"
        echo "           pip install -r $SCRIPT_DIR/requirements.txt"
    fi
    echo ""
fi

echo "========================================"
echo "  Installation complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo "  1. Open your project in Cursor"
echo "  2. The MCP Servers will auto-start based on .cursor/mcp.json"
echo "  3. Skills, Rules, and Subagents are active immediately"
echo ""
echo "To verify MCP tools are available, ask Cursor:"
echo '  "List all available MCP tools"'
echo ""
