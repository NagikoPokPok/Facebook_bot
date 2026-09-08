import time
import pytest
from main import (
    _format_count,
    _format_time_ago,
    _chunk_text,
    _build_stats_text,
    FB_URL_REGEX,
)


class TestFormatCount:
    def test_none_and_empty(self):
        assert _format_count(None) is None
        assert _format_count("") == ""

    def test_numbers_under_thousand(self):
        assert _format_count(0) == "0"
        assert _format_count(42) == "42"
        assert _format_count(999) == "999"
        assert _format_count("500") == "500"

    def test_numbers_thousands(self):
        assert _format_count(1000) == "1K"
        assert _format_count(1250) == "1.2K"
        assert _format_count("41,200") == "41.2K"
        assert _format_count("41.200") == "41.2K"
        assert _format_count(999900) == "999.9K"

    def test_numbers_millions(self):
        assert _format_count(1_000_000) == "1M"
        assert _format_count(5_700_000) == "5.7M"
        assert _format_count("12,345,678") == "12.3M"

    def test_non_numeric_fallback(self):
        assert _format_count("N/A") == "N/A"


class TestFormatTimeAgo:
    def test_none_or_empty(self):
        assert _format_time_ago(None) is None
        assert _format_time_ago("") is None

    def test_just_now(self):
        now = time.time()
        assert _format_time_ago(now - 10) == "Vừa xong"

    def test_minutes_ago(self):
        now = time.time()
        assert _format_time_ago(now - 120) == "2 phút trước"
        assert _format_time_ago(now - 3500) == "58 phút trước"

    def test_hours_ago(self):
        now = time.time()
        assert _format_time_ago(now - 7200) == "2 giờ trước"
        assert _format_time_ago(now - 80000) == "22 giờ trước"

    def test_days_ago(self):
        now = time.time()
        assert _format_time_ago(now - 86400 * 3) == "3 ngày trước"

    def test_yyyymmdd_string(self):
        result = _format_time_ago("20240101")
        assert result is not None
        assert "trước" in result


class TestChunkText:
    def test_empty_text(self):
        assert _chunk_text("") == []
        assert _chunk_text(None) == []

    def test_short_text(self):
        text = "Hello world from Discord embed test"
        chunks = _chunk_text(text, max_chunk_size=100)
        assert chunks == [text]

    def test_split_by_double_newline(self):
        p1 = "A" * 60
        p2 = "B" * 60
        text = f"{p1}\n\n{p2}"
        chunks = _chunk_text(text, max_chunk_size=80)
        assert len(chunks) == 2
        assert chunks[0] == p1
        assert chunks[1] == p2

    def test_split_by_single_newline(self):
        line1 = "A" * 50
        line2 = "B" * 50
        text = f"{line1}\n{line2}"
        chunks = _chunk_text(text, max_chunk_size=70)
        assert len(chunks) == 2
        assert chunks[0] == line1
        assert chunks[1] == line2


class TestFbUrlRegex:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.facebook.com/watch/?v=123456789",
            "https://facebook.com/reel/987654321",
            "https://m.facebook.com/story.php?story_fbid=111&id=222",
            "https://fb.watch/abcdef123/",
            "http://facebook.com/permalink.php?story_fbid=1&id=2",
            "https://web.facebook.com/someuser/posts/123456",
        ],
    )
    def test_valid_fb_urls(self, url):
        assert FB_URL_REGEX.match(url) is not None

    @pytest.mark.parametrize(
        "url",
        [
            "https://google.com",
            "https://youtube.com/watch?v=123",
            "https://fakebook.com/123",
            "random text not a url",
            "",
        ],
    )
    def test_invalid_urls(self, url):
        assert FB_URL_REGEX.match(url) is None


class TestBuildStatsText:
    def test_build_stats_all_fields(self):
        data = {
            "likes": 1500,
            "comments": 250,
            "shares": 50,
            "timestamp": time.time() - 3600,
            "url": "https://facebook.com/test",
        }
        stats_bar, meta_bar = _build_stats_text(data)
        assert "👍 1.5K" in stats_bar
        assert "💬 250" in stats_bar
        assert "↪️ 50" in stats_bar
        assert "Facebook" in meta_bar
        assert "1 giờ trước" in meta_bar
        assert "[Link](https://facebook.com/test)" in meta_bar
