#!/bin/bash
# Deep Sky Advisor Analyzer Launcher
# Automatically detects and uses the correct Python environment
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${SKILL_DIR}/.venv"

# Check if venv exists, create if not
if [ ! -d "$VENV_DIR" ]; then
    echo "🔧 Setting up virtual environment..."
    
    # Prefer the repository's supported Python version, then fall back safely.
    if command -v python3.12 >/dev/null 2>&1; then
        PYTHON="$(command -v python3.12)"
    elif [ -x "/opt/homebrew/bin/python3" ]; then
        PYTHON="/opt/homebrew/bin/python3"
    else
        PYTHON="python3"
    fi
    
    "$PYTHON" -m venv "$VENV_DIR"
    
    # Install dependencies
    echo "📦 Installing dependencies..."
    "$VENV_DIR/bin/pip" install -r "$SKILL_DIR/requirements.txt" -q
    
    echo "✅ Environment ready!"
fi

# Run the analysis script
exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/analyze_file.py" "$@"
