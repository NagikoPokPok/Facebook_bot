import asyncio
from dataclasses import dataclass
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import aiohttp
import discord
from discord import app_commands

from threads_fetcher import (
    ThreadsFetcher,
    ThreadsPost,
    ThreadsInvalidURLError,
    ThreadsPostNotFound,
    ALLOWED_DOMAINS,
)
from threads_embed_builder import (
    build_threads_embeds,
    build_threads_payload_dict,
    truncate_description,
    THREADS_COLOR,
)
from cogs.threads_embed import ThreadsEmbedCog


# ==============================================================================
# 1. URL VALIDATION & SSRF PROTECTION TESTS
# ==============================================================================

class TestThreadsURLValidation:
    def test_valid_urls(self):
        valid_urls = [
            ("https://www.threads.net/@zuck/post/CuZsgfWLyiI", "@zuck", "CuZsgfWLyiI"),
            ("https://threads.net/@user.name/post/12345", "@user.name", "12345"),
            ("https://www.threads.com/@someone/post/abc_XYZ-1", "@someone", "abc_XYZ-1"),
            ("https://threads.net/t/CuZsgfWLyiI", None, "CuZsgfWLyiI"),
            ("http://threads.com/t/ABC123", None, "ABC123"),
            ("https://www.threads.net/post/CuZsgfWLyiI", None, "CuZsgfWLyiI"),
            ("https://www.threads.com/share/IqfJJdeHW/", None, "IqfJJdeHW"),
            ("https://www.threads.com/share/QOVP7ZtYH/", None, "QOVP7ZtYH"),
            ("https://threads.net/share/IqfJJdeHW", None, "IqfJJdeHW"),
            ("https://www.threads.com/share/IqfJJdeHW/?xmt=AQG0rhQBG", None, "IqfJJdeHW"),
        ]
        for url, expected_handle, expected_id in valid_urls:
            canonical, handle, post_id = ThreadsFetcher.validate_and_normalize_url(url)
            assert post_id == expected_id
            if expected_handle:
                assert handle == expected_handle[1:]
            assert canonical.startswith("https://www.threads.net/")

    def test_invalid_domains_ssrf_protection(self):
        malicious_urls = [
            "https://evil.com/@zuck/post/12345",
            "https://threads.net.attacker.org/@zuck/post/12345",
            "https://facebook.com/@zuck/post/12345",
            "https://localhost:8080/@zuck/post/12345",
            "http://169.254.169.254/latest/meta-data/",
            "ftp://threads.net/@zuck/post/12345",
            "not-a-url",
            "",
        ]
        for url in malicious_urls:
            with pytest.raises(ThreadsInvalidURLError):
                ThreadsFetcher.validate_and_normalize_url(url)


# ==============================================================================
# 2. DATA FETCHER & LAYERED FALLBACK TESTS
# ==============================================================================

