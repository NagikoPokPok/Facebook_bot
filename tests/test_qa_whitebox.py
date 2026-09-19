"""
TEST SUITE: WHITE-BOX TESTING (HỘP TRẮNG)
Dự án: Discord Facebook & Threads Embed Bot
Mục tiêu: Kiểm thử dựa trên cấu trúc mã nguồn nội bộ (Structure-Based Testing)
Kỹ thuật áp dụng:
  1. Độ bao phủ câu lệnh (Statement Coverage)
  2. Độ bao phủ nhánh (Branch / Decision Coverage)
  3. Độ bao phủ điều kiện (Condition Coverage)
  4. Kiểm thử đường dẫn độc lập (Path Coverage)
"""

import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import main
from main import (
    _chunk_text,
    _raw_body,
    _verify,
    _send_followup,
    _format_count,
    _format_time_ago,
    _fetch_fb_data,
)
from threads_fetcher import ThreadsFetcher, ThreadsPost
from threads_embed_builder import (
    build_threads_payload_dict,
    build_threads_embeds,
)


# ==============================================================================
# 1. BRANCH COVERAGE: THREADS EMBED BUILDER
# ==============================================================================
class TestWhiteBoxEmbedBuilderBranches:
    """
    Kiểm thử vét cạn các nhánh rẽ trong threads_embed_builder.py
    """

    def test_branch_author_naming_variations(self):
        """
        Nhánh Line 142-147 trong build_threads_payload_dict:
        - Nhánh 1: author_name khác author_handle -> 'Name (@handle)'
        - Nhánh 2: author_name trùng author_handle -> '@handle'
        - Nhánh 3: author_name là 'Threads User' hoặc None -> '@handle' hoặc 'Threads User'
        - Nhánh 4: handle là 'threads' -> Tên gốc hoặc 'Threads User'
        """
        # Nhánh 1: Name != Handle
        p1 = ThreadsPost("Elon Musk", "elon", None, "Text", [], post_url="https://threads.net/p1")
        d1 = build_threads_payload_dict(p1)
        assert d1["embeds"][0]["author"]["name"] == "Elon Musk (@elon)"

        # Nhánh 2: Name == Handle (không lặp lại @elon (@elon))
        p2 = ThreadsPost("elon", "elon", None, "Text", [], post_url="https://threads.net/p2")
        d2 = build_threads_payload_dict(p2)
        assert d2["embeds"][0]["author"]["name"] == "@elon"

        # Nhánh 3: Name là 'Threads User'
        p3 = ThreadsPost("Threads User", "random_handle", None, "Text", [], post_url="https://threads.net/p3")
        d3 = build_threads_payload_dict(p3)
        assert d3["embeds"][0]["author"]["name"] == "@random_handle"

        # Nhánh 4: Handle là 'threads' (default fallback)
        p4 = ThreadsPost("Threads User", "threads", None, "Text", [], post_url="https://threads.net/p4")
        d4 = build_threads_payload_dict(p4)
        assert d4["embeds"][0]["author"]["name"] == "Threads User"

    def test_branch_payload_gallery_and_video_thumbnails(self):
        """
        Nhánh Line 137, 172-185:
        - Video post có video_thumbnail_url
        - Carousel có > 1 ảnh tạo danh sách raw_embeds
        """
        # Nhánh video
        p_vid = ThreadsPost(
            "User", "user", None, "Clip", [],
            video_url="https://stream.mp4", video_thumbnail_url="https://thumb.jpg",
            post_url="https://threads.net/vid"
        )
        d_vid = build_threads_payload_dict(p_vid)
        assert "🎥 *Bài viết có video" in d_vid["embeds"][0]["description"]
        assert d_vid["embeds"][0]["image"]["url"] == "https://thumb.jpg"

        # Nhánh Gallery Grid > 1 ảnh trong REST API raw payload
        imgs = ["https://cdn.com/1.jpg", "https://cdn.com/2.jpg", "https://cdn.com/3.jpg"]
        p_gal = ThreadsPost("User", "user", None, "Album", imgs, post_url="https://threads.net/gal")
        d_gal = build_threads_payload_dict(p_gal)
        assert len(d_gal["embeds"]) == 3
        assert d_gal["embeds"][1]["image"]["url"] == "https://cdn.com/2.jpg"
        assert d_gal["embeds"][2]["image"]["url"] == "https://cdn.com/3.jpg"


