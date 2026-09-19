"""
TEST SUITE: BLACK-BOX TESTING (HỘP ĐEN)
Dự án: Discord Facebook & Threads Embed Bot
Mục tiêu: Kiểm thử dựa trên đặc tả yêu cầu (Specification-Based Testing)
Kỹ thuật áp dụng:
  1. Phân vùng tương đương (Equivalence Partitioning - EP)
  2. Phân tích giá trị biên (Boundary Value Analysis - BVA)
  3. Bảng quyết định (Decision Table Testing - DT)
  4. Kiểm thử chuyển trạng thái (State Transition Testing - ST)
  5. Đoán lỗi (Error Guessing - EG)
"""

import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

# Các module được kiểm thử
from main import (
    _format_count,
    _format_time_ago,
    _chunk_text,
    _build_stats_text,
    FB_URL_REGEX,
    lambda_handler,
    _process_slash_command,
)
from threads_fetcher import (
    ThreadsFetcher,
    ThreadsPost,
    ThreadsInvalidURLError,
    ThreadsPostNotFound,
    THREADS_POST_REGEX,
)
from threads_embed_builder import (
    build_threads_embeds,
    build_threads_payload_dict,
    truncate_description,
    MAX_GALLERY_IMAGES,
)


# ==============================================================================
# 1. PHÂN VÙNG TƯƠNG ĐƯƠNG (EQUIVALENCE PARTITIONING - EP)
# ==============================================================================
class TestEquivalencePartitioning:
    """
    Kỹ thuật EP chia miền đầu vào thành các lớp tương đương (Valid & Invalid).
    Mỗi lớp chọn 1 đại diện kiểm thử thay vì vét cạn vô tận.
    """

    # --- EP-01: Facebook URL Validation ---
    # Phân vùng Hợp lệ: Standard Reel, Watch URL, Mobile story, Permalinks, Shortlink fb.watch
    @pytest.mark.parametrize(
        "valid_url",
        [
            "https://www.facebook.com/reel/123456789",  # Partition 1: Standard Reel
            "https://facebook.com/watch/?v=987654321",  # Partition 2: Watch page
            "https://m.facebook.com/story.php?story_fbid=111&id=222",  # Partition 3: Mobile web
            "https://fb.watch/xYz123AbC/",  # Partition 4: Short domain
            "https://web.facebook.com/user/posts/1000",  # Partition 5: Subdomain web
        ],
    )
    def test_ep_valid_facebook_urls(self, valid_url):
        assert FB_URL_REGEX.match(valid_url) is not None

    # Phân vùng Không hợp lệ: Tên miền khác, URL không đúng cấu trúc, Chuỗi rỗng
    @pytest.mark.parametrize(
        "invalid_url",
        [
            "https://google.com/search?q=fb",  # Partition: Domain ngoài whitelist
            "https://fakefacebook.com/reel/123",  # Partition: Phishing / Fake domain
            "https://youtube.com/watch?v=123",  # Partition: Video nền tảng khác
            "not_a_url_at_all",  # Partition: Plain text
            "",  # Partition: Empty string
        ],
    )
    def test_ep_invalid_facebook_urls(self, invalid_url):
        assert FB_URL_REGEX.match(invalid_url) is None

    # --- EP-02: Threads URL Validation & SSRF Whitelist ---
    @pytest.mark.parametrize(
        "valid_threads_url",
        [
            "https://www.threads.net/@user/post/CuZsgfWLyiI",  # Partition: Canonical net
            "https://threads.net/@zuck/post/123456789",  # Partition: Apex net
            "https://www.threads.com/@user/post/CuZsgfWLyiI",  # Partition: Canonical com
            "https://threads.net/t/CuZsgfWLyiI",  # Partition: Shortlink /t/
            "https://www.threads.net/@user/post/CuZsgfWLyiI?xmt=AQG",  # Partition: URL with Query params
        ],
    )
    def test_ep_valid_threads_urls(self, valid_threads_url):
        fetcher = ThreadsFetcher()
        canonical, handle, post_id = fetcher.validate_and_normalize_url(valid_threads_url)
        assert canonical is not None
        assert post_id == "CuZsgfWLyiI" or post_id == "123456789"

    @pytest.mark.parametrize(
        "ssrf_dangerous_url",
        [
            "http://169.254.169.254/latest/meta-data/",  # Partition: AWS Instance Metadata
            "http://localhost:8080/admin",  # Partition: Loopback interface
            "http://127.0.0.1/flag",  # Partition: IPv4 Loopback
            "https://threads.net.attacker.com/post/123",  # Partition: Subdomain hijack attempt
            "https://evil.org/threads.net/@user/post/123",  # Partition: Path confusion
        ],
    )
    def test_ep_invalid_threads_ssrf_protection(self, ssrf_dangerous_url):
        fetcher = ThreadsFetcher()
        with pytest.raises(ThreadsInvalidURLError):
            fetcher.validate_and_normalize_url(ssrf_dangerous_url)

    # --- EP-03: Format Count Partitions ---
    def test_ep_format_count_partitions(self):
        # Partition 1: Null / Empty -> Giữ nguyên
        assert _format_count(None) is None
        assert _format_count("") == ""

        # Partition 2: Dưới 1,000 (Đơn vị cơ sở)
        assert _format_count(0) == "0"
        assert _format_count(750) == "750"

        # Partition 3: Hàng nghìn (1K - 999.9K)
        assert _format_count(1500) == "1.5K"
        assert _format_count(25000) == "25K"

        # Partition 4: Hàng triệu (>= 1M)
        assert _format_count(2000000) == "2M"
        assert _format_count(4500000) == "4.5M"

        # Partition 5: Chuỗi không thể parse số -> fallback chuỗi gốc an toàn
        assert _format_count("Unknown") == "Unknown"


