# TÀI LIỆU CHI TIẾT BỘ TEST CASE (PYTEST SUITE)
**Hệ thống:** Facebook Embed Discord Bot  
**Framework kiểm thử:** `pytest >= 9.1`  
**Tổng số test case:** 44 test cases  
**Thời gian thực thi:** ~0.50 giây  
**Trạng thái:** 100% Passed

---

## 1. CẤU TRÚC THƯ MỤC KIỂM THỬ

```text
Facebook_bot/
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   # Fixtures dùng chung (Ed25519 keypair, env mocking)
│   ├── test_fast_path.py             # Kiểm thử Fast-Path HTML Regex & yt-dlp fallback
│   ├── test_helpers.py               # Kiểm thử các hàm định dạng, regex, chunking
│   ├── test_security_and_discord.py  # Kiểm thử xác thực chữ ký số Ed25519 & HTTP routing
│   └── test_slash_command.py         # Kiểm thử xử lý lệnh /fbembbed & gửi Followup Webhook
```

---

## 2. FIXTURES DÙNG CHUNG (`conftest.py`)

| Tên Fixture | Scope | Mục đích sử dụng |
| :--- | :--- | :--- |
| `ed25519_keypair` | `session` | Tự động sinh ngẫu nhiên một cặp khóa Ed25519 (`SigningKey` và `VerifyKey`) chuẩn mật mã học bằng thư viện `pynacl`. Cho phép kiểm thử việc ký và giải mã chữ ký Discord mà không cần dùng khóa thật từ Discord Portal. |
| `mock_env` | `function` | Giả lập các biến môi trường thiết yếu (`DISCORD_PUBLIC_KEY`, `APPLICATION_ID`, `BOT_TOKEN`) bằng `monkeypatch`, đảm bảo môi trường kiểm thử độc lập và an toàn tuyệt đối. |

---

## 3. DANH MỤC CHI TIẾT CÁC TEST CASES

### 3.1. `tests/test_fast_path.py` (2 Test Cases)
Kiểm tra luồng trích xuất video trực tiếp từ HTML/JSON và cơ chế fallback sang `yt_dlp`.

| STT | Tên Test Case | Mô tả chi tiết | Tiêu chí đánh giá (Assertion) |
| :---: | :--- | :--- | :--- |
| 1 | `test_fast_path_extracts_video_without_yt_dlp` | Giả lập HTML Facebook chứa thuộc tính `"playable_url"` và các trường thống kê tương tác. Đặt `yt_dlp` ở trạng thái unimported (`sys.modules["yt_dlp"] = None`). | - Lấy được chính xác link MP4 stream.<br>- `yt_dlp` không bị gọi hoặc import.<br>- Bóc tách đầy đủ likes, comments, shares, title. |
| 2 | `test_fallback_to_yt_dlp_when_fast_path_has_no_video_stream` | Giả lập HTML Facebook không chứa link stream trực tiếp (video bảo mật hoặc giao diện cũ). Hệ thống phải tự động fallback sang `yt_dlp`. | - Mock `yt_dlp.YoutubeDL` được gọi đúng cách.<br>- Dữ liệu video từ `yt_dlp` được gán chính xác vào kết quả trả về. |

---

### 3.2. `tests/test_helpers.py` (25 Test Cases)
Kiểm tra tính chính xác của các hàm bổ trợ xử lý dữ liệu người dùng và định dạng hiển thị Discord.

#### A. Nhóm kiểm thử `_format_count` (5 Tests)
Chuyển đổi số lượng tương tác sang định dạng gọn đẹp trên giao diện Discord:
* `test_none_and_empty`: Giá trị `None` hoặc rỗng trả về đúng giá trị gốc.
* `test_numbers_under_thousand`: Số < 1,000 (ví dụ: `0`, `42`, `999`, `"500"`) giữ nguyên dạng chuỗi số.
* `test_numbers_thousands`: Số hàng nghìn (ví dụ: `1000` -> `"1K"`, `1250` -> `"1.2K"`, `"41,200"` -> `"41.2K"`).
* `test_numbers_millions`: Số hàng triệu (ví dụ: `1,000,000` -> `"1M"`, `5,700,000` -> `"5.7M"`).
* `test_non_numeric_fallback`: Dữ liệu chuỗi không thể parse số (ví dụ: `"N/A"`) không gây crash và trả về chuỗi gốc.

#### B. Nhóm kiểm thử `_format_time_ago` (6 Tests)
Tính khoảng thời gian tương đối hiển thị ở footer bài viết:
* `test_none_or_empty`: Xử lý an toàn khi timestamp rỗng.
* `test_just_now`: Khoảng cách dưới 60 giây hiển thị `"Vừa xong"`.
* `test_minutes_ago`: Khoảng cách dưới 60 phút hiển thị `"X phút trước"`.
* `test_hours_ago`: Khoảng cách dưới 24 giờ hiển thị `"X giờ trước"`.
* `test_days_ago`: Khoảng cách trên 24 giờ hiển thị `"X ngày trước"`.
* `test_yyyymmdd_string`: Chuyển đổi chuỗi ngày Facebook dạng `"20240101"` sang thời gian tương đối chính xác.

#### C. Nhóm kiểm thử `_chunk_text` (4 Tests)
Chia nhỏ nội dung bài viết dài không vượt quá giới hạn 4096 ký tự của Discord Embed:
* `test_empty_text`: Chuỗi rỗng trả về danh sách rỗng.
* `test_short_text`: Chuỗi ngắn hơn kích thước tối đa giữ nguyên trong 1 chunk.
* `test_split_by_double_newline`: Tách văn bản tại vị trí xuống dòng đoạn văn (`\n\n`), không làm gãy đoạn văn bản.
* `test_split_by_single_newline`: Tách văn bản tại vị trí xuống dòng đơn (`\n`) khi không có `\n\n`.

