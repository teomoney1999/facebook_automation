# Facebook Automation Bot

Công cụ tự động hóa đăng bài và quản lý tham gia hội nhóm Facebook theo chuẩn **Production-Ready**, thiết kế với triết lý **An toàn tài khoản là ưu tiên số một** (*Human-Centric & Resilience First*).

---

## Tính năng chính

- **Đăng bài tự động theo Batch (Văn bản & Hình ảnh)**: Hỗ trợ đăng bài lên danh sách nhiều nhóm Facebook với ảnh đính kèm.
- **Cơ chế Anti-Ban & Resilience**:
  - **Jitter Delay**: Nghỉ ngẫu nhiên giữa các bài đăng (mặc định `45s – 90s`).
  - **Cooldown 24h & State Persistence**: Tự động ghi nhận lịch sử vào `repo/json/history.json` để tránh đăng trùng vào cùng một nhóm trong vòng 24 giờ.
  - **Circuit Breaker**: Tự động ngắt khẩn cấp toàn bộ phiên chạy khi phát hiện tài khoản bị khóa tính năng (`ACTION_BLOCKED`) hoặc gặp lỗi liên tiếp.
- **Chèn văn bản không xâm phạm Clipboard**: Sử dụng JavaScript Native DOM Events (`document.execCommand('insertText')` / `InputEvent`) thay vì `pyperclip`, không làm ảnh hưởng clipboard hệ điều hành.
- **Hậu kiểm DOM thực tế (`PostVerifier`)**: Phân loại rõ ràng trạng thái sau khi đăng (`SUCCESS`, `PENDING_APPROVAL`, `ACTION_BLOCKED`, `MARKETPLACE_FORM`, `FAILED`).
- **DOM Canary (`--check-dom`)**: Quét và báo cáo sức khỏe selector trong `repo/json/selectors.json` để phát hiện sớm thay đổi giao diện từ Facebook.
- **Quản lý gia nhập nhóm (`--check-membership`, `--join-groups`)**: Kiểm tra trạng thái thành viên và tự động gửi yêu cầu tham gia nhóm kèm câu trả lời mẫu.
- **Giao diện Desktop GUI & CLI**: Hỗ trợ chạy qua giao diện Tkinter (`app_ui.py`), dòng lệnh (`main.py`), và tùy chọn điều khiển từ xa qua Telegram Bot.

---

## Cấu trúc thư mục

```text
facebook_automation/
├── README.md
├── ARCHITECTURE.md               # Chi tiết kiến trúc hệ thống & workflow 7 giai đoạn
├── GEMINI.md                     # Quy chuẩn code & tiêu chuẩn vận hành production
├── PROGRESS.md                   # Nhật ký tiến độ các module
├── facebook_bot.spec             # Cấu hình đóng gói PyInstaller
├── run_gui.sh                    # Script khởi chạy Desktop GUI (macOS/Linux)
├── run_cli.sh                    # Script khởi chạy CLI (macOS/Linux)
├── docs/
│   └── PACKAGING_GUIDE.md        # Hướng dẫn đóng gói ứng dụng
├── scripts/
│   ├── build_macos.sh            # Build app cho macOS
│   └── build_windows.bat         # Build .exe cho Windows
└── repo/
    ├── .env.example              # Mẫu biến môi trường
    ├── requirements.txt          # Thư viện Python phụ thuộc
    ├── config.py                 # Quản lý cấu hình tập trung (Fail-Fast)
    ├── facebookbot.py            # Orchestrator điều phối quy trình đăng bài
    ├── app_ui.py                 # Giao diện Desktop GUI (Tkinter)
    ├── main.py                   # CLI Entrypoint & ProcessLock
    ├── browser/                  # Tầng trình duyệt (Driver, DOM Actions, DOM Inspector)
    ├── services/                 # Tầng nghiệp vụ (State, Verifier, Circuit Breaker, Joiner, Telegram)
    ├── helpers/                  # Logging chuẩn production & tiện ích
    ├── json/
    │   └── selectors.json        # Bộ selector DOM & fallback cho Facebook
    ├── images/                   # Thư mục chứa ảnh đính kèm khi đăng bài
    └── tests/                    # Bộ kiểm thử đơn vị (Unit & Integration Tests)
```

---

## Yêu cầu hệ thống

- **Python**: 3.10 trở lên (kèm `tkinter` nếu sử dụng Desktop GUI).
- **Trình duyệt**: Google Chrome (phiên bản mới nhất).
- **Hệ điều hành**: macOS, Linux, hoặc Windows.

---

## Cài đặt & Cấu hình

### 1. Clone repository và tạo môi trường ảo

```bash
git clone https://github.com/teomoney1999/facebook_automation.git
cd facebook_automation

python3 -m venv .venv
source .venv/bin/activate   # Trên Windows: .venv\Scripts\activate
pip install -r repo/requirements.txt
```

### 2. Cấu hình biến môi trường (`.env`)

