#!/usr/bin/env bash
# ==============================================================================
# Facebook Automation Bot - Background Daemon Launcher (POSIX / macOS / Linux)
# Launches the bot in background (daemon) mode with --headless enabled.
# ==============================================================================

set -e

# Resolve project base directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BASE_DIR"

mkdir -p "$BASE_DIR/repo/logs"
PID_FILE="$BASE_DIR/repo/logs/bot.pid"
LOG_FILE="$BASE_DIR/repo/logs/bot_background.log"

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE" 2>/dev/null || true)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "⚠️  Facebook Bot đang chạy ở tiến trình ngầm (PID: $OLD_PID)."
        echo "👉 Sử dụng ./scripts/stop_background.sh để dừng trước khi khởi chạy mới."
        exit 1
    else
        # Stale PID file
        rm -f "$PID_FILE"
    fi
fi

echo "🚀 Đang khởi chạy Facebook Bot ở chế độ ngầm (Headless Daemon)..."

# Run with nohup in background, passing all extra CLI arguments
nohup python3 repo/main.py --headless "$@" > "$LOG_FILE" 2>&1 &
BOT_PID=$!

# Record PID
echo "$BOT_PID" > "$PID_FILE"

sleep 1

# Check if process is still running or finished successfully
if kill -0 "$BOT_PID" 2>/dev/null; then
    echo "✅ Facebook Bot đã khởi chạy ngầm thành công!"
    echo "📌 Tiến trình PID : $BOT_PID"
    echo "📄 Log thời gian thực: tail -f repo/logs/bot_background.log"
    echo "🛑 Lệnh dừng an toàn : ./scripts/stop_background.sh"
else
    # Process might have already finished quickly (e.g. --dry-run, --status)
    EXIT_CODE=0
    wait "$BOT_PID" 2>/dev/null || EXIT_CODE=$?
    if [ "$EXIT_CODE" -eq 0 ]; then
        echo "✅ Facebook Bot đã thực thi ngầm và hoàn tất thành công (Exit Code: 0)."
        echo "📄 Xem chi tiết kết quả tại: repo/logs/bot_background.log"
        rm -f "$PID_FILE"
    else
        echo "❌ Tiến trình bot kết thúc với lỗi (Exit Code: $EXIT_CODE). Chi tiết tại $LOG_FILE:"
        cat "$LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
fi
