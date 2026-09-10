import json
import time
from unittest.mock import patch, MagicMock
import pytest
from nacl.signing import SigningKey
import main
from main import (
    lambda_handler,
    _process_slash_command,
)


class TestSlashCommandHandling:
    def test_type_2_returns_deferred_type_5(self, ed25519_keypair, monkeypatch):
        signing_key: SigningKey = ed25519_keypair["signing_key"]
        monkeypatch.setattr(main, "verify_key", signing_key.verify_key)

        timestamp = str(int(time.time()))
        interaction_payload = {
            "type": 2,
            "token": "mock_interaction_token",
            "data": {
                "name": "fbembbed",
                "options": [
                    {"name": "url", "value": "https://www.facebook.com/reel/123456789"}
                ],
            },
        }
        body = json.dumps(interaction_payload)
        signature = signing_key.sign(f"{timestamp}{body}".encode("utf-8")).signature.hex()

        event = {
            "httpMethod": "POST",
            "headers": {
                "x-signature-ed25519": signature,
                "x-signature-timestamp": timestamp,
            },
            "body": body,
        }

        # Mock background task launcher to avoid actually running threads or AWS invoke during this test
        with patch("threading.Thread") as mock_thread:
            mock_thread.return_value = MagicMock()
            res = lambda_handler(event, None)

        assert res["statusCode"] == 200
        data = json.loads(res["body"])
        assert data["type"] == 5  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE

    def test_invalid_facebook_url_followup(self):
        interaction = {
            "token": "test_token",
            "data": {
                "options": [
                    {"name": "url", "value": "https://notfacebook.com/something"}
                ]
            },
        }

        with patch("main._send_followup") as mock_followup:
            _process_slash_command(interaction)
            mock_followup.assert_called_once()
            call_args = mock_followup.call_args[0]
            assert call_args[0] == "test_token"
            assert "không hợp lệ" in call_args[1]["content"]

    def test_video_payload_formatting(self):
        interaction = {
            "token": "test_token_video",
            "data": {
                "options": [
                    {"name": "url", "value": "https://www.facebook.com/reel/123456"}
                ]
            },
        }

        mock_data = {
            "title": "Meme hay",
            "description": "Video hai huoc",
            "image": "https://fbcdn.net/thumb.jpg",
            "video_url": "https://fbcdn.net/video.mp4",
            "author": "Fanpage Hai",
            "site_name": "Facebook",
            "url": "https://www.facebook.com/reel/123456",
            "likes": 5000,
            "comments": 100,
            "shares": 20,
            "timestamp": time.time(),
        }

        with patch("main._fetch_fb_data", return_value=mock_data), \
             patch("main._send_followup") as mock_followup:
            _process_slash_command(interaction)
            mock_followup.assert_called_once()
            call_args = mock_followup.call_args[0]
            token, payload = call_args[0], call_args[1]
            assert token == "test_token_video"
            assert "[▶️ Video](https://fbcdn.net/video.mp4)" in payload["content"]
            assert "Fanpage Hai" in payload["content"]

    def test_post_payload_formatting(self):
        interaction = {
            "token": "test_token_post",
            "data": {
                "options": [
                    {"name": "url", "value": "https://www.facebook.com/post/999"}
                ]
            },
        }

        mock_data = {
            "title": "Bai viet Facebook",
            "description": "Noi dung bai viet dai rat hay",
            "image": "https://fbcdn.net/image.jpg",
            "video_url": None,
            "author": "Tac Gia",
            "site_name": "Facebook",
            "url": "https://www.facebook.com/post/999",
            "likes": 200,
            "comments": 10,
            "shares": 5,
            "timestamp": time.time(),
        }

        with patch("main._fetch_fb_data", return_value=mock_data), \
             patch("main._send_followup") as mock_followup:
            _process_slash_command(interaction)
            mock_followup.assert_called_once()
            call_args = mock_followup.call_args[0]
            token, payload = call_args[0], call_args[1]
            assert token == "test_token_post"
            assert "embeds" in payload
            assert payload["embeds"][0]["author"]["name"] == "Tac Gia"
            assert payload["embeds"][0]["color"] == 0x1877F2

    def test_async_background_task_event(self):
        event = {
            "async_task": "process_slash_command",
            "interaction": {"token": "async_token"},
        }
        with patch("main._process_slash_command") as mock_proc:
            res = lambda_handler(event, None)
            mock_proc.assert_called_once_with({"token": "async_token"})
            assert res["statusCode"] == 200

    def test_video_long_caption_splits_into_followups(self):
        interaction = {
            "token": "token_long_video",
            "data": {
                "options": [
                    {"name": "url", "value": "https://www.facebook.com/reel/123456"}
                ]
            },
        }

        # Caption dài 3000 ký tự
        long_caption = "Đoạn văn miêu tả video dài rất hay và chi tiết. " * 60
        mock_data = {
            "title": "Video Dài",
            "description": long_caption,
            "image": "https://fbcdn.net/thumb.jpg",
            "video_url": "https://fbcdn.net/video.mp4",
            "author": "Fanpage Dai",
            "site_name": "Facebook",
            "url": "https://www.facebook.com/reel/123456",
            "likes": 100,
            "comments": 5,
            "shares": 1,
            "timestamp": time.time(),
        }

        with patch("main._fetch_fb_data", return_value=mock_data), \
             patch("main._send_followup") as mock_followup, \
             patch("main._send_new_followup") as mock_new_followup:
            _process_slash_command(interaction)
            mock_followup.assert_called_once()
            call_args = mock_followup.call_args[0]
            token, payload = call_args[0], call_args[1]
            assert token == "token_long_video"
            assert len(payload["content"]) <= 2000
            assert "[▶️ Video](https://fbcdn.net/video.mp4)" in payload["content"]
            # Phải có ít nhất 1 followup bổ sung do caption dài
            assert mock_new_followup.call_count >= 1
            for call in mock_new_followup.call_args_list:
                new_payload = call[0][1]
                assert len(new_payload["content"]) <= 2000

    def test_send_followup_content_length_safety(self, monkeypatch):
        monkeypatch.setattr(main, "APPLICATION_ID", "dummy_app_id")
        overlength_content = "A" * 2500

        with patch("requests.patch") as mock_patch:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_patch.return_value = mock_resp

            main._send_followup("dummy_token", {"content": overlength_content})

            mock_patch.assert_called_once()
            called_json = mock_patch.call_args[1]["json"]
            assert len(called_json["content"]) <= 2000
            assert called_json["content"].endswith("...")