#### D. Nhóm kiểm thử `FB_URL_REGEX` (9 Tests)
Lọc và xác thực tính hợp lệ của đường link Facebook do người dùng cung cấp:
* `test_valid_fb_urls` (6 URLs):
  - `https://www.facebook.com/watch/?v=123456789` (Watch)
  - `https://facebook.com/reel/987654321` (Reels)
  - `https://m.facebook.com/story.php?story_fbid=111&id=222` (Mobile Story)
  - `https://fb.watch/abcdef123/` (Rút gọn)
  - `http://facebook.com/permalink.php?story_fbid=1&id=2` (Permalink)
  - `https://web.facebook.com/someuser/posts/123456` (Web Post)
* `test_invalid_urls` (3 URLs):
  - Link ngoài: `https://google.com`, `https://youtube.com/watch?v=123`
  - Tên miền giả: `https://fakebook.com/123`
  - Chuỗi văn bản ngẫu nhiên không phải URL.

#### E. Nhóm kiểm thử `_build_stats_text` (1 Test)
* `test_build_stats_all_fields`: Kiểm tra thanh thống kê `👍 Likes   💬 Comments   ↪️ Shares` và dòng nguồn `Facebook • Thời gian • [Link]`.

---

### 3.3. `tests/test_security_and_discord.py` (10 Test Cases)
Kiểm tra tính tuân thủ giao thức Discord Interactions và bảo mật chữ ký số Ed25519.

#### A. Nhóm kiểm thử `_raw_body` (4 Tests)
* `test_plain_string_body`: Trích xuất body dạng chuỗi thông thường.
* `test_bytes_body`: Giải mã body dạng `bytes` UTF-8.
* `test_base64_encoded_body`: Giải mã body bị Base64 encode bởi API Gateway (`isBase64Encoded: True`).
* `test_empty_body`: Xử lý event không có body mà không văng ngoại lệ.

#### B. Nhóm kiểm thử `_verify` (3 Tests)
* `test_valid_signature`: Dùng `SigningKey` ký cặp `timestamp + body`. Hàm `_verify` phải trả về `(True, body)`.
* `test_invalid_signature`: Dùng chữ ký giả mạo 64 bytes ngẫu nhiên. Hàm phải từ chối `(False, body)`.
* `test_missing_headers`: Thiếu header `x-signature-ed25519` hoặc `x-signature-timestamp` phải bị từ chối ngay.

#### C. Nhóm kiểm thử `lambda_handler` Protocol (3 Tests)
* `test_cors_options`: Request HTTP method `OPTIONS` nhận ngay status 200 kèm các header CORS (`Access-Control-Allow-Origin: *`).
* `test_invalid_signature_returns_401`: Request không hợp lệ chữ ký bị chặn ở tầng đầu tiên với mã **401 Unauthorized**.
* `test_discord_ping_type_1_returns_pong`: Request Handshake PING (Type 1) từ Discord Developer Portal được phản hồi ngay PONG (`{"type": 1}`) với status 200.

---

### 3.4. `tests/test_slash_command.py` (7 Test Cases)
Kiểm tra quy trình tiếp nhận và xử lý lệnh Slash Command `/fbembbed`.

* `test_type_2_returns_deferred_type_5`: Khi nhận lệnh tương tác Type 2 từ Discord, bot phản hồi ngay lập tức **Type 5 (DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE)** trong < 100ms.
* `test_invalid_facebook_url_followup`: Khi người dùng nhập link không phải Facebook, bot gửi thông báo lỗi qua Webhook Followup: *"Link Facebook không hợp lệ"*.
* `test_video_payload_formatting`: Khi bài viết là video, bot tạo payload dạng content có nhãn `[▶️ Video](stream_url)` để kích hoạt trình phát HTML5 video player của Discord.
* `test_post_payload_formatting`: Khi bài viết là văn bản/ảnh, bot đóng gói vào cấu trúc Discord Rich Embed chuẩn màu Facebook `0x1877F2`.
* `test_async_background_task_event`: Kiểm tra router phân nhánh tác vụ nền: Khi event có cờ `async_task: "process_slash_command"`, Lambda chạy thẳng vào hàm xử lý nền mà không bị lặp lại bước xác thực Discord.

---

## 4. HƯỚNG DẪN CHẠY KIỂM THỬ

Tại thư mục gốc dự án, mở Terminal (PowerShell) và chạy:

```powershell
# Chạy toàn bộ 44 test cases
.\.venv\Scripts\pytest -v tests

# Chạy riêng nhóm kiểm thử Fast-path
.\.venv\Scripts\pytest -v tests/test_fast_path.py

# Chạy riêng nhóm kiểm thử bảo mật & Discord protocol
.\.venv\Scripts\pytest -v tests/test_security_and_discord.py
```

### Kết quả đầu ra mẫu:
```text
============================= test session starts =============================
collected 44 items

tests/test_fast_path.py::TestFastPathExtraction::test_fast_path_extracts_video_without_yt_dlp PASSED [  2%]
tests/test_fast_path.py::TestFastPathExtraction::test_fallback_to_yt_dlp_when_fast_path_has_no_video_stream PASSED [  4%]
...
tests/test_slash_command.py::TestSlashCommandHandling::test_async_background_task_event PASSED [100%]

============================= 44 passed in 0.50s ==============================
```
