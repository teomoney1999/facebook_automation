# Đánh giá mức độ sẵn sàng cho production

Ngày đánh giá: 21/11/2026.

## Kết luận

Codebase đã có cấu trúc rõ và nhiều cơ chế bảo vệ, nhưng **chưa đủ điều kiện chạy production tự động, không cần giám sát**. Hướng phù hợp hiện tại là hoàn thiện thành công cụ nội bộ chạy một tài khoản, một tiến trình, rồi kiểm chứng độ tin cậy trước khi mở rộng.

Đánh giá dựa trên việc đọc phần mã đang hoạt động, chạy **86/86 test thành công**, và kiểm tra bổ sung bằng mock cùng dữ liệu tạm. Một số lỗi quan trọng vẫn tái hiện dù bộ test hiện tại đều đạt.

## Những nền tảng nên giữ lại

- Tách trách nhiệm giữa điều phối, thao tác trình duyệt, quản lý trạng thái, xác minh kết quả và circuit breaker.
- Cấu hình có validation và che mật khẩu trong biểu diễn đối tượng.
- Selector được đưa ra JSON và có cơ chế dự phòng.
- Có log xoay vòng, screenshot hỗ trợ chẩn đoán.
- Lịch sử được ghi qua file tạm rồi thay thế nguyên tử.
- Có cooldown theo nhóm, giới hạn batch và dừng khi lỗi liên tiếp hoặc nhận diện bị chặn.

## Các vấn đề cần xử lý & Trạng thái đã khắc phục

| Mức ưu tiên | Vấn đề ban đầu | Giải pháp kỹ thuật đã triển khai | Trạng thái |
|---|---|---|:---:|
| **Chặn production** | **Có thể báo đăng thành công khi trình duyệt mất kết nối.** `_is_modal_closed()` nuốt `WebDriverException`. | Bắt `WebDriverException` chuyển thành `PostStatus.UNKNOWN`, không báo `SUCCESS` giả khi rớt mạng. | <span style="color:green">✅ ĐÃ KHẮC PHỤC</span> |
| **Chặn production** | **Lịch sử chưa bảo vệ được việc chạy lại.** Hai tiến trình chạy đè nhau hoặc JSON hỏng bị reset trắng. | `ProcessLock` qua `fcntl.flock` khóa đơn tiến trình; `is_corrupted` cảnh báo thay vì chạy đè. | <span style="color:green">✅ ĐÃ KHẮC PHỤC</span> |
| **Chặn production** | **Trạng thái bị chặn không tồn tại qua lần khởi động mới.** `ACTION_BLOCKED` chỉ nằm trong RAM. | `mark_account_blocked` & `is_account_blocked` lưu hạn phạt vào JSON; bot dừng ngay khi khởi động. | <span style="color:green">✅ ĐÃ KHẮC PHỤC</span> |
| **Chặn production** | **Có thể tắt nhầm tiến trình trên máy.** `free_port()` kill PID bất kỳ. | Kiểm tra `_is_chrome_process(pid)` qua `ps` trước khi gửi tín hiệu kill, từ chối can thiệp app khác. | <span style="color:green">✅ ĐÃ KHẮC PHỤC</span> |
| **Cao** | **CLI báo thành công khi nghiệp vụ thất bại.** `main()` luôn trả `0`. | Trả exit codes chuẩn Unix: `0` (xong), `1` (thất bại), `2` (bị Facebook chặn), `3` (cần đăng nhập). | <span style="color:green">✅ ĐÃ KHẮC PHỤC</span> |
| **Cao** | **Validation đầu vào còn thiếu.** `--batch-size -1` được chấp nhận; query param bypass cooldown. | Chặn `batch_size <= 0` ở cả CLI và service; `normalize_url` lọc query `?`, fragment `#` và domain. | <span style="color:green">✅ ĐÃ KHẮC PHỤC</span> |
| **Cao** | **Đăng nhập chưa phải điều kiện bắt buộc để tiếp tục.** | Kiểm tra `check_login_status`, dừng ngay với mã `NEEDS_LOGIN` (exit code 3), không spam nhóm. | <span style="color:green">✅ ĐÃ KHẮC PHỤC</span> |
| **Trung bình** | **DOM health check kiểm tra sai ngữ cảnh.** Báo modal bị hỏng trên trang feed tĩnh. | Thêm `SITUATIONAL_COMPONENTS`; `--check-dom` chỉ kiểm tra các selector thường trực trên feed/home. | <span style="color:green">✅ ĐÃ KHẮC PHỤC</span> |
| **Bảo vệ rủi ro** | **Chống đăng trùng khi bot chết giữa chừng.** | Ghi nhận trạng thái `IN_PROGRESS` trước khi nhấn nút Post; `IN_PROGRESS` kích hoạt cooldown 24h. | <span style="color:green">✅ ĐÃ KHẮC PHỤC</span> |
| **Vận hành** | **Khóa version dependency.** | Khóa phiên bản cụ thể cho `selenium`, `webdriver_manager`, `python-dotenv` trong `requirements.txt`. | <span style="color:green">✅ ĐÃ KHẮC PHỤC</span> |

