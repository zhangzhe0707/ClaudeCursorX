#!/usr/bin/env bash
set -euo pipefail

# ClaudeCursorX installer
# Installs MCP Servers, Skills, Rules, and Subagents into USER-level ~/.cursor/ directory.
# This ensures global availability across all Cursor projects.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Default: install to user-level ~/.cursor (global installation)
USER_CURSOR_DIR="$HOME/.cursor"
TARGET_DIR="$USER_CURSOR_DIR"
MODE="copy"
FORCE_OVERWRITE=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Install ClaudeCursorX to USER-GLOBAL Cursor directory (~/.cursor/).
All components are available globally for every Cursor project.

Options:
  --link               Use symlinks instead of copying (auto-updates when toolkit changes)
  --copy               Copy files (default, standalone, no dependency on toolkit location)
  --mcp-only           Only install MCP Servers
  --skills-only        Only install Skills (Agent Skills)
  --rules-only         Only install Rules (.mdc rules)
  --agents-only        Only install Subagents
  --no-deps            Skip Python dependency installation
  --force, -f          Force overwrite existing files
  -h, --help           Show this help message

Examples:
  ./install.sh                         (install everything to ~/.cursor/)
  ./install.sh --mcp-only              (only install MCP Servers)
  ./install.sh --link --force          (use symlinks, overwrite existing)
  ./install.sh --copy --no-deps        (copy files, skip dependency install)
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
        --force|-f) FORCE_OVERWRITE=true; shift ;;
        -h|--help) usage ;;
        -*)        echo "Unknown option: $1"; usage ;;
        *)         TARGET_DIR="$1"; shift ;;
    esac
done

# Validate source directory exists
if [[ ! -d "$SCRIPT_DIR/mcp-servers" ]]; then
    echo "Error: Source directory mcp-servers not found in: $SCRIPT_DIR"
    echo "       Make sure you're running install.sh from the ClaudeCursorX root directory."
    exit 1
fi

# Ensure target directory is created if it doesn't exist
mkdir -p "$TARGET_DIR"

echo "========================================"
echo "  ClaudeCursorX Installer"
echo "========================================"
echo "  Source:  $SCRIPT_DIR"
echo "  Target:  $TARGET_DIR  (USER-GLOBAL)"
echo "  Mode:    $MODE"
echo "  Force:   $FORCE_OVERWRITE"
echo "========================================"
echo ""

install_item() {
    local src="$1"
    local dst="$2"

    # Skip if source doesn't exist
    if [[ ! -e "$src" ]]; then
        echo "  SKIP  $dst (source not found: $src)"
        return
    fi

    mkdir -p "$(dirname "$dst")"

    # Skip if already exists and not force
    if [[ -e "$dst" && "$FORCE_OVERWRITE" == false ]]; then
        echo "  SKIP  $dst (already exists, use --force to overwrite)"
        return
    fi

    if [[ "$MODE" == "link" ]]; then
        if [[ -e "$dst" || -L "$dst" ]]; then
            rm -rf "$dst"
        fi
        ln -s "$src" "$dst"
        echo "  LINK  $dst -> $src"
    else
        if [[ -d "$src" ]]; then
            mkdir -p "$dst"
            cp -r "$src"/. "$dst"/
        else
            cp "$src" "$dst"
        fi
        echo "  COPY  $dst"
    fi
}