Tạo file `repo/.env` từ file mẫu [repo/.env.example](repo/.env.example):

```bash
cp repo/.env.example repo/.env
```

Mở `repo/.env` và điền thông tin:

```ini
# Facebook Bot Credentials
EMAIL=your_email_or_phone@example.com
PASSWORD=your_facebook_password

# Anti-Ban Delays & Batch Limits
MIN_DELAY_BETWEEN_POSTS=45.0
MAX_DELAY_BETWEEN_POSTS=90.0
MAX_POSTS_PER_RUN=10
COOLDOWN_HOURS=24

# Group Joining Timing & Limits
JOIN_DELAY_MIN=15.0
JOIN_DELAY_MAX=35.0
JOIN_LIMIT=10

# Optional Telegram Remote Control (để trống nếu không dùng)
TELEGRAM_BOT_TOKEN=
TELEGRAM_ADMIN_ID=
```

### 3. Dữ liệu nhóm và bài viết (`repo/json/`)

Để bảo vệ dữ liệu riêng tư, `repo/json/links.json` và `repo/json/posts.json` được loại trừ khỏi Git. 

> **💡 Zero-Config**: Bạn **không cần phải tạo file thủ công**! 
> - Khi mở ứng dụng lần đầu, hệ thống tự động khởi tạo cấu trúc dữ liệu rỗng chuẩn contract.
> - Trên Desktop GUI (Tab *Nội dung & Nhóm mục tiêu*), bạn chỉ cần dán link nhóm vào phần **➕ Thêm Nhóm...** (hoặc nhấn `Ctrl+V` / `Cmd+V` trên bảng nhóm), code sẽ tự động lọc sạch canonical URL và lưu vào `links.json`.
> - Nếu muốn chuẩn bị file mẫu thủ công trước, cấu trúc schema như sau:

**`repo/json/links.json`** (danh sách nhóm mục tiêu):
```json
{
  "groups": [
    {
      "url": "https://www.facebook.com/groups/example_group",
      "name": "Example Group",
      "members": "—",
      "privacy": "—",
      "membership": "ALREADY_JOINED",
      "updated_at": null
    }
  ],
  "target_links": [
    "https://www.facebook.com/groups/example_group"
  ]
}
```

**`repo/json/posts.json`** (nội dung bài viết):
```json
{
  "posts": [
    {
      "id": "post_1",
      "name": "Bài mẫu 01",
      "content": "Nội dung bài viết của bạn...",
      "images": [],
      "is_selected": true,
      "status": "ready"
    }
  ]
}
```

---

## Hướng dẫn sử dụng

### Chế độ Desktop GUI

```bash
./run_gui.sh
# Hoặc chạy trực tiếp:
.venv/bin/python repo/app_ui.py
```

### Chế độ CLI

Sử dụng `./run_cli.sh` (hoặc `python repo/main.py`) kèm các cờ điều khiển:

```bash
# 1. Đăng nhập thủ công lần đầu để lưu session vào Chrome Profile
./run_cli.sh --login

# 2. Chạy thử (không mở Chrome, kiểm tra lọc cooldown & batch)
./run_cli.sh --dry-run

# 3. Kiểm tra sức khỏe DOM Facebook (DOM Canary Check)
./run_cli.sh --check-dom

# 4. Xem thống kê lịch sử & cooldown 24h
./run_cli.sh --status

# 5. Chạy đăng bài theo batch mặc định
./run_cli.sh

# 6. Giới hạn số nhóm đăng trong phiên hoặc test trên 1 nhóm cụ thể
./run_cli.sh --batch-size 5
./run_cli.sh --url "https://www.facebook.com/groups/example_group/"

# 7. Đăng bài kèm ảnh chỉ định
./run_cli.sh --image path/to/image1.jpg path/to/image2.jpg

# 8. Kiểm tra trạng thái thành viên & tự động gia nhập nhóm
./run_cli.sh --check-membership
./run_cli.sh --join-groups --join-limit 10

# 9. Đăng xuất và xóa session/cookies khỏi Chrome Profile
./run_cli.sh --logout
```

---

## Kiểm thử & Đóng gói

### Chạy Unit Tests

```bash
.venv/bin/pytest repo/tests/
```

### Đóng gói ứng dụng (PyInstaller)

Xem hướng dẫn chi tiết tại [docs/PACKAGING_GUIDE.md](docs/PACKAGING_GUIDE.md):

- **macOS**: `./scripts/build_macos.sh`
- **Windows**: `scripts\build_windows.bat`

---

## Lưu ý bảo mật

- **Tuyệt đối không commit** các file/thư mục chứa thông tin nhạy cảm hoặc phiên đăng nhập: `repo/.env`, `repo/FacebookBotProfile/`, `repo/json/history.json`, `repo/json/links.json`, `repo/json/posts.json`, `repo/logs/`, `repo/bot.lock`.
- Các mục trên đã được cấu hình chặn sẵn trong [.gitignore](.gitignore).