## Chống đăng trùng khi tiến trình chết giữa chừng

Hiện bot chỉ ghi lịch sử sau khi gửi bài và xác minh. Nếu Facebook đã nhận bài nhưng bot chết trước khi ghi, lần chạy sau có thể gửi lại. Ghi JSON nguyên tử không giải quyết được khoảng trống này.

Cần lưu một lần thực thi trước khi gửi, rồi đối soát các lần có kết quả chưa rõ. Không tự động gửi lại chỉ vì chưa nhận được xác nhận thành công. Chuyển sang cơ sở dữ liệu cũng không tự giải quyết được việc đồng bộ giữa thao tác trên Facebook và trạng thái nội bộ.

## Logging và tính chính xác của báo cáo

CLI cấu hình logger `MainRunner`, còn nhiều service dùng `FacebookBot.*`. Trong kiểm tra riêng, log của `StateManager` không đi vào file của runner. Cần thống nhất cấu hình logging để các sự kiện quan trọng của service được ghi đầy đủ.

`total_successful_posts` thực chất đếm **số nhóm có trạng thái cuối là thành công/chờ duyệt**, không phải tổng số bài thành công. Lịch sử hiện giữ trạng thái cuối của từng nhóm, chưa phải nhật ký đầy đủ của từng lần thực thi. Xem [state_manager.py](services/state_manager.py), dòng 254–280.

## Triển khai và bảo mật vận hành

Trong bản workspace được đánh giá:

- Dependency chưa khóa phiên bản trong [requirements.txt](requirements.txt).
- Chưa thấy CI, runbook hoặc cấu hình triển khai tái lập.
- Chưa có cấu hình headless hay timeout tải trang riêng.
- `.env` có quyền `0644`; profile trình duyệt và dữ liệu chạy nằm cùng thư mục mã nguồn.
- Chrome luôn bật `--no-sandbox` và `--remote-allow-origins=*`; cần rà soát theo môi trường triển khai.

Không có bằng chứng trong lần đánh giá này để kết luận secrets từng bị đưa lên Git.

## Giới hạn của bộ test hiện tại

**86 test đạt là nền tảng hữu ích, nhưng chưa chứng minh luồng đăng thật hoạt động đúng.** Phần tương tác trình duyệt chủ yếu dùng mock.

Ví dụ, `test_verify_success_when_modal_closed` trong [test_module4.py](tests/test_module4.py), dòng 143–151, coi DOM không có phần tử nào là `SUCCESS`, nên vẫn chấp nhận giả định gây lỗi xác minh nêu trên.

Cần bổ sung kiểm tra cho mất kết nối trình duyệt, kết quả gửi bài chưa rõ, restart sau khi bị chặn, ghi trạng thái đồng thời, dữ liệu lịch sử hỏng, đầu vào không hợp lệ và mã thoát của CLI. Kiểm thử trình duyệt nên bao gồm lỗi DOM, timeout, mất session và phản hồi chậm.

## Thứ tự cải thiện để đạt điều kiện production

1. **Làm đúng kết quả và hành vi dừng:** sửa xác minh `SUCCESS`, thêm `UNKNOWN`, dừng khi mất đăng nhập, lưu trạng thái bị chặn, sửa exit code, validation và quản lý tiến trình Chrome. Bổ sung test cho các lỗi đã tái hiện.
2. **Làm bền việc chạy lại và vận hành:** với quy mô hiện tại, một worker cùng SQLite là hướng hợp lý. Lưu từng lần thực thi với tài khoản, nhóm, nội dung, thời điểm, trạng thái và bằng chứng kết quả. Chặn chạy đồng thời, đối soát trạng thái chưa rõ, hoàn thiện logging, cảnh báo, backup, khóa dependency và hướng dẫn xử lý sự cố.
3. **Pilot có kiểm soát:** kiểm thử trình duyệt với fixture trước, sau đó chạy thử có giám sát trên nhóm được phép. Chỉ mở rộng khi kết quả báo cáo khớp bài thực tế, restart không tự gửi lại bài chưa rõ trạng thái, và hệ thống giám sát nhận đúng lỗi.

## Phạm vi chưa kiểm chứng

Chưa chạy đăng bài thật hoặc kiểm chứng selector trên Facebook hiện tại. Các kết luận dựa vào mã nguồn, unit test và tình huống lỗi mô phỏng; giãn cách và các cờ stealth hiện có không bảo đảm tài khoản tránh bị chặn.

Các tham chiếu dòng tương ứng với mã nguồn tại thời điểm đánh giá và có thể thay đổi sau khi sửa code.
