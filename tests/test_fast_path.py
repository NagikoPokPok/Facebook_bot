import json
from unittest.mock import patch, MagicMock
import pytest
from main import _fetch_fb_data


class TestFastPathExtraction:
    def test_fast_path_extracts_video_without_yt_dlp(self):
        """Kiểm tra fast-path cào trực tiếp playable_url mà không cần import yt-dlp."""
        mock_html = """
        <html>
            <head>
                <meta property="og:title" content="Trấn Thành Fanpage" />
                <meta property="og:description" content="Hài hước cùng Trấn Thành" />
                <meta property="og:image" content="https://fbcdn.net/thumb.jpg" />
            </head>
            <body>
                <script>
                    var data = {
                        "playable_url": "https://video.xx.fbcdn.net/v/t39.1234/test.mp4",
                        "reaction_count": {"count": 12500},
                        "total_comment_count": 340,
                        "share_count": {"count": 89},
                        "creation_time": 1700000000
                    };
                </script>
            </body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.text = mock_html
        mock_resp.url = "https://www.facebook.com/reel/123456789"

        # Mock yt_dlp to ensure it is NEVER called
        with patch("main.http_session.get", return_value=mock_resp), \
             patch.dict("sys.modules", {"yt_dlp": None}):
            data = _fetch_fb_data("https://www.facebook.com/reel/123456789")

        assert data["title"] == "Trấn Thành Fanpage"
        assert data["video_url"] == "https://video.xx.fbcdn.net/v/t39.1234/test.mp4"
        assert data["likes"] == "12500"
        assert data["comments"] == "340"
        assert data["shares"] == "89"

    def test_fallback_to_yt_dlp_when_fast_path_has_no_video_stream(self):
        """Khi fast-path không tìm thấy link stream trong HTML, hệ thống fallback sang yt-dlp."""
        mock_html = """
        <html>
            <head><meta property="og:title" content="Reel Video" /></head>
            <body>No direct video in this HTML</body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.text = mock_html
        mock_resp.url = "https://www.facebook.com/reel/99999"

        mock_ydl_info = {
            "title": "Fallback Video Title",
            "url": "https://video.xx.fbcdn.net/fallback.mp4",
            "uploader": "Creator",
            "like_count": 500,
        }

        mock_ydl_instance = MagicMock()
        mock_ydl_instance.extract_info.return_value = mock_ydl_info
        mock_ydl_class = MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=mock_ydl_instance)))

        with patch("main.http_session.get", return_value=mock_resp), \
             patch("yt_dlp.YoutubeDL", mock_ydl_class, create=True):
            data = _fetch_fb_data("https://www.facebook.com/reel/99999")

        assert data["video_url"] == "https://video.xx.fbcdn.net/fallback.mp4"
        assert data["author"] == "Reel Video"

    def test_fast_path_extracts_stats_and_author_from_composite_og_title(self):
        """Kiểm tra bóc tách stats và author từ og:title dạng '515K lượt xem · 4,3K cảm xúc | ... | KodeKloud'"""
        mock_html = """
        <html>
            <head>
                <meta property="og:title" content="515K lượt xem · 4,3K cảm xúc | How AI actually searches the web... | KodeKloud" />
                <meta property="og:description" content="How AI actually searches the web is different from what most people picture..." />
                <meta property="og:image" content="https://fbcdn.net/thumb_kodekloud.jpg" />
            </head>
            <body>
                <script>
                    var data = {
                        "playable_url": "https://video.xx.fbcdn.net/v/ai_search.mp4",
                        "i18n_comment_count": "100"
                    };
                </script>
            </body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.text = mock_html
        mock_resp.url = "https://www.facebook.com/reel/987654321"

        with patch("main.http_session.get", return_value=mock_resp):
            data = _fetch_fb_data("https://www.facebook.com/reel/987654321")

        assert data["author"] == "KodeKloud"
        assert data["likes"] == "4,3K"
        assert data["comments"] == "100"
        assert data["video_url"] == "https://video.xx.fbcdn.net/v/ai_search.mp4"
        assert data["image"] == "https://fbcdn.net/thumb_kodekloud.jpg"

    def test_fast_path_ignores_login_wall_meta(self):
        """Kiểm tra hệ thống loại trừ thông báo Login wall 'Log in or sign up to view'"""
        mock_html = """
        <html>
            <head>
                <meta property="og:title" content="Log in or sign up to view" />
                <meta property="og:description" content="See posts, photos and more on Facebook." />
            </head>
            <body></body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.text = mock_html
        mock_resp.url = "https://www.facebook.com/reel/private123"

        with patch("main.http_session.get", return_value=mock_resp):
            data = _fetch_fb_data("https://www.facebook.com/reel/private123")

        assert data["title"] is None
        assert data["author"] is None
        assert data["description"] is None