# ==============================================================================
# 2. PHÂN TÍCH GIÁ TRỊ BIÊN (BOUNDARY VALUE ANALYSIS - BVA)
# ==============================================================================
class TestBoundaryValueAnalysis:
    """
    Kỹ thuật BVA tập trung vào các điểm ranh giới nơi xác suất xảy ra lỗi cao nhất.
    Bộ giá trị: {Min-1, Min, Min+1, Nom, Max-1, Max, Max+1}
    """

    # --- BVA-01: Ngưỡng chuyển đổi số đếm (999 -> 1,000 & 999,999 -> 1,000,000) ---
    def test_bva_count_formatting_boundaries(self):
        # Ranh giới 1,000:
        assert _format_count(999) == "999"  # Max của vùng < 1K
        assert _format_count(1000) == "1K"  # Min của vùng hàng nghìn (1K)
        assert _format_count(1001) == "1K"  # Min + 1

        # Ranh giới 1,000,000:
        assert _format_count(999900) == "999.9K"  # Cận trên hàng nghìn
        assert _format_count(1000000) == "1M"  # Min hàng triệu
        assert _format_count(1000001) == "1M"  # Min + 1

    # --- BVA-02: Giới hạn độ dài tin nhắn Discord (2000 ký tự) ---
    def test_bva_discord_message_length_safety(self, monkeypatch):
        """
        Discord giới hạn content tối đa 2000 ký tự.
        Hàm gửi followup phải tự động truncate an toàn nếu vượt quá 2000 ký tự.
        """
        from main import _send_followup
        import main

        monkeypatch.setattr(main, "APPLICATION_ID", "mock_app_id")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "{}"

        with patch("requests.patch", return_value=mock_resp) as mock_patch:
            # Biên 1: 1999 ký tự (Hợp lệ, không bị cắt)
            content_1999 = "A" * 1999
            _send_followup("tok", {"content": content_1999})
            sent_payload_1 = mock_patch.call_args[1]["json"]
            assert len(sent_payload_1["content"]) == 1999

            # Biên 2: 2000 ký tự (Chính xác Max, không bị cắt)
            content_2000 = "B" * 2000
            _send_followup("tok", {"content": content_2000})
            sent_payload_2 = mock_patch.call_args[1]["json"]
            assert len(sent_payload_2["content"]) == 2000

            # Biên 3: 2001 ký tự (Max + 1, phải bị cắt thành <= 2000 và có dấu ...)
            content_2001 = "C" * 2001
            _send_followup("tok", {"content": content_2001})
            sent_payload_3 = mock_patch.call_args[1]["json"]
            assert len(sent_payload_3["content"]) <= 2000
            assert sent_payload_3["content"].endswith("...")

    # --- BVA-03: Giới hạn số lượng ảnh trong Discord Embed Gallery Grid (Max = 4) ---
    def test_bva_carousel_gallery_grid_boundaries(self):
        """
        Discord chỉ cho phép hiển thị layout lưới ảnh khi có tối đa 4 embeds chung URL.
        Biên kiểm tra: 0 ảnh, 1 ảnh, 4 ảnh (Max Grid), 5 ảnh (Max + 1 -> hiển thị notice '+1 ảnh khác').
        """
        post_url = "https://www.threads.net/@artist/post/art123"

        # Case 1: 0 ảnh (Text-only)
        post_0 = ThreadsPost("Artist", "artist", None, "Text", [], post_url=post_url)
        embeds_0, _ = build_threads_embeds(post_0)
        assert len(embeds_0) == 1
        assert embeds_0[0].image.url is None

        # Case 2: 1 ảnh (Đơn)
        post_1 = ThreadsPost("Artist", "artist", None, "One", ["https://cdn.com/1.jpg"], post_url=post_url)
        embeds_1, _ = build_threads_embeds(post_1)
        assert len(embeds_1) == 1
        assert embeds_1[0].image.url == "https://cdn.com/1.jpg"

        # Case 3: 4 ảnh (Đúng ranh giới tối đa MAX_GALLERY_IMAGES)
        images_4 = [f"https://cdn.com/{i}.jpg" for i in range(4)]
        post_4 = ThreadsPost("Artist", "artist", None, "Four", images_4, post_url=post_url)
        embeds_4, _ = build_threads_embeds(post_4)
        assert len(embeds_4) == 4
        assert embeds_4[0].footer.text == "Threads"  # Không có chú thích dư

        # Case 4: 5 ảnh (Vượt biên Max + 1)
        images_5 = [f"https://cdn.com/{i}.jpg" for i in range(5)]
        post_5 = ThreadsPost("Artist", "artist", None, "Five", images_5, post_url=post_url)
        embeds_5, _ = build_threads_embeds(post_5)
        assert len(embeds_5) == 4  # Discord UI vẫn chỉ render 4 embeds
        assert "+1 ảnh khác" in embeds_5[0].footer.text  # Footer báo dư chính xác 1 ảnh

    # --- BVA-04: Ranh giới thời gian tương đối (59s -> 60s, 3599s -> 3600s, 86399s -> 86400s) ---
    def test_bva_relative_time_boundaries(self):
        now = time.time()
        # Biên Giây -> Phút:
        assert _format_time_ago(now - 59) == "Vừa xong"
        assert _format_time_ago(now - 60) == "1 phút trước"

        # Biên Phút -> Giờ:
        assert _format_time_ago(now - 3599) == "59 phút trước"
        assert _format_time_ago(now - 3600) == "1 giờ trước"

        # Biên Giờ -> Ngày:
        assert _format_time_ago(now - 86399) == "23 giờ trước"
        assert _format_time_ago(now - 86400) == "1 ngày trước"


