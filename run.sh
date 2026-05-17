#!/usr/bin/env bash
# GSTR-1 Generator — Web UI launcher
# Starts the Flask server on http://127.0.0.1:5050

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 not found. Please install Python 3.9+."
    exit 1
fi

# Install dependencies if missing
python3 -c "import flask, pandas, openpyxl, rapidfuzz" 2>/dev/null || {
    echo "Installing dependencies..."
    pip install --user flask pandas openpyxl rapidfuzz xlrd
}

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  GSTR-1 Generator"
echo "  Open http://127.0.0.1:5050 in your browser"
echo "  Press Ctrl+C to stop"
echo "═══════════════════════════════════════════════════════════════"
echo ""

cd web
exec python3 app.py
