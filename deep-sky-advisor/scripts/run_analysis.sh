#!/bin/bash
# Deep Sky Advisor Analyzer Launcher
# Automatically detects and uses the correct Python environment
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/venv_hb"

# Check if venv exists, create if not
if [ ! -d "$VENV_DIR" ]; then
    echo "🔧 Setting up virtual environment..."
    
    # Try Homebrew Python first, then system Python
    if [ -x "/opt/homebrew/bin/python3.11" ]; then
        PYTHON="/opt/homebrew/bin/python3.11"
    elif [ -x "/opt/homebrew/bin/python3" ]; then
        PYTHON="/opt/homebrew/bin/python3"
    else
        PYTHON="python3"
    fi
    
    "$PYTHON" -m venv "$VENV_DIR"
    
    # Install dependencies
    echo "📦 Installing dependencies..."
    "$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/../requirements.txt" -q
    
    echo "✅ Environment ready!"
fi

# Run the analysis script
exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/analyze_file.py" "$@"