# ==============================================================================
# 3. BẢNG QUYẾT ĐỊNH (DECISION TABLE TESTING - DT)
# ==============================================================================
class TestDecisionTable:
    """
    Kỹ thuật Decision Table mô tả mối quan hệ giữa các điều kiện logic (Inputs)
    và hành động tương ứng (Outputs/Actions).

    Decision Matrix: Media Extraction & Rendering Flow
    | Điều kiện (Conditions)               | R1 (Video) | R2 (Multi-Img) | R3 (Single-Img) | R4 (Text-only) | R5 (Deleted) |
    |--------------------------------------|:----------:|:--------------:|:---------------:|:--------------:|:------------:|
    | C1: URL tồn tại & hợp lệ?            |    True    |      True      |      True       |      True      |    False     |
    | C2: Có video stream url?             |    True    |     False      |     False       |     False      |      -       |
    | C3: Số lượng ảnh > 1?                |     -      |      True      |     False       |     False      |      -       |
    | C4: Số lượng ảnh == 1?               |     -      |     False      |      True       |     False      |      -       |
    |--------------------------------------|------------|----------------|-----------------|----------------|--------------|
    | Hành động (Actions)                  |            |                |                 |                |              |
    | A1: Gửi Ephemeral Báo lỗi             |   False    |     False      |     False       |     False      |     True     |
    | A2: Tạo Embed kèm Video Hint         |    True    |     False      |     False       |     False      |    False     |
    | A3: Tạo Gallery Grid (N Embeds)      |   False    |      True      |     False       |     False      |    False     |
    | A4: Tạo 1 Embed Đơn kèm Ảnh          |   False    |     False      |      True       |     False      |    False     |
    | A5: Tạo 1 Embed Thuần Chữ            |   False    |     False      |     False       |      True      |    False     |
    """

    def test_dt_rule_1_video_post(self):
        post = ThreadsPost(
            "Tester", "tester", None, "Sample Video",
            image_urls=[], video_url="https://video.mp4", video_thumbnail_url="https://thumb.jpg",
            post_url="https://www.threads.net/@tester/post/vid1"
        )
        embeds, _ = build_threads_embeds(post)
        assert len(embeds) == 1
        assert "🎥 *Bài viết có video" in embeds[0].description
        assert embeds[0].image.url == "https://thumb.jpg"

    def test_dt_rule_2_multi_image_gallery(self):
        images = ["https://cdn.com/1.jpg", "https://cdn.com/2.jpg", "https://cdn.com/3.jpg"]
        post = ThreadsPost("Tester", "tester", None, "Gallery", image_urls=images, post_url="https://www.threads.net/@tester/post/gal1")
        embeds, _ = build_threads_embeds(post)
        assert len(embeds) == 3
        assert embeds[0].image.url == images[0]
        assert embeds[1].image.url == images[1]
        assert embeds[2].image.url == images[2]

    def test_dt_rule_3_single_image(self):
        post = ThreadsPost("Tester", "tester", None, "Single Pic", image_urls=["https://cdn.com/pic.jpg"], post_url="https://www.threads.net/@tester/post/pic1")
        embeds, _ = build_threads_embeds(post)
        assert len(embeds) == 1
        assert embeds[0].image.url == "https://cdn.com/pic.jpg"

    def test_dt_rule_4_text_only(self):
        post = ThreadsPost("Tester", "tester", None, "Pure Text", image_urls=[], post_url="https://www.threads.net/@tester/post/txt1")
        embeds, _ = build_threads_embeds(post)
        assert len(embeds) == 1
        assert embeds[0].image.url is None

    @pytest.mark.asyncio
    async def test_dt_rule_5_deleted_post_raises(self):
        fetcher = ThreadsFetcher()
        url = "https://www.threads.net/@tester/post/deleted_post_id"
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.url = f"{url}?error=invalid_post"  # Meta redirects deleted posts
        mock_resp.text = AsyncMock(return_value="<html>Login to Threads</html>")

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        mock_session.closed = False
        mock_session.close = AsyncMock()
        fetcher._session = mock_session

        with pytest.raises(ThreadsPostNotFound):
            await fetcher._fetch_og_scrape(url, "tester", "deleted_post_id")

        await fetcher.close()


