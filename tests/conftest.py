import os
import pytest
from nacl.signing import SigningKey

@pytest.fixture(scope="session")
def ed25519_keypair():
    """Tạo cặp khóa Ed25519 dùng cho việc test chữ ký Discord."""
    signing_key = SigningKey.generate()
    verify_key = signing_key.verify_key
    return {
        "signing_key": signing_key,
        "verify_key_hex": verify_key.encode().hex(),
        "public_key_bytes": verify_key.encode(),
    }

@pytest.fixture
def mock_env(ed25519_keypair, monkeypatch):
    """Giả lập biến môi trường cho Lambda."""
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", ed25519_keypair["verify_key_hex"])
    monkeypatch.setenv("APPLICATION_ID", "123456789012345678")
    monkeypatch.setenv("BOT_TOKEN", "mock_bot_token_abc_xyz")
