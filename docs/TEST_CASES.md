# TÀI LIỆU CHI TIẾT BỘ TEST CASE (PYTEST SUITE)
**Hệ thống:** Facebook & Threads Embed Discord Bot  
**Framework kiểm thử:** `pytest >= 9.1`, `pytest-asyncio >= 1.4`  
**Tổng số test case:** **62 test cases** (v2.1: 46 cases, v2.2: 16 cases)  
**Thời gian thực thi:** ~1.10 giây  
**Trạng thái:** 100% Passed

---

## 1. CẤU TRÚC THƯ MỤC KIỂM THỬ

```text
Facebook_bot/
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   # Fixtures dùng chung (Ed25519 keypair, env mocking)
│   ├── test_fast_path.py             # [v2.1] Fast-Path HTML Regex & yt-dlp fallback (2 tests)
│   ├── test_helpers.py               # [v2.1] Format số, thời gian, text chunking, URL regex (25 tests)
│   ├── test_security_and_discord.py  # [v2.1] Chữ ký số Ed25519, CORS, Ping/Pong (12 tests)
│   ├── test_slash_command.py         # [v2.1] Slash command /fbembbed & Followup Webhooks (7 tests)
│   └── test_threads.py               # [v2.2] Slash command /threads, SSRF, Cache, Fallback (16 tests)
```

---

## 2. FIXTURES DÙNG CHUNG (`conftest.py`)

| Tên Fixture | Scope | Mục đích sử dụng |
| :--- | :--- | :--- |
| `ed25519_keypair` | `session` | Tự động sinh ngẫu nhiên một cặp khóa Ed25519 (`SigningKey` và `VerifyKey`) chuẩn mật mã học bằng thư viện `pynacl`. Cho phép kiểm thử việc ký và giải mã chữ ký Discord mà không cần dùng khóa thật từ Discord Portal. |
| `mock_env` | `function` | Giả lập các biến môi trường thiết yếu (`DISCORD_PUBLIC_KEY`, `APPLICATION_ID`, `BOT_TOKEN`) bằng `monkeypatch`, đảm bảo môi trường kiểm thử độc lập và an toàn tuyệt đối. |

---

## 3. DANH MỤC CHI TIẾT CÁC TEST CASES THEO PHIÊN BẢN

---

### PHẦN I: TEST CASES THUỘC PHIÊN BẢN 2.1.0 (46 TEST CASES)

#### 3.1. `tests/test_fast_path.py` (2 Test Cases - Version 2.1.0)
Kiểm tra luồng trích xuất video trực tiếp từ HTML/JSON và cơ chế fallback sang `yt_dlp`.

| STT | Tên Test Case | Mô tả chi tiết | Tiêu chí đánh giá (Assertion) |
| :---: | :--- | :--- | :--- |
| 1 | `test_fast_path_extracts_video_without_yt_dlp` | Giả lập HTML Facebook chứa thuộc tính `"playable_url"` và các trường thống kê tương tác. Đặt `yt_dlp` ở trạng thái unimported (`sys.modules["yt_dlp"] = None`). | - Lấy được chính xác link MP4 stream.<br>- `yt_dlp` không bị gọi hoặc import.<br>- Bóc tách đầy đủ likes, comments, shares, title. |
| 2 | `test_fallback_to_yt_dlp_when_fast_path_has_no_video_stream` | Giả lập HTML Facebook không chứa link stream trực tiếp (video bảo mật hoặc giao diện cũ). Hệ thống phải tự động fallback sang `yt_dlp`. | - Mock `yt_dlp.YoutubeDL` được gọi đúng cách.<br>- Dữ liệu video từ `yt_dlp` được gán chính xác vào kết quả trả về. |

---

#### 3.2. `tests/test_helpers.py` (25 Test Cases - Version 2.1.0)
Kiểm tra tính chính xác của các hàm bổ trợ xử lý dữ liệu người dùng và định dạng hiển thị Discord.