# ==============================================================================
# 4. KIỂM THỬ CHUYỂN TRẠNG THÁI (STATE TRANSITION TESTING - ST)
# ==============================================================================
class TestStateTransition:
    """
    Kiểm thử máy trạng thái (FSM) của quy trình Discord HTTP Interaction:
    Trạng thái: [S0: Raw HTTP] -> [S1: Verify Signature] -> [S2: Acknowledge (Type 5)] -> [S3: Async Task] -> [S4: Final Followup]
    """

    def test_st_invalid_signature_transition_to_401_termination(self, ed25519_keypair, monkeypatch):
        """S0 -> S1 (Failed) -> Terminate 401 Unauthorized"""
        import main
        monkeypatch.setattr(main, "verify_key", ed25519_keypair["signing_key"].verify_key)

        bad_event = {
            "httpMethod": "POST",
            "headers": {"x-signature-ed25519": "00" * 64, "x-signature-timestamp": "1000"},
            "body": "{}",
        }
        res = lambda_handler(bad_event, None)
        assert res["statusCode"] == 401

    def test_st_valid_ping_transition_to_pong(self, ed25519_keypair, monkeypatch):
        """S0 -> S1 (OK) -> Handshake PING (Type 1) -> PONG (Type 1) 200 OK"""
        import json
        import main
        signing_key = ed25519_keypair["signing_key"]
        monkeypatch.setattr(main, "verify_key", signing_key.verify_key)

        ts = str(int(time.time()))
        body = json.dumps({"type": 1})
        sig = signing_key.sign(f"{ts}{body}".encode()).signature.hex()

        ping_event = {
            "httpMethod": "POST",
            "headers": {"x-signature-ed25519": sig, "x-signature-timestamp": ts},
            "body": body,
        }
        res = lambda_handler(ping_event, None)
        assert res["statusCode"] == 200
        assert json.loads(res["body"])["type"] == 1

    def test_st_command_defer_to_async_followup_cycle(self, ed25519_keypair, monkeypatch):
        """S0 -> S1 (OK) -> Command (Type 2) -> Fast Defer (Type 5 < 3s) -> Async Background Task -> Followup Webhook"""
        import json
        import main
        signing_key = ed25519_keypair["signing_key"]
        monkeypatch.setattr(main, "verify_key", signing_key.verify_key)

        ts = str(int(time.time()))
        cmd_body = json.dumps({
            "type": 2,
            "token": "lifecycle_token_xyz",
            "data": {"name": "threads", "options": [{"name": "url", "value": "https://www.threads.net/@user/post/CuZsgfWLyiI"}]}
        })
        sig = signing_key.sign(f"{ts}{cmd_body}".encode()).signature.hex()

        event = {
            "httpMethod": "POST",
            "headers": {"x-signature-ed25519": sig, "x-signature-timestamp": ts},
            "body": cmd_body,
        }

        # Bắt transition defer
        with patch("threading.Thread") as mock_thread:
            defer_res = lambda_handler(event, None)
            assert defer_res["statusCode"] == 200
            assert json.loads(defer_res["body"])["type"] == 5
            mock_thread.assert_called_once()  # Kích hoạt transition sang Background Task


