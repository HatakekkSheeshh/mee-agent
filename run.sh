#!/bin/bash
# GreenNode Interview Assistant — Quick Start
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check for .env
if [ ! -f .env ]; then
    echo "No .env file found. Copying from .env.example..."
    cp .env.example .env
    echo "Please edit .env with your VNGCloud MaaS credentials, then run again."
    exit 1
fi

# Check for claude CLI
if ! command -v claude &> /dev/null; then
    echo "Warning: 'claude' CLI not found in PATH."
    echo "Assessment will fail without Claude Code installed."
    echo "Install: https://docs.anthropic.com/en/docs/claude-code"
fi

# Install dependencies if needed
if ! python3 -c "import fastapi, openpyxl, soundfile" 2>/dev/null; then
    echo "Installing dependencies..."
    pip3 install -r requirements_interview.txt
fi

echo ""
echo "==================================="
echo " GreenNode Interview Assistant"
echo "==================================="
echo " Web UI:    http://localhost:8000"
echo " WebSocket: ws://localhost:9090"
echo "==================================="
echo ""

python3 run_interview.py "$@"
