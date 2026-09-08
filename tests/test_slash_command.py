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
