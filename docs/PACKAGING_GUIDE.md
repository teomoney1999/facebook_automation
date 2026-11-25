# Hướng Dẫn Đóng Gói Đa Nền Tảng (Packaging & Distribution Guide)

Tài liệu này hướng dẫn cách đóng gói **Facebook Automation Bot** thành ứng dụng độc lập (standalone executable) có thể chạy trực tiếp trên **Windows** (`.exe`) và **macOS** (`.app`) mà không yêu cầu máy người dùng phải cài sẵn Python hay môi trường ảo.

---

## 1. Yêu Cầu Môi Trường Trước Khi Đóng Gói (Prerequisites)

- **Python**: Phiên bản 3.9 trở lên.
- **Google Chrome**: Đã cài đặt trên máy người dùng (ChromeDriver sẽ được tự động quản lý qua `webdriver_manager`).
- **PyInstaller**:
  ```bash
  pip install pyinstaller
  ```

---

## 2. Cấu Trúc Đặc Tả Đóng Gói (`facebook_bot.spec`)

Tệp đặc tả `facebook_bot.spec` được tối ưu theo mô hình `onedir` (thư mục phân phối độc lập):
- **Tại sao chọn `onedir` thay vì `onefile`?**
  - Mô hình `onefile` phải giải nén toàn bộ thư viện và profile Chrome ra thư mục `%TEMP%` mỗi lần khởi động, làm chậm tốc độ khởi chạy từ 5 - 15 giây.
  - Mô hình `onedir` khởi động tức thì, cho phép lưu trữ cache profile Chrome (`FacebookBotProfile`) và tệp cấu hình `.env` ngay bên cạnh tệp thực thi.
- **Tài nguyên tĩnh được đóng gói kèm:**
  - `repo/json/` (chứa `links.json`, `posts.json`, `selectors.json`, `history.json` mẫu).
  - `repo/.env.example`.
- **Ẩn console nền (Windowed App):**
  - Đặt `console=False` để khi người dùng mở GUI, không bị bật cửa sổ cmd/terminal đen gây phiền toái. Mọi log đều được hiển thị trực tiếp trong **Live Log Console** của Tab 3.

---

## 3. Hướng Dẫn Đóng Gói Trên macOS (.app)

### Bước 1: Cấp quyền thực thi và chạy script build
```bash
chmod +x scripts/build_macos.sh
./scripts/build_macos.sh
```

### Bước 2: Kết quả đầu ra
- Ứng dụng độc lập: `dist/FacebookBot.app`
- Tệp nén phân phối: `dist/FacebookBot-macOS.zip`

### Bước 3: Xử lý cảnh báo bảo mật macOS Gatekeeper (Nếu gặp)
Nếu người dùng tải file zip về và macOS báo *"App is damaged and can't be opened"* do chính sách kiểm soát chứng chỉ của Apple, hãy chạy lệnh sau trong Terminal để gỡ cờ cách ly:
```bash
xattr -cr /path/to/FacebookBot.app
```

---

## 4. Hướng Dẫn Đóng Gói Trên Windows (.exe)

### Bước 1: Chạy file kịch bản build
Nhấp đúp chuột vào file `scripts\build_windows.bat` hoặc mở Command Prompt chạy:
```cmd
scripts\build_windows.bat
```

### Bước 2: Kết quả đầu ra
Toàn bộ ứng dụng sẽ nằm trong thư mục:
```text
dist\FacebookBot\
├── FacebookBot.exe       <-- File thực thi chính
├── .env.example          <-- File mẫu cấu hình
├── json\                 <-- Thư mục cấu hình links & posts
└── _internal\            <-- Thư viện và runtime nhúng
```

### Bước 3: Phân phối cho người dùng
1. Nén toàn bộ thư mục `dist\FacebookBot` thành file `FacebookBot-Windows.zip`.
2. Hướng dẫn người dùng giải nén, đổi tên `.env.example` thành `.env`, điền thông tin tài khoản và nhấp đúp vào `FacebookBot.exe`.

---

## 5. Quy Chuẩn Vận Hành Của Ứng Dụng Sau Đóng Gói

- **Vị trí file `.env`**: Ứng dụng sẽ ưu tiên tìm file `.env` nằm cùng cấp thư mục với file thực thi `FacebookBot.exe` hoặc bên trong thư mục ứng dụng.
- **Lưu lịch sử & nhật ký**:
  - `logs/bot.log` và `logs/screenshots/` sẽ được tự động tạo và lưu trữ cục bộ.
  - Lịch sử đăng bài `history.json` được bảo toàn qua các phiên chạy, đảm bảo giãn cách 24h hoạt động chính xác.
- **Điều khiển từ xa Telegram**: Nếu người dùng điền `TELEGRAM_BOT_TOKEN` và `TELEGRAM_ADMIN_ID` vào Tab 1, bot sẽ tự động lắng nghe lệnh từ điện thoại ngay cả khi ứng dụng đang thu nhỏ (minimize).