@pytest.mark.asyncio
class TestThreadsFetcher:
    async def test_cache_hit_mechanism(self):
        fetcher = ThreadsFetcher()
        url = "https://www.threads.net/@zuck/post/CuZsgfWLyiI"
        dummy_post = ThreadsPost(
            author_name="Mark Zuckerberg",
            author_handle="zuck",
            author_avatar_url="https://example.com/avatar.jpg",
            text="Hello Threads!",
            post_url=url,
        )

        canonical, _, _ = fetcher.validate_and_normalize_url(url)
        fetcher.post_cache[canonical] = dummy_post

        # Should retrieve from cache without network calls
        with patch.object(fetcher, "_fetch_og_scrape", new_callable=AsyncMock) as mock_scrape:
            result = await fetcher.fetch_post(url)
            assert result == dummy_post
            mock_scrape.assert_not_called()

        await fetcher.close()

    async def test_deleted_or_private_post_raises(self):
        fetcher = ThreadsFetcher()
        url = "https://www.threads.net/@zuck/post/DELETED123"

        # Mock HTML response redirecting to error=invalid_post
        mock_resp = MagicMock()
        mock_resp.url = "https://www.threads.com/?error=invalid_post"
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="<html><title>Threads · Log in</title></html>")

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        mock_session.closed = False
        mock_session.close = AsyncMock()

        fetcher._session = mock_session

        with pytest.raises(ThreadsPostNotFound):
            await fetcher.fetch_post(url)

        await fetcher.close()


    async def test_layered_fallback_to_oembed(self):
        fetcher = ThreadsFetcher()
        url = "https://www.threads.net/@zuck/post/CuZsgfWLyiI"

        # Force Layer 1 (Scrape) to fail with connection error
        with patch.object(fetcher, "_fetch_og_scrape", side_effect=aiohttp.ClientError("Blocked")):
            # Layer 2 (oEmbed) succeeds
            oembed_post = ThreadsPost(
                author_name="Mark Zuckerberg",
                author_handle="zuck",
                author_avatar_url=None,
                text="70 million signups",
                post_url=url,
            )
            with patch.object(fetcher, "_fetch_oembed", return_value=oembed_post) as mock_oembed:
                result = await fetcher.fetch_post(url)
                assert result == oembed_post
                mock_oembed.assert_called_once()

        await fetcher.close()

    async def test_layered_fallback_to_layer3_minimal(self):
        fetcher = ThreadsFetcher()
        url = "https://www.threads.net/@zuck/post/CuZsgfWLyiI"

        # Both Layer 1 and Layer 2 fail
        with patch.object(fetcher, "_fetch_og_scrape", side_effect=Exception("Layer 1 error")), \
             patch.object(fetcher, "_fetch_oembed", side_effect=Exception("Layer 2 error")):
            result = await fetcher.fetch_post(url)
            assert result.is_fallback is True
            assert result.author_handle == "zuck"
            assert "Không thể tải nội dung" in result.text

        await fetcher.close()

    async def test_resolve_share_url_success(self):
        fetcher = ThreadsFetcher()
        share_url = "https://www.threads.com/share/IqfJJdeHW/"
        resolved_post_url = "https://www.threads.com/@daychun5/post/DdErKg5D6uB?xmt=AQG"

        mock_resp = MagicMock()
        mock_resp.status = 302
        mock_resp.headers = {"Location": resolved_post_url}

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        mock_session.closed = False
        mock_session.close = AsyncMock()
        fetcher._session = mock_session

        canonical, handle, post_id = await fetcher._resolve_share_url(share_url)
        assert canonical == "https://www.threads.net/@daychun5/post/DdErKg5D6uB"
        assert handle == "daychun5"
        assert post_id == "DdErKg5D6uB"
        await fetcher.close()

    async def test_resolve_share_url_deleted_raises(self):
        fetcher = ThreadsFetcher()
        share_url = "https://www.threads.com/share/QOVP7ZtYH/"
        invalid_redirect = "https://www.threads.com/?error=invalid_post"

        mock_resp = MagicMock()
        mock_resp.status = 302
        mock_resp.headers = {"Location": invalid_redirect}

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        mock_session.closed = False
        mock_session.close = AsyncMock()
        fetcher._session = mock_session

        with pytest.raises(ThreadsPostNotFound):
            await fetcher._resolve_share_url(share_url)
        await fetcher.close()

    async def test_vietnamese_tren_title_parsing(self):
        from threads_fetcher import TITLE_AUTHOR_REGEX
        title = "Dây Chun (@daychun5) trên Threads"
        m = TITLE_AUTHOR_REGEX.match(title)
        assert m is not None
        assert m.group("name") == "Dây Chun"
        assert m.group("handle") == "daychun5"

    async def test_handle_only_title_parsing(self):
        from threads_fetcher import TITLE_HANDLE_ONLY_REGEX
        cases = [
            ("@xmawmx trên Threads", "xmawmx"),
            ("@xmawmx • Threads", "xmawmx"),
            ("xmawmx on Threads", "xmawmx"),
            ("@zuck - Threads", "zuck"),
        ]
        for title, expected in cases:
            m = TITLE_HANDLE_ONLY_REGEX.match(title)
            assert m is not None, f"Failed on title: {title}"
            assert m.group("handle") == expected

    async def test_og_scrape_handle_fallback_from_og_url(self):
        fetcher = ThreadsFetcher()
        url = "https://www.threads.com/share/test1234"
        html = """
        <html>
        <head>
            <meta property="og:title" content="Threads" />
            <meta property="og:description" content="Một chữ hả thiệt lớn" />
            <meta property="og:url" content="https://www.threads.net/@xmawmx/post/test1234" />
            <meta property="og:image" content="https://example.com/card.jpg" />
        </head>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.url = "https://www.threads.net/@xmawmx/post/test1234"
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        mock_session.closed = False
        mock_session.close = AsyncMock()
        fetcher._session = mock_session

        with patch.object(fetcher, "_get_author_avatar", new_callable=AsyncMock) as mock_avatar:
            mock_avatar.return_value = "https://cdn.example.com/avatar.jpg"
            post = await fetcher._fetch_og_scrape("https://www.threads.net/share/test1234", None, "test1234")
            assert post.author_handle == "xmawmx"
            assert post.author_name == "xmawmx"
            assert post.author_avatar_url == "https://cdn.example.com/avatar.jpg"
            mock_avatar.assert_called_once_with("xmawmx")

        await fetcher.close()

    async def test_is_synthesized_card_detection(self):
        synthesized_url = (
            "https://scontent.fdad4-1.fna.fbcdn.net/v/t39.92108-6/"
            "811534939_1616065136672922_8055016116524971875_n.jpg?_nc_cat=109"
        )
        clean_photo_url = (
            "https://instagram.fdad4-1.fna.fbcdn.net/v/t51.82787-15/"
            "811405250_18089658386378584_887674151234702559_n.jpg?stp=dst-jpg"
        )
        normal_url = "https://example.com/photos/image.jpg"

        assert ThreadsFetcher._is_synthesized_card(synthesized_url) is True
        assert ThreadsFetcher._is_synthesized_card(clean_photo_url) is False
        assert ThreadsFetcher._is_synthesized_card(normal_url) is False
        assert ThreadsFetcher._is_synthesized_card("") is False

    async def test_is_avatar_asset_detection(self):
        avatar_url_19 = "https://scontent.cdninstagram.com/v/t51.82787-19/747736304_n.jpg"
        avatar_url_2885 = "https://scontent.cdninstagram.com/v/t51.2885-19/573323465_n.jpg"
        post_photo_url_15 = "https://scontent.cdninstagram.com/v/t51.82787-15/811405250_n.jpg"

        assert ThreadsFetcher._is_avatar_asset(avatar_url_19) is True
        assert ThreadsFetcher._is_avatar_asset(avatar_url_2885) is True
        assert ThreadsFetcher._is_avatar_asset(post_photo_url_15) is False
        assert ThreadsFetcher._is_avatar_asset("https://example.com/pic.jpg") is False

    async def test_extract_clean_images_from_ssr_single_post(self):
        mock_ssr_html = r"""
        <script>
        requireLazy(["ServerJS"], function(s) {
            s.handle({
                "code":"DdTstebmIRd",
                "image_versions2":{
                    "candidates":[
                        {"height":905,"url":"https:\/\/instagram.fna.fbcdn.net\/v\/t51.82787-15\/811405250_18089658386378584_887674151234702559_n.jpg?stp=dst-jpg_e35_tt6\u00253D\u00253D","width":648},
                        {"height":670,"url":"https:\/\/instagram.fna.fbcdn.net\/v\/t51.82787-15\/811405250_18089658386378584_887674151234702559_n.jpg?stp=p480x480","width":480}
                    ]
                }
            });
        });
        </script>
        """
        images = ThreadsFetcher._extract_clean_images_from_ssr(mock_ssr_html, "DdTstebmIRd")
        assert len(images) == 1
        assert "811405250_18089658386378584_887674151234702559_n.jpg" in images[0]
        # Should pick the higher resolution candidate (score 650 > 480)
        assert "dst-jpg_e35_tt6" in images[0]

    async def test_extract_clean_images_from_ssr_carousel_post(self):
        mock_ssr_html = r"""
        <script>
        requireLazy(["ServerJS"], function(s) {
            s.handle({
                "carousel_media":[
                    {
                        "image_versions2":{
                            "candidates":[
                                {"height":720,"url":"https:\/\/instagram.fna.fbcdn.net\/v\/t51.82787-15\/item1_18087996260378584_n.jpg?stp=s720x720","width":720},
                                {"height":320,"url":"https:\/\/instagram.fna.fbcdn.net\/v\/t51.82787-15\/item1_18087996260378584_n.jpg?stp=s320x320","width":320}
                            ]
                        }
                    },
                    {
                        "image_versions2":{
                            "candidates":[
                                {"height":720,"url":"https:\/\/instagram.fna.fbcdn.net\/v\/t51.82787-15\/item2_18087996269378584_n.jpg?stp=s720x720","width":720}
                            ]
                        }
                    }
                ],
                "code":"Dc_WK_UGGoO"
            });
        });
        </script>
        """
        images = ThreadsFetcher._extract_clean_images_from_ssr(mock_ssr_html, "Dc_WK_UGGoO")
        assert len(images) == 2
        assert "item1_" in images[0]
        assert "item2_" in images[1]
        assert "s720x720" in images[0]

    async def test_og_scrape_replaces_synthesized_card_with_clean_images(self):
        fetcher = ThreadsFetcher()
        url = "https://www.threads.net/@kristina_ha02/post/DdTstebmIRd"
        html = """
        <html>
        <head>
            <meta property="og:title" content="kristina_ha02 on Threads" />
            <meta property="og:description" content="đừng hỏi vì sao Trường Giang bị phốt" />
            <meta property="og:url" content="https://www.threads.net/@kristina_ha02/post/DdTstebmIRd" />
            <meta property="og:image" content="https://scontent.fna.fbcdn.net/v/t39.92108-6/synthesized_card.jpg" />
        </head>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.url = url
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        mock_session.closed = False
        mock_session.close = AsyncMock()
        fetcher._session = mock_session

        clean_img = "https://instagram.fna.fbcdn.net/v/t51.82787-15/clean_photo.jpg"
        with patch.object(fetcher, "_get_clean_post_images", new_callable=AsyncMock) as mock_clean, \
             patch.object(fetcher, "_get_author_avatar", new_callable=AsyncMock) as mock_avatar:
            mock_clean.return_value = [clean_img]
            mock_avatar.return_value = "https://scontent.fna.fbcdn.net/v/t51.82787-19/avatar.jpg"

            post = await fetcher._fetch_og_scrape(url, "kristina_ha02", "DdTstebmIRd")
            assert len(post.image_urls) == 1
            assert post.image_urls[0] == clean_img
            # Synthesized card must NOT be present
            assert "synthesized_card.jpg" not in post.image_urls

        await fetcher.close()

    async def test_og_scrape_ignores_synthesized_card_for_text_post(self):
        fetcher = ThreadsFetcher()
        url = "https://www.threads.net/@zuck/post/CuZsgfWLyiI"
        html = """
        <html>
        <head>
            <meta property="og:title" content="Mark Zuckerberg (@zuck) on Threads" />
            <meta property="og:description" content="70 million sign ups on Threads as of this morning." />
            <meta property="og:url" content="https://www.threads.net/@zuck/post/CuZsgfWLyiI" />
            <meta property="og:image" content="https://scontent.fna.fbcdn.net/v/t39.92108-6/synthesized_card.jpg" />
        </head>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.url = url
        mock_resp.text = AsyncMock(return_value=html)

        mock_session = MagicMock()
        mock_session.get.return_value.__aenter__.return_value = mock_resp
        mock_session.closed = False
        mock_session.close = AsyncMock()
        fetcher._session = mock_session

        with patch.object(fetcher, "_get_clean_post_images", new_callable=AsyncMock) as mock_clean, \
             patch.object(fetcher, "_get_author_avatar", new_callable=AsyncMock) as mock_avatar:
            mock_clean.return_value = []
            mock_avatar.return_value = "https://scontent.fna.fbcdn.net/v/t51.82787-19/avatar.jpg"

            post = await fetcher._fetch_og_scrape(url, "zuck", "CuZsgfWLyiI")
            # For text-only post, synthesized card (t39.92108-6) must be discarded
            assert len(post.image_urls) == 0
            assert post.author_avatar_url == "https://scontent.fna.fbcdn.net/v/t51.82787-19/avatar.jpg"

        await fetcher.close()



# ==============================================================================
# 3. EMBED BUILDER UI/UX SPEC TESTS
# ==============================================================================

class TestThreadsEmbedBuilder:
    def test_text_only_post(self):
        post = ThreadsPost(
            author_name="Mark Zuckerberg",
            author_handle="zuck",
            author_avatar_url="https://example.com/zuck.jpg",
            text="70 million sign ups on Threads as of this morning.",
            image_urls=[],
            video_url=None,
            video_thumbnail_url=None,
            profile_url="https://www.threads.net/@zuck",
            post_url="https://www.threads.net/@zuck/post/CuZsgfWLyiI",
        )
        embeds, view = build_threads_embeds(post)

        assert len(embeds) == 1
        main_embed = embeds[0]
        assert main_embed.color.value == THREADS_COLOR
        assert "Mark Zuckerberg (@zuck)" in main_embed.author.name
        assert main_embed.author.icon_url == post.author_avatar_url
        assert main_embed.description == post.text
        assert main_embed.image.url is None
        assert main_embed.footer.text == "Threads"

        # Check view buttons
        assert len(view.children) == 2
        assert view.children[0].label == "🔗 Xem bài viết gốc"
        assert view.children[0].url == post.post_url
        assert view.children[1].label == "👤 Xem trang cá nhân"
        assert view.children[1].url == post.profile_url

    def test_single_image_post(self):
        img_url = "https://example.com/photo.jpg"
        post = ThreadsPost(
            author_name="Creator",
            author_handle="creator",
            author_avatar_url=None,
            text="Check out this photo!",
            image_urls=[img_url],
            post_url="https://www.threads.net/@creator/post/123",
        )
        embeds, _ = build_threads_embeds(post)
        assert len(embeds) == 1
        assert embeds[0].image.url == img_url

    def test_carousel_multiple_images(self):
        images = [f"https://example.com/img{i}.jpg" for i in range(6)]
        post = ThreadsPost(
            author_name="Creator",
            author_handle="creator",
            author_avatar_url=None,
            text="Multi-photo post",
            image_urls=images,
            post_url="https://www.threads.net/@creator/post/gallery",
        )
        embeds, _ = build_threads_embeds(post)
        # Max 4 images in gallery grid
        assert len(embeds) == 4
        # All embeds share the same URL for Discord gallery layout
        for emb in embeds:
            assert emb.url == post.post_url
        assert embeds[0].image.url == images[0]
        assert embeds[1].image.url == images[1]
        assert embeds[2].image.url == images[2]
        assert embeds[3].image.url == images[3]
        # Check extra images notice in footer
        assert "+2 ảnh khác" in embeds[0].footer.text

    def test_video_post(self):
        thumb = "https://example.com/video_thumb.jpg"
        post = ThreadsPost(
            author_name="Video Guy",
            author_handle="videoguy",
            author_avatar_url=None,
            text="Watch this cool clip!",
            image_urls=[],
            video_url="https://example.com/stream.mp4",
            video_thumbnail_url=thumb,
            post_url="https://www.threads.net/@videoguy/post/vid123",
        )
        embeds, _ = build_threads_embeds(post)
        assert len(embeds) == 1
        assert embeds[0].image.url == thumb
        assert "🎥 *Bài viết có video — nhấn nút bên dưới để xem*" in embeds[0].description

    def test_description_truncation(self):
        long_text = "A " * 600  # 1200 characters
        post_url = "https://www.threads.net/@user/post/long123"
        truncated = truncate_description(long_text, post_url, max_length=900)
        assert len(truncated) < 1000
        assert f"… [xem đầy đủ]({post_url})" in truncated

    def test_build_payload_dict_for_api(self):
        post = ThreadsPost(
            author_name="User",
            author_handle="user",
            author_avatar_url=None,
            text="Testing API payload",
            post_url="https://www.threads.net/@user/post/123",
            profile_url="https://www.threads.net/@user",
        )
        payload = build_threads_payload_dict(post)
        assert "embeds" in payload
        assert "components" in payload
        assert len(payload["embeds"]) == 1
        assert payload["components"][0]["type"] == 1  # Action Row
        assert payload["components"][0]["components"][0]["label"] == "🔗 Xem bài viết gốc"

    def test_threads_icon_url_is_valid_png(self):
        from threads_embed_builder import THREADS_ICON_URL
        assert THREADS_ICON_URL.endswith(".png")
        assert not THREADS_ICON_URL.endswith(".ico")

    def test_author_formatting_single_handle(self):
        post = ThreadsPost(
            author_name="xmawmx",
            author_handle="xmawmx",
            author_avatar_url="https://cdn.example.com/avatar.jpg",
            text="Single handle formatting test",
            post_url="https://www.threads.net/@xmawmx/post/123",
        )
        embeds, _ = build_threads_embeds(post)
        assert embeds[0].author.name == "@xmawmx"
        assert embeds[0].author.icon_url == "https://cdn.example.com/avatar.jpg"



# ==============================================================================
# 4. COG INTERACTION & CONCURRENCY TESTS
# ==============================================================================

@pytest.mark.asyncio
class TestThreadsCog:
    async def test_cog_invalid_url_ephemeral(self):
        bot = MagicMock()
        cog = ThreadsEmbedCog(bot)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.send_message = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        await cog.threads_command.callback(cog, interaction, "https://invalid-site.com/post/123")

        interaction.response.send_message.assert_called_once()
        args, kwargs = interaction.response.send_message.call_args
        assert kwargs.get("ephemeral") is True
        assert "Tên miền không được hỗ trợ" in args[0]

        await cog.cog_unload()

    async def test_cog_successful_flow(self):
        bot = MagicMock()
        mock_fetcher = MagicMock(spec=ThreadsFetcher)
        dummy_post = ThreadsPost(
            author_name="Zuck",
            author_handle="zuck",
            author_avatar_url=None,
            text="Successful embed",
            post_url="https://www.threads.net/@zuck/post/123",
            profile_url="https://www.threads.net/@zuck",
        )
        mock_fetcher.validate_and_normalize_url.return_value = (dummy_post.post_url, "zuck", "123")
        mock_fetcher.fetch_post = AsyncMock(return_value=dummy_post)

        cog = ThreadsEmbedCog(bot, fetcher=mock_fetcher)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        await cog.threads_command.callback(cog, interaction, dummy_post.post_url)

        # Defer should be called first
        interaction.response.defer.assert_called_once_with(thinking=True)
        # Followup send should be called with embeds and view
        interaction.followup.send.assert_called_once()
        _, kwargs = interaction.followup.send.call_args
        assert "embeds" in kwargs
        assert "view" in kwargs

    async def test_cog_deleted_post_ephemeral_followup(self):
        bot = MagicMock()
        mock_fetcher = MagicMock(spec=ThreadsFetcher)
        url = "https://www.threads.net/@zuck/post/deleted"
        mock_fetcher.validate_and_normalize_url.return_value = (url, "zuck", "deleted")
        mock_fetcher.fetch_post = AsyncMock(side_effect=ThreadsPostNotFound("Not found"))

        cog = ThreadsEmbedCog(bot, fetcher=mock_fetcher)

        interaction = MagicMock(spec=discord.Interaction)
        interaction.response = MagicMock()
        interaction.response.is_done.return_value = False
        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        await cog.threads_command.callback(cog, interaction, url)

        interaction.response.defer.assert_called_once_with(thinking=True)
        interaction.followup.send.assert_called_once()
        args, kwargs = interaction.followup.send.call_args
        assert kwargs.get("ephemeral") is True
        assert "Bài viết này không tồn tại" in args[0]


    async def test_concurrent_fetching_non_blocking(self):
        """Simulates 10 concurrent /threads command calls without blocking the event loop."""
        fetcher = ThreadsFetcher()

        async def mock_network_delay(url):
            await asyncio.sleep(0.05)
            canonical, handle, post_id = fetcher.validate_and_normalize_url(url)
            return ThreadsPost(
                author_name="User",
                author_handle=handle or "user",
                author_avatar_url=None,
                text=f"Post {post_id}",
                post_url=canonical,
            )

        with patch.object(fetcher, "fetch_post", side_effect=mock_network_delay):
            urls = [f"https://www.threads.net/@user/post/post_{i}" for i in range(10)]
            start_time = asyncio.get_event_loop().time()
            results = await asyncio.gather(*(fetcher.fetch_post(u) for u in urls))
            elapsed = asyncio.get_event_loop().time() - start_time

            assert len(results) == 10
            # If blocking sequentially: 10 * 0.05 = 0.5s. Asynchronously concurrent: ~0.05 - 0.15s.
            assert elapsed < 0.35

        await fetcher.close()
