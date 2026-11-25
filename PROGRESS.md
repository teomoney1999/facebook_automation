# PROGRESS.md - Bảng Theo Dõi Tiến Độ Nâng Cấp Hệ Thống

Tài liệu này theo dõi tiến độ triển khai các module của dự án **Facebook Automation Bot** theo đúng quy định trong [GEMINI.md](file:///Users/teomoney/Documents/facebook_automation/GEMINI.md) ("*Duy trì và cập nhật tiến độ sau mỗi chu kỳ làm việc*").

---

## 📊 TỔNG QUAN TIẾN ĐỘ TOÀN DỰ ÁN

- **Mục tiêu**: Nâng cấp toàn diện codebase từ script tự phát sang kiến trúc 5 tầng chuẩn Production-Ready & The Pragmatic Programmer, tích hợp Desktop GUI, Telegram Remote Control và Đóng gói Đa nền tảng.
- **Trạng thái hiện tại**: `HOÀN THÀNH TOÀN DIỆN (209/209 TESTS PASS 100%) - TÍCH HỢP TỔNG KẾT THEO LẦN ĐĂNG & THỜI GIAN HOÀN THÀNH CÔNG VIỆC TRÊN GUI`.

```
[████████████████████] 100% Hoàn thành (5 Core Modules + Hardening + GUI v1 & v2 + Telegram + Packaging + Multi-Drafts + Group Membership & Join Cooldown Controls + Per-Session Summary & Completion Time, 209 Tests PASS)
```

---

## 📋 CHI TIẾT TIẾN ĐỘ THEO TỪNG MODULE

| Module | Tên Module & Trách nhiệm | Tập tin liên quan | Trạng thái |
| :---: | :--- | :--- | :--- :|
| **P0** | **Quy chuẩn & Kiến trúc Cốt lõi**<br/>• Thiết lập `GEMINI.md`<br/>• Lưu đồ kiến trúc `ARCHITECTURE.md`<br/>• Lưu trữ code cũ vào `archive/`<br/>• Chuẩn hoá `.gitignore`, `.env.example`, `requirements.txt` | • `GEMINI.md`<br/>• `ARCHITECTURE.md`<br/>• `repo/archive/main_legacy.py`<br/>• `.gitignore`<br/>• `repo/.env.example`<br/>• `repo/requirements.txt` | <span style="color:green">✅ HOÀN THÀNH</span> |
| **M1** | **Foundation & Observability**<br/>• Cấu hình bất biến `BotConfig`<br/>• Fail-fast credential validation<br/>• Logging xoay vòng (`logs/bot.log`)<br/>• Tiện ích chụp ảnh lỗi `capture_screenshot`<br/>• Bộ kiểm thử độc lập `test_module1.py` | • `repo/config.py`<br/>• `repo/helpers/logger.py`<br/>• `repo/tests/test_module1.py` | <span style="color:green">✅ HOÀN THÀNH (8/8 Unit Tests PASS)</span> |
| **M2** | **Browser Automation Engine & DOM Health**<br/>• Dọn dẹp port 9224 & SingletonLock rác<br/>• Context manager Chrome lifecycle<br/>• JS Native DOM Text Injection (Không chiếm clipboard)<br/>• Fallback Selector Registry (`selectors.json`)<br/>• Công cụ quét DOM Drift (`dom_inspector.py`) | • `repo/browser/driver_manager.py`<br/>• `repo/browser/dom_actions.py`<br/>• `repo/browser/dom_inspector.py`<br/>• `repo/json/selectors.json`<br/>• `repo/tests/test_module2.py` | <span style="color:green">✅ HOÀN THÀNH (32/32 Unit Tests PASS)</span> |
| **M3** | **State & History Manager**<br/>• Quản lý lịch sử `json/history.json`<br/>• Ghi file nguyên tử (Atomic write chống corrupt)<br/>• Bộ lọc Cooldown 24h không spam trùng<br/>• Cơ chế cắt Batching (5–10 bài/lần)<br/>• Fast-Exit khi danh sách đủ điều kiện rỗng | • `repo/services/state_manager.py`<br/>• `repo/services/__init__.py`<br/>• `repo/json/history.json`<br/>• `repo/tests/test_module3.py` | <span style="color:green">✅ HOÀN THÀNH (13/13 Unit Tests PASS)</span> |
| **M4** | **Post Verification & Circuit Breaker**<br/>• Thanh tra DOM: Phân loại 5 trạng thái `PostStatus`<br/>• Ngắt mạch khẩn cấp khi bị khóa tính năng<br/>• Chụp ảnh bằng chứng khi vi phạm | • `repo/services/post_verifier.py`<br/>• `repo/services/circuit_breaker.py`<br/>• `repo/tests/test_module4.py` | <span style="color:green">✅ HOÀN THÀNH (16/16 Unit Tests PASS)</span> |
| **M5** | **Bot Orchestrator & CLI Integration**<br/>• Tái cấu trúc `FacebookBot` (< 150 dòng tinh gọn)<br/>• Tích hợp luồng an toàn 7 giai đoạn<br/>• CLI hỗ trợ `--dry-run`, `--check-dom`, `--status`, `--batch-size`, `--url`<br/>• Bộ kiểm thử độc lập `test_module5.py` | • `repo/facebookbot.py`<br/>• `repo/main.py`<br/>• `repo/tests/test_module5.py` | <span style="color:green">✅ HOÀN THÀNH (18/18 Unit Tests PASS)</span> |
| **HARDEN** | **Production Readiness Hardening**<br/>• Trạng thái `UNKNOWN` chống báo `SUCCESS` giả<br/>• Khóa đơn tiến trình `ProcessLock` chống corrupt<br/>• Lưu Cooldown cho `ACTION_BLOCKED` vào disk<br/>• Lọc URL bỏ query params, chặn batch âm, exit codes | • `repo/services/post_verifier.py`<br/>• `repo/services/state_manager.py`<br/>• `repo/browser/driver_manager.py`<br/>• `repo/tests/test_production_hardening.py` | <span style="color:green">✅ HOÀN THÀNH (8/8 Unit Tests PASS)</span> |
| **TELEGRAM** | **Telegram Remote Control Service**<br/>• Long-polling daemon thread, zero 3rd-party dep<br/>• Lệnh từ xa: `/status`, `/start`, `/dryrun`, `/stop`<br/>• Push alerts & tự động upload screenshot khi lỗi<br/>• Whitelist Admin Guard | • `repo/services/telegram_controller.py`<br/>• `repo/config.py`<br/>• `repo/tests/test_telegram.py` | <span style="color:green">✅ HOÀN THÀNH (6/6 Unit Tests PASS)</span> |
| **GUI & PKG** | **Desktop GUI & Cross-Platform Packaging**<br/>• 3 Tab: Cấu hình .env, Nội dung/Nhóm, Dashboard<br/>• WorkerThread + QueueHandler (Zero UI Freeze)<br/>• PyInstaller spec (`onedir` Portable), scripts build Win/Mac | • `repo/app_ui.py`<br/>• `facebook_bot.spec`<br/>• `scripts/build_macos.sh`<br/>• `scripts/build_windows.bat`<br/>• `docs/PACKAGING_GUIDE.md`<br/>• `repo/tests/test_gui.py` | <span style="color:green">✅ HOÀN THÀNH (4/4 Unit Tests PASS)</span> |
| **MEDIA** | **Đăng Bài Kèm Ảnh (CLI & GUI)**<br/>• CLI tự động nạp ảnh từ `repo/images/` hoặc cờ `--images`<br/>• GUI Tab 2 quản lý ảnh đính kèm (Thêm/Xóa/Lưu)<br/>• Cơ chế `send_keys` chuẩn Selenium, unhide input, đồng bộ preview<br/>• Fail-fast kiểm tra tồn tại của tệp ảnh trước khi bật Chrome | • `repo/images/`<br/>• `repo/browser/dom_actions.py`<br/>• `repo/facebookbot.py`<br/>• `repo/main.py`<br/>• `repo/app_ui.py`<br/>• `repo/tests/test_image_posting.py` | <span style="color:green">✅ HOÀN THÀNH (15/15 Unit Tests PASS)</span> |
| **GUI V2** | **Desktop GUI v2.0 Độc Lập (Canva 12 Screens)**<br/>• Kiến trúc 3 Tab chuẩn đặc tả `Facebook_Automation_UI_UX_v1.docx`<br/>• Quản lý Đa bài viết & Bộ sưu tập ảnh riêng (Multi-Post)<br/>• Phân bổ luân phiên Round-Robin (`posting_plan.py`)<br/>• Chế độ Sáng / Tối (`ui_theme.py`)<br/>• Hộp thoại Dừng an toàn (Graceful Stop Confirmation)<br/>• Nút Đăng nhập Facebook & Quét Info nhóm kế thừa từ GUI cũ | • `repo/app_ui_v2.py`<br/>• `run_gui_v2.sh`<br/>• `repo/services/posting_plan.py`<br/>• `repo/ui_theme.py`<br/>• `repo/tests/test_posting_plan.py`<br/>• `repo/tests/test_gui_v2.py` | <span style="color:green">✅ HOÀN THÀNH (24/24 Unit Tests PASS)</span> |
| **MOD-J1** | **Group Joiner: Selectors & Membership Verifier**<br/>• Bổ sung selectors `join_group_button`, `joined_indicator`, `join_pending_indicator`<br/>• Lớp `GroupMembershipVerifier` (Read-only CQS inspection)<br/>• Enum `MembershipStatus` (5 trạng thái)<br/>• Bộ kiểm thử độc lập `test_group_verifier.py` (Mock 100%) | • `repo/json/selectors.json`<br/>• `repo/services/group_verifier.py`<br/>• `repo/services/__init__.py`<br/>• `repo/tests/test_group_verifier.py` | <span style="color:green">✅ HOÀN THÀNH (8/8 Unit Tests PASS)</span> |
| **MOD-J2** | **Group Joiner: Join Action & Questionnaire Handler**<br/>• Bổ sung selectors `membership_questionnaire_dialog`, `rules_agreement_checkbox`, `question_text_input`, `submit_join_button`<br/>• Lớp `GroupJoiner` & `JoinResult` phân rã hàm con<br/>• Tự động tích quy tắc nhóm, điền câu trả lời mẫu, bấm submit & hậu kiểm<br/>• Bộ kiểm thử độc lập `test_group_joiner.py` (Mock 100%) | • `repo/json/selectors.json`<br/>• `repo/services/group_joiner.py`<br/>• `repo/services/__init__.py`<br/>• `repo/tests/test_group_joiner.py` | <span style="color:green">✅ HOÀN THÀNH (8/8 Unit Tests PASS)</span> |
| **MOD-J3** | **Group Joiner: Batch Orchestration & CLI**<br/>• Phương thức `FacebookBot.join_groups()`<br/>• Dynamic Jitter (Fast-delay 2-4s vs Safe-jitter 15-35s)<br/>• Hàm báo cáo tổng kết `print_join_summary_table()`<br/>• Tích hợp CLI `--join-groups`, `--join-limit`, `--dry-run`<br/>• Bộ kiểm thử độc lập `test_group_join_orchestrator.py` (Mock 100%) | • `repo/helpers/common_helpers.py`<br/>• `repo/facebookbot.py`<br/>• `repo/main.py`<br/>• `repo/tests/test_group_join_orchestrator.py` | <span style="color:green">✅ HOÀN THÀNH (6/6 Unit Tests PASS)</span> |
| **MOD-J4** | **Group Joiner: Desktop GUI Integration (Canva UI v2.0)**<br/>• Nút '🤝 Gia nhập nhóm' trên Screen 03 (Tab 02)<br/>• Worker thread nền `BotWorkerThreadV2` (Zero UI Freeze)<br/>• Cột huy hiệu thành viên trên Treeview (`Đã vào`, `Chờ duyệt`, `Chưa vào`)<br/>• Live logs & KPI metrics cập nhật thời gian thực trên Tab 03<br/>• Bộ kiểm thử độc lập `test_gui_join_integration.py` (Mock 100%) | • `repo/app_ui_v2.py`<br/>• `repo/facebookbot.py`<br/>• `repo/main.py`<br/>• `repo/tests/test_gui_join_integration.py` | <span style="color:green">✅ HOÀN THÀNH (6/6 Unit Tests PASS)</span> |
| **MOD-SESSION** | **Tổng Kết Theo Lần Đăng & Thời Gian Hoàn Thành**<br/>• Quản lý danh sách phiên (`sessions`) trong `history.json`<br/>• Thẻ tổng kết nổi bật `frame_session_card` hiển thị thời gian hoàn thành công việc<br/>• Dropdown `Chọn Lần Đăng:` lọc nhóm theo từng lần đăng hoặc xem tất cả<br/>• Hiển thị thời gian hoàn thành trên hộp thoại hoàn tất và Telegram<br/>• Bộ kiểm thử độc lập `test_session_summary.py` | • `repo/services/state_manager.py`<br/>• `repo/helpers/common_helpers.py`<br/>• `repo/facebookbot.py`<br/>• `repo/app_ui.py`<br/>• `repo/app_ui_v2.py`<br/>• `repo/services/telegram_controller.py`<br/>• `repo/tests/test_session_summary.py` | <span style="color:green">✅ HOÀN THÀNH (7/7 Unit Tests PASS)</span> |

---

## 🎯 CÁC MỐC ĐÃ ĐẠT ĐƯỢC (MILESTONES COMPLETED)

1. **Chuẩn hóa đường dẫn & Tương thích Cross-platform (Phần 1 & 4)**:
   - Sửa lỗi đường dẫn tương đối bằng `BASE_DIR = os.path.dirname(os.path.abspath(__file__))`.
   - Hỗ trợ phím tắt dán văn bản tự động nhận diện macOS (`COMMAND + ALT + SHIFT + V`) và Windows/Linux (`CONTROL + SHIFT + V`).
   - Lưu trữ bản prototype nguyên bản vào `repo/archive/main_legacy.py`.
2. **Thiết lập Quy chuẩn Dự án (GEMINI.md)**:
   - Đúc kết các nguyên tắc từ `CRMSiLe/GEMINI.md` sang đặc thù Python/Selenium Automation.
   - Bắt buộc tuân thủ 4 câu hỏi (*Làm gì, Vì sao làm, Ưu điểm, Best practices*) trước mỗi module.
3. **Phân rã Kiến trúc 5 Module & Sơ đồ Luồng Tổng thể**:
   - Hoàn thành tài liệu phân rã trong `pragmatic_production_upgrade_plan.md`.
   - Hoàn thành `ARCHITECTURE.md` với 2 sơ đồ Mermaid trực quan (System Flowchart & PostStatus State Machine).
4. **Lập phương án kỹ thuật chi tiết cho Module 1 (Foundation & Observability)**:
   - Subagent `Foundation Architect` đã hoàn thành bản thiết kế chi tiết và dự thảo mã nguồn cho `config.py`, `logger.py`, và 6 bài unit test trong `test_module1.py`.
5. **Hoàn thiện Tối ưu Kiến trúc theo Phản hồi Người dùng (Architectural Refinements)**:
   - Bổ sung nhánh Fast-Exit khi tất cả nhóm đều đang trong Cooldown 24h (tránh mở trình duyệt vô ích).
   - Chuẩn hoá quy tắc Circuit Breaker: Tự động reset bộ đếm lỗi `consecutive_failures = 0` khi gặp `SUCCESS` hoặc `PENDING_APPROVAL`.
   - Bổ sung bước kiểm tra sức khỏe phiên (Mid-Flight Session & Network Health Check) trước khi thao tác để phân biệt mất mạng/logout với lỗi bài đăng.
6. **Triển khai và Nghiệm thu Thành công Module 1 (Foundation & Observability)**:
   - Tạo [repo/config.py](file:///Users/teomoney/Documents/facebook_automation/repo/config.py) với `BotConfig` bất biến (@dataclass(frozen=True)), fail-fast validation, masking mật khẩu, và quản lý đường dẫn tuyệt đối.
   - Tạo [repo/helpers/logger.py](file:///Users/teomoney/Documents/facebook_automation/repo/helpers/logger.py) với dual handlers (Console + RotatingFileHandler 5MB UTF-8) và tiện ích chụp ảnh màn hình an toàn `capture_screenshot`.
   - Viết 7 bài unit test trong [repo/tests/test_module1.py](file:///Users/teomoney/Documents/facebook_automation/repo/tests/test_module1.py), kiểm thử độc lập 100% pass (Ran 7 tests in 0.003s, OK).
7. **Triển khai Bước 2.1 của Module 2 (Browser Automation Engine)**:
   - Tạo [repo/json/selectors.json](file:///Users/teomoney/Documents/facebook_automation/repo/json/selectors.json) chuẩn hóa 11 UI components dưới cấu trúc `Primary + Fallback List`.
   - Bảo toàn 100% selector cũ ở vị trí Primary (index 0) đảm bảo tương thích ngược, bổ sung các phương án Fallback đa ngôn ngữ (EN/VI).
   - Kiểm thử toàn vẹn dữ liệu thành công qua script kiểm định cú pháp JSON.
8. **Triển khai Bước 2.2 của Module 2 (Driver Lifecycle & Sanitation)**:
   - Tạo [repo/browser/driver_manager.py](file:///Users/teomoney/Documents/facebook_automation/repo/browser/driver_manager.py) với Context Manager `get_driver()` tuân thủ nguyên tắc Finish What You Start.
   - Cơ chế dọn dẹp port 9224 (`free_port`) và xóa stale lock (`clean_chrome_locks`) tự động trước khi mở và sau khi đóng trình duyệt.
   - Áp dụng CDP Stealth Patch (`Page.addScriptToEvaluateOnNewDocument`) ẩn `navigator.webdriver`.
   - Tạo [repo/browser/__init__.py](file:///Users/teomoney/Documents/facebook_automation/repo/browser/__init__.py) và viết 8 bài unit test trong [repo/tests/test_module2.py](file:///Users/teomoney/Documents/facebook_automation/repo/tests/test_module2.py) (8/8 test cases PASS).
9. **Triển khai Bước 2.3 của Module 2 (Safe DOM Actions & Native JS Text Injection)**:
   - Tạo [repo/browser/dom_actions.py](file:///Users/teomoney/Documents/facebook_automation/repo/browser/dom_actions.py) với cơ chế tìm kiếm đa tầng `find_element_with_fallback()` cảnh báo DOM Drift khi khớp fallback.
   - Thao tác click an toàn `safe_click()`: tự động scroll center, thử native click và fallback sang JavaScript click nếu bị overlay/modal chặn.
   - Bơm văn bản tự nhiên `inject_text_native_dom()`: dùng `document.execCommand('insertText')` và dispatch `InputEvent` + `Event('change')`, loại bỏ vĩnh viễn sự phụ thuộc vào clipboard hệ thống (`pyperclip`).
   - Mở rộng [repo/tests/test_module2.py](file:///Users/teomoney/Documents/facebook_automation/repo/tests/test_module2.py) thêm 16 bài unit test mới, nâng tổng số test của dự án lên **31/31 unit tests PASS** (0.019s).
10. **Triển khai Bước 2.4 & 2.5 của Module 2 (DOM Health Inspector & Nghiệm thu toàn diện Module 2)**:
    - Tạo [repo/browser/dom_inspector.py](file:///Users/teomoney/Documents/facebook_automation/repo/browser/dom_inspector.py) với `DOMStatus` / `ElementHealth` (`PASS`, `WARN`, `BROKEN`), `ComponentHealth`, `DOMHealthReport` (format bảng ASCII/Markdown), và class `DOMInspector`.
    - Hỗ trợ chụp ảnh màn hình và trích xuất HTML snapshot tự động vào `logs/dom_drift/` khi phát hiện selector bị `BROKEN`.
    - Cập nhật [repo/browser/__init__.py](file:///Users/teomoney/Documents/facebook_automation/repo/browser/__init__.py) export đầy đủ các thành phần của Module 2.
    - Bổ sung 9 bài unit test cho `DOMInspector` trong [repo/tests/test_module2.py](file:///Users/teomoney/Documents/facebook_automation/repo/tests/test_module2.py). Toàn bộ **40/40 unit tests của dự án (Module 1 & 2) đều PASS 100%** (0.019s).
11. **Triển khai và Nghiệm thu Thành công Module 3 (State & History Manager)**:
    - Tạo [repo/services/state_manager.py](file:///Users/teomoney/Documents/facebook_automation/repo/services/state_manager.py) với `StateManager`, `PostRecord`, ghi file atomic chống corrupt.
    - Lọc Cooldown 24h, cắt Batching an toàn (5–10 bài), cơ chế Fast-Exit khi tất cả nhóm đang trong cooldown.
    - 13/13 bài unit test độc lập trong [repo/tests/test_module3.py](file:///Users/teomoney/Documents/facebook_automation/repo/tests/test_module3.py) PASS 100%.
12. **Triển khai và Nghiệm thu Thành công Module 4 (Post Verification & Circuit Breaker)**:
    - Tạo [repo/services/post_verifier.py](file:///Users/teomoney/Documents/facebook_automation/repo/services/post_verifier.py) với `PostStatus` Enum (5 trạng thái), `PostVerificationResult` dataclass bất biến, và `PostVerifier` (kiểm tra DOM sau submit, phát hiện Action Blocked, Pending Approval, Modal closure, Marketplace form, tự động chụp screenshot).
    - Tạo [repo/services/circuit_breaker.py](file:///Users/teomoney/Documents/facebook_automation/repo/services/circuit_breaker.py) với `CircuitBreakerState` Enum, ngoại lệ `CircuitBreakerTriggeredError`, và `CircuitBreaker` (ngắt mạch khẩn cấp khi gặp `ACTION_BLOCKED` hoặc `failures >= 3`, tự động reset counter khi có `SUCCESS`/`PENDING_APPROVAL`, bỏ qua `MARKETPLACE_FORM`).
    - Cập nhật [repo/services/__init__.py](file:///Users/teomoney/Documents/facebook_automation/repo/services/__init__.py) export đầy đủ.
    - Viết 16 bài unit test trong [repo/tests/test_module4.py](file:///Users/teomoney/Documents/facebook_automation/repo/tests/test_module4.py). Toàn bộ **69/69 unit tests của toàn bộ dự án đều PASS 100%** (0.135s).

---

## 🚀 HÀNG MỤC TIẾP THEO (NEXT ACTIONS)

- [x] **Thực thi Module 1**: Tạo `repo/config.py`, `repo/helpers/logger.py`, `repo/tests/test_module1.py`.
- [x] **Chạy nghiệm thu Module 1**: Chạy `python -m unittest repo/tests/test_module1.py` xác nhận 7/7 test case pass.
- [x] **Bước 2.1 (Module 2)**: Tạo `repo/json/selectors.json` (Registry selector đa tầng dự phòng).
- [x] **Bước 2.2 (Module 2)**: Thiết kế `repo/browser/driver_manager.py` (quét port 9224, xoá `SingletonLock`, context manager).
- [x] **Bước 2.3 (Module 2)**: Thiết kế `repo/browser/dom_actions.py` (chèn văn bản qua JS Native Event thay thế `pyperclip`, Safe Click, Fallback Resolver).
- [x] **Bước 2.4 (Module 2)**: Thiết kế `repo/browser/dom_inspector.py` & cập nhật `repo/browser/__init__.py`.
- [x] **Bước 2.5 (Module 2)**: Nghiệm thu tổng thể Module 2 (40/40 Unit Tests PASS).
- [x] **MODULE 3: State & History Manager** (`repo/services/state_manager.py`, `history.json`, Cooldown 24h, Batching limit, Fast-Exit, 13/13 Tests PASS).
- [x] **MODULE 4: Post Verification & Circuit Breaker** (`repo/services/post_verifier.py`, `repo/services/circuit_breaker.py`, 16/16 Tests PASS).
- [x] **MODULE 5: Bot Orchestrator & CLI Integration** (`repo/facebookbot.py`, `repo/main.py`, `repo/tests/test_module5.py`, 18/18 Tests PASS).
- [x] **PRODUCTION HARDENING PLAN**: Đạt chuẩn sản xuất theo `PRODUCTION_READINESS.md` (`post_verifier.py`, `state_manager.py`, `driver_manager.py`, `facebookbot.py`, `main.py`, `requirements.txt`, `test_production_hardening.py`).
- [x] **TELEGRAM REMOTE CONTROL**: Service điều khiển từ xa qua Telegram (`repo/services/telegram_controller.py`, lệnh `/status`, `/start`, `/dryrun`, `/stop`, thông báo & upload ảnh screenshot, whitelist admin guard, `test_telegram.py`).
- [x] **DESKTOP GUI**: Giao diện Desktop đa tab (`repo/app_ui.py`, WorkerThread, Live Colored Console, quản lý `.env`, `links.json`, `posts.json` mà không cần mở file text, `test_gui.py`).
- [x] **CROSS-PLATFORM PACKAGING**: Cấu hình đóng gói PyInstaller (`facebook_bot.spec`), kịch bản `scripts/build_macos.sh`, `scripts/build_windows.bat`, tài liệu `docs/PACKAGING_GUIDE.md`.
- [x] **POST WITH IMAGES (CLI & GUI)**: Hỗ trợ đăng bài kèm ảnh (`repo/images/`, `--images`, Tab 2 GUI Listbox, `dom_actions.attach_media_files`, 15/15 Tests PASS trong `test_image_posting.py`).
- [x] **DESKTOP GUI V2 ĐỘC LẬP**: Hoàn thành 100% GUI v2 (`run_gui_v2.sh`, `repo/app_ui_v2.py`, `posting_plan.py`, `ui_theme.py`, 24/24 Tests PASS trong `test_posting_plan.py` và `test_gui_v2.py`).
- [x] **CHROME SESSION PURGE & ACCOUNT SWITCHING (LOGOUT)**: Bổ sung tính năng đăng xuất tài khoản Facebook bằng cách xóa sạch session/cookies khỏi Chrome profile (`clear_chrome_session`, `clear_driver_session`, `bot.logout()`, CLI `--logout`, nút đăng xuất trên GUI v1 `app_ui.py` và GUI v2 `app_ui_v2.py`).
- [x] **MEMBERSHIP INSPECTION ON GROUP SCAN**: Tích hợp `GroupMembershipVerifier` vào luồng quét thông tin nhóm của `app_ui.py`, `app_ui_v2.py` và `facebookbot.py`. Tự động nhận diện và cập nhật trạng thái gia nhập nhóm (`ALREADY_JOINED`, `PENDING_APPROVAL`, `NOT_JOINED`, `ACTION_BLOCKED`) hiển thị trực tiếp trên giao diện và lưu đồng bộ vào `links.json` & `groups_meta.json`.
- [x] **BUGFIX BOTWORKERTHREAD JOIN_MODE**: Bổ sung tham số `join_mode`, `post_content`, `posting_tasks` và nhánh gọi `bot.join_groups()` trong `BotWorkerThread` ([repo/app_ui.py](file:///Users/teomoney/Documents/facebook_automation/repo/app_ui.py)). Khắc phục triệt để lỗi khi bấm nút 'Tham gia nhóm' trên GUI v1.
- [x] **TOÀN DỰ ÁN**: 195/195 Unit Tests PASS, Zero Regression.


---

## 🤖 ĐỘI NGŨ SUB-AGENTS CHUYÊN TRÁCH

- **Foundation Architect** (`d098ac74-5f12-4958-8833-324cb2b9bff5`): Chịu trách nhiệm cấu hình, hạ tầng và quản lý tài nguyên.
- **Workflow & Architecture Specialist** (`5c259d51-9b14-44d8-9b55-66f7ca89e32b`): Chịu trách nhiệm luồng vận hành tổng thể, kịch bản biên và tương thích môi trường.
- **Code Quality & Production Reviewer** (`f4d3b620-c76c-4903-87ef-83df76bb544a`): Thanh tra độc lập review code, kiểm tra nguyên tắc Pragmatic/Ponytail và bảo vệ an toàn tài khoản trước khi merge code.
*Cơ chế giao tiếp*: Các sub-agent hoạt động độc lập bất đồng bộ và trao đổi thông tin qua `send_message`, giữ luồng chính tinh gọn và không bị chờ đợi nghẽn mạch.