##### A. Nhóm kiểm thử `_format_count` (5 Tests)
Chuyển đổi số lượng tương tác sang định dạng gọn đẹp trên giao diện Discord:
* `test_none_and_empty`: Giá trị `None` hoặc rỗng trả về đúng giá trị gốc.
* `test_numbers_under_thousand`: Số < 1,000 (ví dụ: `0`, `42`, `999`, `"500"`) giữ nguyên dạng chuỗi số.
* `test_numbers_thousands`: Số hàng nghìn (ví dụ: `1000` -> `"1K"`, `1250` -> `"1.2K"`, `"41,200"` -> `"41.2K"`).
* `test_numbers_millions`: Số hàng triệu (ví dụ: `1,000,000` -> `"1M"`, `5,700,000` -> `"5.7M"`).
* `test_non_numeric_fallback`: Dữ liệu chuỗi không thể parse số (ví dụ: `"N/A"`) không gây crash và trả về chuỗi gốc.

##### B. Nhóm kiểm thử `_format_time_ago` (6 Tests)
Tính khoảng thời gian tương đối hiển thị ở footer bài viết:
* `test_none_or_empty`: Xử lý an toàn khi timestamp rỗng.
* `test_just_now`: Khoảng cách dưới 60 giây hiển thị `"Vừa xong"`.
* `test_minutes_ago`: Khoảng cách dưới 60 phút hiển thị `"X phút trước"`.
* `test_hours_ago`: Khoảng cách dưới 24 giờ hiển thị `"X giờ trước"`.
* `test_days_ago`: Khoảng cách trên 24 giờ hiển thị `"X ngày trước"`.
* `test_yyyymmdd_string`: Chuyển đổi chuỗi ngày Facebook dạng `"20240101"` sang thời gian tương đối chính xác.

##### C. Nhóm kiểm thử `_chunk_text` (4 Tests)
Chia nhỏ nội dung bài viết dài không vượt quá giới hạn 4096 ký tự của Discord Embed:
* `test_empty_text`: Chuỗi rỗng trả về danh sách rỗng.
* `test_short_text`: Chuỗi ngắn hơn kích thước tối đa giữ nguyên trong 1 chunk.
* `test_split_by_double_newline`: Tách văn bản tại vị trí xuống dòng đoạn văn (`\n\n`), không làm gãy đoạn văn bản.
* `test_split_by_single_newline`: Tách văn bản tại vị trí xuống dòng đơn (`\n`) khi không có `\n\n`.

##### D. Nhóm kiểm thử `FB_URL_REGEX` (9 Tests)
Lọc và xác thực tính hợp lệ của đường link Facebook do người dùng cung cấp:
* `test_valid_fb_urls` (6 URLs): `facebook.com/watch`, `facebook.com/reel`, `m.facebook.com/story.php`, `fb.watch/...`, `facebook.com/permalink.php`, `web.facebook.com/...`.
* `test_invalid_urls` (3 URLs): domain ngoài whitelist (`google.com`, `youtube.com`, `fakebook.com`).
* `test_invalid_urls` (chuỗi ngẫu nhiên & chuỗi rỗng).

##### E. Nhóm kiểm thử `_build_stats_text` (1 Test)
* `test_build_stats_all_fields`: Kiểm tra tổng hợp chuỗi thống kê (Likes, Comments, Shares) và thanh điều hướng nguồn bài viết.

---

#### 3.3. `tests/test_security_and_discord.py` (12 Test Cases - Version 2.1.0)
Kiểm tra an toàn mật mã học và tuân thủ giao thức Discord Interaction HTTP Webhook.

##### A. Nhóm kiểm thử `_raw_body` (6 Tests)
* `test_plain_string_body`: Chuỗi thông thường giữ nguyên.
* `test_bytes_body`: Dữ liệu dạng `bytes` được decode utf-8 an toàn.
* `test_base64_encoded_body`: Giải mã chính xác khi API Gateway bật cờ `isBase64Encoded=True`.
* `test_empty_body`: Trả về chuỗi rỗng khi event không có body.

##### B. Nhóm kiểm thử `_verify` (3 Tests)
* `test_valid_signature`: Dùng `SigningKey` ký cặp `timestamp + body`. Hàm `_verify` phải trả về `(True, body)`.
* `test_invalid_signature`: Dùng chữ ký giả mạo 64 bytes ngẫu nhiên. Hàm phải từ chối `(False, body)`.
* `test_missing_headers`: Thiếu header `x-signature-ed25519` hoặc `x-signature-timestamp` phải bị từ chối ngay.

