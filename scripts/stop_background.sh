#!/usr/bin/env bash
# ==============================================================================
# Facebook Automation Bot - Background Daemon Stopper (POSIX / macOS / Linux)
# Safely terminates background bot process using SIGTERM, allowing driver cleanup.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BASE_DIR"

PID_FILE="$BASE_DIR/repo/logs/bot.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "⚠️  Không tìm thấy tệp PID ($PID_FILE). Bot có thể chưa được chạy ngầm."
    exit 0
fi

PID=$(cat "$PID_FILE" 2>/dev/null || true)

if [ -z "$PID" ]; then
    echo "⚠️  Tệp PID rỗng. Đang dọn dẹp..."
    rm -f "$PID_FILE"
    exit 0
fi

if ! kill -0 "$PID" 2>/dev/null; then
    echo "ℹ️  Tiến trình $PID không còn hoạt động. Dọn dẹp tệp PID."
    rm -f "$PID_FILE"
    exit 0
fi

echo "🛑 Đang gửi tín hiệu dừng an toàn (SIGTERM) tới Facebook Bot (PID: $PID)..."
kill -15 "$PID" 2>/dev/null || true

# Grace period loop (up to 5 seconds) for Chrome WebDriver teardown
for i in {1..5}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "✅ Facebook Bot (PID: $PID) đã dừng an toàn và giải phóng tài nguyên."
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

# If still running after grace period, escalate to SIGKILL
if kill -0 "$PID" 2>/dev/null; then
    echo "⚠️  Tiến trình $PID chưa dừng sau 5 giây. Buộc dừng khẩn cấp (SIGKILL)..."
    kill -9 "$PID" 2>/dev/null || true
    echo "🛑 Đã buộc dừng tiến trình $PID."
fi

rm -f "$PID_FILE"