install_dir_recursive() {
    local src_dir="$1"
    local dst_base="$2"

    if [[ ! -d "$src_dir" ]]; then
        return
    fi

    # Process files in current directory
    for item in "$src_dir"/*; do
        if [[ -f "$item" ]]; then
            local name=$(basename "$item")
            install_item "$item" "$dst_base/$name"
        elif [[ -d "$item" ]]; then
            local name=$(basename "$item")
            install_dir_recursive "$item" "$dst_base/$name"
        fi
    done
}

install_dir_flat() {
    local src_dir="$1"
    local dst_dir="$2"
    local label="$3"

    echo "Installing $label..."

    mkdir -p "$dst_dir"

    for item in "$src_dir"/*; do
        if [[ -d "$item" ]]; then
            local name=$(basename "$item")
            install_item "$item" "$dst_dir/$name"
        elif [[ -f "$item" ]]; then
            local name=$(basename "$item")
            install_item "$item" "$dst_dir/$name"
        fi
    done

    echo ""
}

# --- MCP Servers ---
if [[ "$INSTALL_MCP" == true ]]; then
    echo "[1/4] Installing MCP Servers..."
    for server_dir in "$SCRIPT_DIR"/mcp-servers/*/; do
        [[ -d "$server_dir" ]] || continue
        server_name=$(basename "$server_dir")
        dst_server="$TARGET_DIR/mcp-servers/$server_name"
        install_dir_recursive "$server_dir" "$dst_server"
    done

    # Install mcp.json template
    MCP_JSON="$TARGET_DIR/mcp.json"
    MCP_JSON_TPL="$SCRIPT_DIR/templates/mcp.json"
    if [[ -f "$MCP_JSON_TPL" ]]; then
        install_item "$MCP_JSON_TPL" "$MCP_JSON"
    fi
    echo ""
fi

# --- Skills ---
if [[ "$INSTALL_SKILLS" == true ]]; then
    echo "[2/4] Installing Agent Skills..."
    install_dir_flat "$SCRIPT_DIR/skills" "$TARGET_DIR/skills" "Skills"
fi

# --- Rules ---
if [[ "$INSTALL_RULES" == true ]]; then
    echo "[3/4] Installing Cursor Rules (.mdc)..."
    mkdir -p "$TARGET_DIR/rules"
    # Install from root rules directory
    for f in "$SCRIPT_DIR"/rules/*.mdc; do
        [[ -f "$f" ]] || continue
        install_item "$f" "$TARGET_DIR/rules/$(basename "$f")"
    done
    # Also install from subdirectories
    for dir in "$SCRIPT_DIR"/rules/*/; do
        [[ -d "$dir" ]] || continue
        for f in "$dir"*.mdc; do
            [[ -f "$f" ]] || continue
            install_item "$f" "$TARGET_DIR/rules/$(basename "$f")"
        done
    done
    echo ""
fi

# --- Agents ---
if [[ "$INSTALL_AGENTS" == true ]]; then
    echo "[4/4] Installing Subagents..."
    mkdir -p "$TARGET_DIR/agents"
    for f in "$SCRIPT_DIR"/agents/*.md; do
        [[ -f "$f" ]] || continue
        install_item "$f" "$TARGET_DIR/agents/$(basename "$f")"
    done
    for f in "$SCRIPT_DIR"/agents/*.mdc; do
        [[ -f "$f" ]] || continue
        install_item "$f" "$TARGET_DIR/agents/$(basename "$f")"
    done
    echo ""
fi

# --- Python Dependencies ---
if [[ "$INSTALL_DEPS" == true && "$INSTALL_MCP" == true ]]; then
    echo "Installing Python dependencies..."
    REQUIREMENTS="$SCRIPT_DIR/requirements.txt"
    if [[ -f "$REQUIREMENTS" ]]; then
        if command -v pip &>/dev/null; then
            pip install -r "$REQUIREMENTS"
            echo "  Done."
        elif command -v pip3 &>/dev/null; then
            pip3 install -r "$REQUIREMENTS"
            echo "  Done."
        else
            echo "  WARNING: pip/pip3 not found in PATH."
            echo "           Please install dependencies manually:"
            echo "           pip install -r $REQUIREMENTS"
        fi
    else
        echo "  WARNING: requirements.txt not found, skipping."
    fi
    echo ""
fi

echo "========================================"
echo "  Installation Complete!"
echo "========================================"
echo ""
echo "Install location: $TARGET_DIR"
echo ""
echo "What was installed:"
[[ "$INSTALL_MCP" == true ]]    && echo "  ✅ MCP Servers"
[[ "$INSTALL_SKILLS" == true ]]  && echo "  ✅ Agent Skills"
[[ "$INSTALL_RULES" == true ]]   && echo "  ✅ Cursor Rules"
[[ "$INSTALL_AGENTS" == true ]]  && echo "  ✅ Subagents"
echo ""
echo "Next steps:"
echo "  1. Restart Cursor to load new MCP Servers"
echo "  2. All components are now available GLOBALLY across all projects"
echo "  3. MCP config is at: $USER_CURSOR_DIR/mcp.json"
echo ""
echo "To verify everything works, ask Cursor in Agent mode:"
echo '  "List all available MCP tools"'
echo ""