##### C. Nhóm kiểm thử `lambda_handler` Protocol (3 Tests)
* `test_cors_options`: Request HTTP method `OPTIONS` nhận ngay status 200 kèm các header CORS (`Access-Control-Allow-Origin: *`).
* `test_invalid_signature_returns_401`: Request không hợp lệ chữ ký bị chặn ở tầng đầu tiên với mã **401 Unauthorized**.
* `test_discord_ping_type_1_returns_pong`: Request Handshake PING (Type 1) từ Discord Developer Portal được phản hồi ngay PONG (`{"type": 1}`) với status 200.

---

#### 3.4. `tests/test_slash_command.py` (7 Test Cases - Version 2.1.0)
Kiểm tra quy trình tiếp nhận và xử lý lệnh Slash Command `/fbembbed`.

* `test_type_2_returns_deferred_type_5`: Khi nhận lệnh tương tác Type 2 từ Discord, bot phản hồi ngay lập tức **Type 5 (DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE)** trong < 100ms.
* `test_invalid_facebook_url_followup`: Khi người dùng nhập link không phải Facebook, bot gửi thông báo lỗi qua Webhook Followup: *"Link Facebook không hợp lệ"*.
* `test_video_payload_formatting`: Khi bài viết là video, bot tạo payload dạng content có nhãn `[▶️ Video](stream_url)` để kích hoạt trình phát HTML5 video player của Discord.
* `test_post_payload_formatting`: Khi bài viết là văn bản/ảnh, bot đóng gói vào cấu trúc Discord Rich Embed chuẩn màu Facebook `0x1877F2`.
* `test_async_background_task_event`: Kiểm tra router phân nhánh tác vụ nền: Khi event có cờ `async_task: "process_slash_command"`, Lambda chạy thẳng vào hàm xử lý nền mà không bị lặp lại bước xác thực Discord.
* `test_video_long_caption_splits_into_followups`: Video có caption dài (>2000 ký tự) được tự động chia thành nhiều message followup gửi nối tiếp.
* `test_send_followup_content_length_safety`: Bảo vệ an toàn độ dài payload webhook followup không vượt quá 2000 ký tự (tự động cắt và thêm dấu `...`).

---

### PHẦN II: TEST CASES THUỘC PHIÊN BẢN 2.2.0 (16 TEST CASES MỚI)

#### 3.5. `tests/test_threads.py` (16 Test Cases - Nâng cấp Version 2.2.0)
Kiểm tra toàn diện tính năng Fetch & Rich Embed bài viết Threads (Meta), cơ chế Layered Fallback, phòng thủ SSRF, cache TTL và Discord Cog.

##### A. Nhóm kiểm thử Bảo mật SSRF & Chuẩn hóa URL (2 Tests)
* `test_valid_urls`: Kiểm tra regex chấp nhận đầy đủ các định dạng URL hợp lệ của Threads (`@user/post/ID`, `t/ID`, domain `threads.net`, `threads.com`) và chuẩn hóa về canonical URL.
* `test_invalid_domains_ssrf_protection`: Thử nghiệm tiêm các URL độc hại, domain nội bộ (`169.254.169.254`, `localhost`, `threads.net.attacker.org`, `evil.com`). Tất cả đều bị chặn đứng và ném ngoại lệ `ThreadsInvalidURLError`.

##### B. Nhóm kiểm thử Data Fetcher & Layered Fallback Engine (4 Tests)
* `test_cache_hit_mechanism`: Kiểm tra cơ chế TTL Cache trong RAM. Khi URL đã có trong cache, hàm trả về ngay mà không phát sinh bất kỳ HTTP network request nào.
* `test_deleted_or_private_post_raises`: Giả lập phản hồi từ Threads khi bài viết bị xóa hoặc ở chế độ riêng tư (redirect về `?error=invalid_post` hoặc trả về trang Login). Hệ thống ném ngoại lệ chuẩn `ThreadsPostNotFound`.
* `test_layered_fallback_to_oembed`: Giả lập Layer 1 (Fast-Path Scrape) gặp sự cố mạng (403/Blocked). Hệ thống tự động chuyển sang Layer 2 (`graph.threads.net/oembed`) để lấy dữ liệu thành công.
* `test_layered_fallback_to_layer3_minimal`: Giả lập cả Layer 1 và Layer 2 đều thất bại. Hệ thống kích hoạt Layer 3 trả về `ThreadsPost` tối giản có cờ `is_fallback=True` kèm link bài viết gốc.

