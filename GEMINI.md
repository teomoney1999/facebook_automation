# GEMINI.md - Quy Chuẩn Code & Tiêu Chuẩn Production Facebook Automation

Tài liệu này định hình các nguyên tắc kỹ thuật, kiến trúc mã nguồn và quy chuẩn vận hành chuẩn production cho dự án **Facebook Automation Bot**.

---

## 1. Nguyên Tắc The Pragmatic Programmer

- **Care About Your Craft**: Tận tâm với chất lượng code; coi trọng tính ổn định của bot và độ an toàn của tài khoản Facebook người dùng.
- **ETC (Easy to Change)**: Thiết kế phân lớp rõ ràng; việc thay đổi công nghệ trình duyệt (Selenium -> Playwright) hay nơi lưu dữ liệu (JSON -> SQLite) không làm xáo trộn business logic.
- **Semantic DRY**: Gom code theo lý do thay đổi và ngữ nghĩa nghiệp vụ; loại bỏ các cấu hình, đường dẫn, timing delays rải rác; mọi tham số đều có nguồn chân lý (Single Source of Truth).
- **Orthogonality (Tính Trực Giao)**: Tách rời tuyệt đối giữa Tầng Trình duyệt (Browser Lifecycle / DOM Actions), Tầng Nghiệp vụ (Facebook Bot / Verification), và Tầng Dữ liệu/Cấu hình.
- **Broken Windows Theory**: Sửa triệt để các cảnh báo linter, dead code, file thừa/code cũ; không tích lũy nợ kỹ thuật.
- **Design by Contract (DBC)**: Mọi thao tác đều có Tiền điều kiện (Preconditions) và Hậu điều kiện (Postconditions). Tuyệt đối không báo cáo "Thành công" giả khi chưa xác minh trạng thái thực tế sau khi click Post.
- **Crash Early & Fail Fast / Dead Programs Tell No Lies**: Thất bại sớm nếu thiếu cấu hình hoặc tài khoản bị phạt. Tuyệt đối không dùng `except Exception: pass` để nuốt lỗi âm thầm.
- **Use Tracer Bullets**: Triển khai luồng end-to-end hoàn chỉnh (Config -> Driver -> Post -> Verify -> Log) trên một group thử nghiệm nhỏ trước khi mở rộng chạy hàng loạt.
- **Domain-Driven Naming**: Đặt tên class, hàm, biến chuẩn xác, bám sát các khái niệm tự động hoá và mạng xã hội (`PostStatus`, `CircuitBreaker`, `StateManager`, `DOMActions`).

---

## 2. Tiêu Chuẩn Kỹ Thuật Production-Ready (Python / Selenium Automation)

- **Zero Silent Failure Guarantee**: Mọi ngoại lệ đều phải được phân loại cụ thể (`TimeoutException`, `NoSuchElementException`, `ActionBlockedError`), ghi log có ngữ cảnh kèm stacktrace khi cần thiết.
- **Resource Management (Finish What You Start)**: 
  - Luôn đảm bảo đóng trình duyệt và giải phóng tài nguyên RAM bằng Context Manager (`with ... as ...`) hoặc khối `finally`.
  - Tự động phát hiện và giải phóng cổng debugging (`9224`) và file `SingletonLock` của Chrome trước khi khởi chạy nhằm ngăn chặn lỗi kẹt tiến trình.
- **Thao tác DOM An toàn & Độc lập Clipboard**:
  - Không ép buộc sử dụng Clipboard hệ điều hành (`pyperclip`) gây ảnh hưởng đến công việc của người dùng máy tính.
  - Ưu tiên phương thức chèn văn bản qua JavaScript DOM (`InputEvent` / `document.execCommand('insertText')`) để đảm bảo hoạt động ổn định trên cả môi trường headless / server Linux.
- **Mô Hình Hóa Trạng Thái Tường Minh (Explicit State Modeling)**:
  - Sử dụng `Enum` cho trạng thái bài viết (`PostStatus`: `SUCCESS`, `PENDING_APPROVAL`, `ACTION_BLOCKED`, `MARKETPLACE_FORM`, `FAILED`), không dùng các chuỗi string rời rạc.
- **Bảo Vệ Tài Khoản & Chống Spam (Anti-Ban & Resilience)**:
  - **Jitter & Cooldown**: Luôn có thời gian nghỉ ngẫu nhiên thực tế (45s – 90s) giữa các bài đăng.
  - **Batching**: Giới hạn số lượng bài đăng tối đa trong một phiên chạy (mặc định 5–10 bài/lần).
  - **State Persistence & Cooldown 24h**: Không bao giờ đăng trùng vào một nhóm trong vòng 24 giờ.
  - **Circuit Breaker**: Tự động ngắt khẩn cấp và dừng toàn bộ chương trình khi phát hiện tài khoản bị Facebook tạm khoá tính năng (Action Blocked) hoặc gặp lỗi liên tiếp.
- **Production Observability**:
  - Sử dụng thư viện `logging` chuẩn của Python với định dạng có timestamp, cấp độ log (INFO, WARNING, ERROR).
  - Xuất log song song: hiển thị trực quan trên Console và lưu trữ vào file có cơ chế xoay vòng (`RotatingFileHandler`: `logs/bot.log`).
  - Tự động chụp ảnh màn hình lưu vào `logs/screenshots/` khi phát sinh sự cố hoặc lỗi tương tác DOM.
- **Bảo Mật & Quản Lý Cấu Hình**:
  - Không hardcode tài khoản, mật khẩu hay đường dẫn tuyệt đối vào source code.
  - Luôn duy trì file mẫu `.env.example` và thiết lập `.gitignore` chặt chẽ bảo vệ profile Chrome và thông tin cá nhân.

---

## 3. Chiến Lược Phân Kỳ & Bàn Giao

- **Phân Rã Module Độc Lập**: Từng module (Config, Driver, State, Verifier, Orchestrator) phải hoạt động độc lập và có khả năng kiểm thử đơn vị (Unit Test) riêng rẽ mà không bắt buộc phải mở Chrome thực tế.
- **Tách Biệt Bàn Giao**: Sau mỗi module hoàn thành, báo cáo rõ:
  1. Files thay đổi / tạo mới.
  2. Tính năng kỹ thuật đã kiểm chứng.
  3. Lợi ích mang lại theo chuẩn Pragmatic.
  4. Hạng mục kết nối cho module tiếp theo.

---

## 4. Quy Trình Làm Việc Bắt Buộc Của Agent

- **Giải Thích Trước Khi Làm**: Trước mỗi module/task, luôn giải thích rõ ràng 4 yếu tố:
  1. **Làm gì** (Mục tiêu và phạm vi thay đổi).
  2. **Vì sao làm** (Vấn đề hiện tại cần giải quyết).
  3. **Ưu điểm** (Lợi ích về bảo trì, an toàn, hiệu năng).
  4. **Cách người ta thường làm hiện nay** (Industry Standard / Best Practices).
- **Theo Dõi Tiến Độ**: Duy trì và cập nhật tiến độ (progress) sau mỗi chu kỳ làm việc.
