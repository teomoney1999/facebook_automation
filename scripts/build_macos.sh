#!/usr/bin/env bash
# ==============================================================================
# Facebook Automation Bot - macOS Build & Packaging Script (.app & .zip)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "======================================================================"
echo "🍏 Đóng gói ứng dụng Facebook Automation Bot cho macOS (.app)"
echo "======================================================================"

cd "$ROOT_DIR"

# 1. Kiểm tra Python & PyInstaller
if ! command -v python3 &>/dev/null; then
    echo "❌ Lỗi: python3 chưa được cài đặt trên hệ thống."
    exit 1
fi

if ! python3 -m pyinstaller --version &>/dev/null; then
    echo "⚠️ PyInstaller chưa được cài đặt. Đang tiến hành cài đặt..."
    python3 -m pip install pyinstaller
fi

echo "✅ Phiên bản PyInstaller: $(python3 -m pyinstaller --version)"

# 2. Dọn dẹp thư mục build cũ
echo "🧹 Dọn dẹp các thư mục build cũ (build/, dist/)..."
rm -rf build dist

# 3. Tiến hành đóng gói với PyInstaller
echo "🔨 Đang đóng gói với facebook_bot.spec..."
python3 -m pyinstaller --clean facebook_bot.spec

# 4. Kiểm tra file .app tạo ra
if [ -d "dist/FacebookBot.app" ]; then
    echo "✅ Tạo thành công macOS Application Bundle: dist/FacebookBot.app"
    
    # Copy file .env mẫu vào thư mục dist để người dùng dễ cấu hình
    cp repo/.env.example dist/.env.example
    
    # 5. Nén file phân phối ZIP
    echo "📦 Đang nén file phân phối dist/FacebookBot-macOS.zip..."
    (cd dist && zip -r -q "FacebookBot-macOS.zip" "FacebookBot.app" ".env.example")
    
    echo "🎉 Đóng gói hoàn tất!"
    echo "📁 Đường dẫn ứng dụng: dist/FacebookBot.app"
    echo "📁 File nén cài đặt : dist/FacebookBot-macOS.zip"
    echo ""
    echo "💡 Ghi chú: Nếu gặp lỗi cảnh báo bảo mật khi mở app trên macOS, hãy chạy:"
    echo "   xattr -cr dist/FacebookBot.app"
else
    echo "❌ Lỗi: Không tìm thấy dist/FacebookBot.app sau khi build."
    exit 1
fi