# ==============================================================================
# 2. BRANCH & EXCEPTION PATH COVERAGE: THREADS FETCHER
# ==============================================================================
@pytest.mark.asyncio
class TestWhiteBoxFetcherBranches:
    """
    Kiểm thử các nhánh ngoại lệ mạng và phân tích cây cú pháp trong threads_fetcher.py
    """

    async def test_branch_resolve_share_url_redirect(self):
        """
        Nhánh `_resolve_share_url`:
        - Phát hiện HTTP 301/302 Redirect kèm header Location
        - Bóc tách handle và post_id từ URL mới
        """
        fetcher = ThreadsFetcher()
        mock_resp = MagicMock()
        mock_resp.status = 302
        mock_resp.headers = {"Location": "https://www.threads.net/@artist_viet/post/DdTstebmIRd"}

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        fetcher._session = mock_session

        url, handle, pid = await fetcher._resolve_share_url("https://www.threads.com/share/IqfJJdeHW/")
        assert url == "https://www.threads.net/@artist_viet/post/DdTstebmIRd"
        assert handle == "artist_viet"
        assert pid == "DdTstebmIRd"

        await fetcher.close()

    async def test_branch_resolve_share_url_exception_handling(self):
        """
        Nhánh `_resolve_share_url`:
        - Ngoại lệ mạng (Timeout, Connection Error) -> Bắt lỗi an toàn, fallback URL gốc
        """
        fetcher = ThreadsFetcher()
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.get.side_effect = Exception("Connection Timeout")
        fetcher._session = mock_session

        url, handle, pid = await fetcher._resolve_share_url("https://www.threads.com/share/IqfJJdeHW/")
        assert url == "https://www.threads.com/share/IqfJJdeHW/"
        assert handle is None
        assert pid == ""

        await fetcher.close()

    async def test_branch_ssr_parsing_malformed_json(self):
        """
        Nhánh `_extract_clean_images_from_ssr`:
        - HTML chứa script giả nhưng cú pháp JSON bị hỏng -> Không crash, trả về []
        """
        bad_ssr_html = """
        <script>
        requireLazy(["ServerJS"], function(s) {
            s.handle({
                "code": "BAD123",
                "image_versions2": { INVALID_JSON_SYNTAX ::::
            });
        });
        </script>
        """
        images = ThreadsFetcher._extract_clean_images_from_ssr(bad_ssr_html, "BAD123")
        assert images == []

    async def test_branch_ssr_parsing_missing_candidates(self):
        """
        Nhánh `_extract_clean_images_from_ssr`:
        - JSON hợp lệ nhưng mảng `candidates` rỗng hoặc không có url
        """
        empty_candidates_html = r"""
        <script>
        requireLazy(["ServerJS"], function(s) {
            s.handle({
                "code": "EMPTY_CANDIDATE",
                "image_versions2": {
                    "candidates": []
                }
            });
        });
        </script>
        """
        images = ThreadsFetcher._extract_clean_images_from_ssr(empty_candidates_html, "EMPTY_CANDIDATE")
        assert images == []

    async def test_branch_fetch_oembed_blockquote_text_extraction(self):
        """
        Nhánh `_fetch_oembed`:
        - Thành công với mã 200, bóc tách text từ thẻ <blockquote> và loại bỏ 'View on Threads'
        """
        fetcher = ThreadsFetcher()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={
            "author_name": "Satya Nadella",
            "html": "<blockquote>AI is changing developer workflows. View on Threads</blockquote>"
        })

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        fetcher._session = mock_session

        with patch.object(fetcher, "_get_author_avatar", new_callable=AsyncMock) as mock_avatar:
            mock_avatar.return_value = "https://cdn.com/avatar.jpg"
            post = await fetcher._fetch_oembed("https://www.threads.net/@satya/post/123", "satya", "123")
            assert post.author_name == "Satya Nadella"
            assert post.text == "AI is changing developer workflows."
            assert "View on Threads" not in post.text

        await fetcher.close()


# ==============================================================================
# 3. CONTROL FLOW & PATH COVERAGE: MAIN.PY
# ==============================================================================
class TestWhiteBoxMainControlFlow:
    """
    Kiểm thử các nhánh rẽ và đường đi độc lập trong main.py
    """

    def test_branch_chunk_text_hard_split(self):
        """
        Nhánh Line 337-338 trong `_chunk_text`:
        - Chuỗi siêu dài không có khoảng trắng, không có dấu chấm, không có xuống dòng.
        - Thuật toán bắt buộc phải cắt cưỡng bức (hard split / force chunk) tại max_chunk_size.
        """
        long_monolithic_string = "Z" * 250
        chunks = _chunk_text(long_monolithic_string, max_chunk_size=100)
        assert len(chunks) == 3
        assert len(chunks[0]) == 100
        assert len(chunks[1]) == 100
        assert len(chunks[2]) == 50

    def test_branch_chunk_text_period_split(self):
        """
        Nhánh Line 332-334 trong `_chunk_text`:
        - Chuỗi không có newline, nhưng có dấu chấm kết thúc câu '. '
        """
        sentence1 = "A" * 60 + ". "
        sentence2 = "B" * 60
        text = sentence1 + sentence2
        chunks = _chunk_text(text, max_chunk_size=80)
        assert len(chunks) == 2
        assert chunks[0] == sentence1.strip()

    def test_branch_raw_body_fallbacks(self):
        """
        Nhánh `_raw_body`:
        - None / dictionary không có key body
        - Body dạng bytes
        """
        assert _raw_body({}) == ""
        assert _raw_body({"body": None}) == ""
        assert _raw_body({"body": b"hello"}) == "hello"

    def test_branch_verify_missing_ed25519_key(self, monkeypatch):
        """
        Nhánh `_verify`:
        - Biến môi trường DISCORD_PUBLIC_KEY rỗng hoặc không khởi tạo được VerifyKey
        """
        monkeypatch.setattr(main, "verify_key", None)
        valid, body = _verify({"headers": {}, "body": "{}"})
        assert valid is False

    def test_branch_send_followup_error_fallback(self, monkeypatch):
        """
        Nhánh Line 578-589 trong `_send_followup`:
        - Khi Discord trả về status code 400 hoặc 500
        - Hệ thống thực thi nhánh fallback gửi thông báo lỗi giải phóng loading status
        """
        monkeypatch.setattr(main, "APPLICATION_ID", "test_app_id")

        mock_bad_resp = MagicMock()
        mock_bad_resp.status_code = 400
        mock_bad_resp.text = "Bad Request"

        with patch("requests.patch", return_value=mock_bad_resp) as mock_patch:
            _send_followup("dummy_token", {"content": "Test content"})
            # requests.patch được gọi 2 lần: 1 lần gửi chính (fail) + 1 lần gửi fallback
            assert mock_patch.call_count == 2
            fallback_call_json = mock_patch.call_args_list[1][1]["json"]
            assert "⚠️ Có lỗi khi hiển thị bài viết" in fallback_call_json["content"]
