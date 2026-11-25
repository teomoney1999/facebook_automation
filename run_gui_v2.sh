#!/usr/bin/env bash
# Khởi chạy giao diện Desktop GUI v2.0 (Độc lập, 12 màn Canva, Multi-Post & Round-Robin)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_EXEC="$SCRIPT_DIR/.venv/bin/python"

if [ ! -f "$PYTHON_EXEC" ]; then
    if [ -f "/opt/homebrew/bin/python3.14" ]; then
        PYTHON_EXEC="/opt/homebrew/bin/python3.14"
    else
        PYTHON_EXEC="$(command -v python3 || echo "python3")"
    fi
fi

exec "$PYTHON_EXEC" "$SCRIPT_DIR/repo/app_ui_v2.py" "$@"