# ==============================================================================
# 5. ĐOÁN LỖI (ERROR GUESSING - EG)
# ==============================================================================
class TestErrorGuessing:
    """
    Kỹ thuật Error Guessing dựa trên kinh nghiệm thực chiến của QA để tìm
    những lỗi tiềm ẩn, dữ liệu dị biệt (anomalies) hoặc trường hợp đặc thù của mạng xã hội.
    """

    def test_eg_vietnamese_stats_with_commas(self):
        """Người dùng mạng xã hội tại VN thường có số liệu dạng '4,3K' hoặc '1,2M' (dấu phẩy thập phân)"""
        assert _format_count("4,3K") == "4.3K"
        assert _format_count("1,2M") == "1.2M"
        from main import parse_stats_from_text
        stats = parse_stats_from_text("515K lượt xem · 8,9K lượt thích · 100 bình luận")
        assert stats.get("likes") == "8,9K"
        assert _format_count(stats["likes"]) == "8.9K"

    def test_eg_facebook_login_wall_bypass(self):
        """Facebook khi bị chặn thường trả về Title: 'Log in or sign up to view' thay vì tên bài viết"""
        from main import is_login_wall_text, parse_fb_title
        assert is_login_wall_text("Log in or sign up to view") is True
        assert is_login_wall_text("Đăng nhập hoặc đăng ký để xem") is True

        parsed = parse_fb_title("Log in or sign up to view | Facebook")
        assert parsed["title"] is None
        assert parsed["author"] is None

    def test_eg_url_with_redundant_whitespaces_and_newlines(self):
        """Người dùng copy link thường vô tình dán kèm khoảng trắng hoặc ký tự xuống dòng"""
        fetcher = ThreadsFetcher()
        dirty_url = "   https://www.threads.net/@zuck/post/CuZsgfWLyiI \n\t "
        canonical, handle, post_id = fetcher.validate_and_normalize_url(dirty_url)
        assert canonical == "https://www.threads.net/@zuck/post/CuZsgfWLyiI"
        assert handle == "zuck"
        assert post_id == "CuZsgfWLyiI"

    def test_eg_description_truncation_without_breaking_words(self):
        """Cắt văn bản quá dài không được cắt ngang giữa một từ, phải cắt theo ranh giới khoảng trắng"""
        long_sentence = "Đây là một câu rất dài nhằm kiểm tra thuật toán cắt chuỗi theo từ ngữ tiếng Việt."
        truncated = truncate_description(long_sentence, "https://threads.net", max_length=30)
        # Không được kết thúc lửng lơ ở giữa từ ngữ
        assert not truncated.startswith("Đây là một câu rất dài nhằm ki…")
        assert "… [xem đầy đủ]" in truncated