##### C. Nhóm kiểm thử Giao diện Discord Embed & Action View (6 Tests)
* `test_text_only_post`: Kiểm tra bài viết chỉ có chữ tạo đúng 1 Embed với màu thương hiệu `#101010`, tên tác giả `@handle`, footer "Threads" và 2 nút bấm điều hướng (`🔗 Xem bài viết gốc`, `👤 Xem trang cá nhân`).
* `test_single_image_post`: Kiểm tra bài viết có 1 ảnh được gán trực tiếp vào `embed.set_image`.
* `test_carousel_multiple_images`: Kiểm tra bài viết có nhiều ảnh (6 ảnh) được chia thành 4 Embeds cùng URL (tạo layout lưới ảnh Discord Image Gallery Grid) và footer ghi chú `"+2 ảnh khác"`.
* `test_video_post`: Kiểm tra bài viết có video sử dụng ảnh thumbnail làm ảnh chính kèm dòng chú thích `🎥 Bài viết có video — nhấn nút bên dưới để xem`.
* `test_description_truncation`: Kiểm tra văn bản quá dài (>900 ký tự) được cắt theo ranh giới từ và thêm hyperlink `… [xem đầy đủ](url)`.
* `test_build_payload_dict_for_api`: Kiểm tra hàm chuyển đổi sang cấu trúc REST API payload (embeds + action row components) phục vụ môi trường serverless.

##### D. Nhóm kiểm thử Discord.py Cog & Non-blocking Concurrency (4 Tests)
* `test_cog_invalid_url_ephemeral`: Người dùng nhập link không hợp lệ nhận ngay phản hồi ephemeral báo lỗi và không gửi tiếp lệnh tới Discord.
* `test_cog_successful_flow`: Luồng hoàn chỉnh của lệnh slash command: gọi `defer(thinking=True)` ngay lập tức, sau đó gửi embeds và view qua `followup.send`.
* `test_cog_deleted_post_ephemeral_followup`: Khi bài viết bị xóa/private, bot phản hồi thông báo lịch sự qua `followup.send(..., ephemeral=True)`.
* `test_concurrent_fetching_non_blocking`: Giả lập 10 lệnh `/threads` chạy đồng thời bằng `asyncio.gather`. Toàn bộ tác vụ hoàn thành trong thời gian tương đương 1 tác vụ đơn lẻ (<0.35s), chứng minh không làm block event loop của Discord bot.

---

## 4. HƯỚNG DẪN CHẠY KIỂM THỬ

Tại thư mục gốc dự án, mở Terminal (PowerShell) và chạy:

```powershell
# Chạy toàn bộ 62 test cases (cả v2.1 và v2.2)
.\.venv\Scripts\pytest -v tests

# Chạy riêng bộ test mới cho Threads (Version 2.2.0)
.\.venv\Scripts\pytest -v tests/test_threads.py

# Chạy riêng nhóm kiểm thử Fast-path Facebook (Version 2.1.0)
.\.venv\Scripts\pytest -v tests/test_fast_path.py
```

### Kết quả đầu ra kiểm thử:
```text
============================= test session starts =============================
platform win32 -- Python 3.13.14, pytest-9.1.1, pluggy-1.6.0
plugins: asyncio-1.4.0
collected 62 items

tests/test_fast_path.py::TestFastPathExtraction::test_fast_path_extracts_video_without_yt_dlp PASSED [  1%]
...
tests/test_threads.py::TestThreadsCog::test_concurrent_fetching_non_blocking PASSED [100%]

============================= 62 passed in 1.16s ==============================
```
