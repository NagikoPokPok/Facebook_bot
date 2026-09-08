import base64
import json
import time
import pytest
from nacl.signing import SigningKey, VerifyKey
import main
from main import (
    _raw_body,
    _verify,
    _json_response,
    lambda_handler,
)


class TestRawBody:
    def test_plain_string_body(self):
        event = {"body": '{"test": 123}'}
        assert _raw_body(event) == '{"test": 123}'

    def test_bytes_body(self):
        event = {"body": b'{"test": 456}'}
        assert _raw_body(event) == '{"test": 456}'

    def test_base64_encoded_body(self):
        raw = '{"action": "test"}'
        b64 = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
        event = {"body": b64, "isBase64Encoded": True}
        assert _raw_body(event) == raw

    def test_empty_body(self):
        assert _raw_body({}) == ""


class TestVerify:
    def test_valid_signature(self, ed25519_keypair, monkeypatch):
        # Set verify_key in main module
        signing_key: SigningKey = ed25519_keypair["signing_key"]
        monkeypatch.setattr(main, "verify_key", signing_key.verify_key)

        timestamp = str(int(time.time()))
        body = json.dumps({"type": 1})
        message = f"{timestamp}{body}".encode("utf-8")
        signed = signing_key.sign(message)
        signature_hex = signed.signature.hex()

        event = {
            "headers": {
                "x-signature-ed25519": signature_hex,
                "x-signature-timestamp": timestamp,
            },
            "body": body,
        }

        valid, returned_body = _verify(event)
        assert valid is True
        assert returned_body == body

    def test_invalid_signature(self, ed25519_keypair, monkeypatch):
        signing_key: SigningKey = ed25519_keypair["signing_key"]
        monkeypatch.setattr(main, "verify_key", signing_key.verify_key)

        event = {
            "headers": {
                "x-signature-ed25519": "00" * 64,
                "x-signature-timestamp": str(int(time.time())),
            },
            "body": json.dumps({"type": 1}),
        }

        valid, _ = _verify(event)
        assert valid is False

    def test_missing_headers(self, ed25519_keypair, monkeypatch):
        signing_key: SigningKey = ed25519_keypair["signing_key"]
        monkeypatch.setattr(main, "verify_key", signing_key.verify_key)

        event = {"headers": {}, "body": "{}"}
        valid, _ = _verify(event)
        assert valid is False


class TestLambdaHandlerProtocol:
    def test_cors_options(self):
        event = {
            "httpMethod": "OPTIONS",
            "headers": {},
        }
        res = lambda_handler(event, None)
        assert res["statusCode"] == 200
        assert "Access-Control-Allow-Origin" in res["headers"]

    def test_invalid_signature_returns_401(self, ed25519_keypair, monkeypatch):
        signing_key: SigningKey = ed25519_keypair["signing_key"]
        monkeypatch.setattr(main, "verify_key", signing_key.verify_key)

        event = {
            "httpMethod": "POST",
            "headers": {
                "x-signature-ed25519": "badbad" * 10,
                "x-signature-timestamp": "12345",
            },
            "body": json.dumps({"type": 1}),
        }
        res = lambda_handler(event, None)
        assert res["statusCode"] == 401

    def test_discord_ping_type_1_returns_pong(self, ed25519_keypair, monkeypatch):
        signing_key: SigningKey = ed25519_keypair["signing_key"]
        monkeypatch.setattr(main, "verify_key", signing_key.verify_key)

        timestamp = str(int(time.time()))
        body = json.dumps({"type": 1})
        signature = signing_key.sign(f"{timestamp}{body}".encode("utf-8")).signature.hex()

        event = {
            "httpMethod": "POST",
            "headers": {
                "x-signature-ed25519": signature,
                "x-signature-timestamp": timestamp,
            },
            "body": body,
        }
        res = lambda_handler(event, None)
        assert res["statusCode"] == 200
        data = json.loads(res["body"])
        assert data["type"] == 1
