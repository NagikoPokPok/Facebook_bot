"""
Media Proxy Module cho Facebook Embed
Giải quyết triệt để lỗi ảnh/video thumbnail Facebook không tải được do:
1. Chữ ký URL CDN của Facebook hết hạn.
2. Facebook chặn hotlink/CORS khi tải trực tiếp từ domain khác qua <img> tag.
"""

import hashlib
import ipaddress
import logging
import os
import time
from urllib.parse import urlparse
from flask import Blueprint, request, Response, jsonify
import requests

logger = logging.getLogger(__name__)

# Blueprint Flask cho API media proxy
media_proxy_bp = Blueprint("media_proxy", __name__)

# Thư mục cache trên ổ đĩa
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".proxy_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Cấu hình thời gian sống của cache (mặc định 24 giờ = 86400 giây)
CACHE_TTL_SECONDS = int(os.environ.get("PROXY_CACHE_TTL", 86400))

# Bộ đệm in-memory (bộ nhớ tạm) để phục vụ siêu tốc
_MEMORY_CACHE = {}
MAX_MEMORY_ITEMS = 300

# Headers giả lập trình duyệt gửi lên Facebook CDN
FB_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.facebook.com/",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
}


def _is_safe_url(url: str) -> bool:
    """
    Bảo vệ chống SSRF:
    - Chỉ cho phép giao thức http hoặc https.
    - Chặn các địa chỉ IP nội bộ / loopback / link-local (127.0.0.1, 10.x, 192.168.x, 169.254.x).
    """
    if not url or not isinstance(url, str):
        return False

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    # Kiểm tra nếu hostname là IP nội bộ
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return False
    except ValueError:
        # Hostname là domain name (hợp lệ)
        pass

    return True


def _get_cache_key(url: str) -> str:
    """Tạo khóa băm SHA-256 từ URL"""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _get_from_cache(cache_key: str):
    """Lấy ảnh từ Memory Cache hoặc Disk Cache nếu còn hạn"""
    now = time.time()

    # 1. Kiểm tra Memory Cache
    if cache_key in _MEMORY_CACHE:
        entry = _MEMORY_CACHE[cache_key]
        if now - entry["timestamp"] < CACHE_TTL_SECONDS:
            return entry["data"], entry["content_type"]
        else:
            del _MEMORY_CACHE[cache_key]

    # 2. Kiểm tra Disk Cache
    disk_path = os.path.join(CACHE_DIR, cache_key)
    meta_path = disk_path + ".meta"
    if os.path.exists(disk_path) and os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = f.read().splitlines()
                ts = float(meta[0])
                content_type = meta[1] if len(meta) > 1 else "image/jpeg"

            if now - ts < CACHE_TTL_SECONDS:
                with open(disk_path, "rb") as f:
                    data = f.read()
                # Lưu ngược lại vào Memory Cache để phục vụ lần sau nhanh hơn
                if len(_MEMORY_CACHE) < MAX_MEMORY_ITEMS:
                    _MEMORY_CACHE[cache_key] = {"data": data, "content_type": content_type, "timestamp": ts}
                return data, content_type
            else:
                # Quá hạn -> xóa file cũ
                os.remove(disk_path)
                os.remove(meta_path)
        except Exception as e:
            logger.warning(f"Lỗi khi đọc disk cache: {e}")

    return None, None


def _save_to_cache(cache_key: str, data: bytes, content_type: str):
    """Lưu ảnh vào Memory Cache và Disk Cache"""
    now = time.time()

    # Lưu Memory Cache
    if len(_MEMORY_CACHE) >= MAX_MEMORY_ITEMS:
        # Xóa các mục cũ nhất nếu đầy
        old_keys = sorted(_MEMORY_CACHE.keys(), key=lambda k: _MEMORY_CACHE[k]["timestamp"])[:50]
        for k in old_keys:
            del _MEMORY_CACHE[k]

    _MEMORY_CACHE[cache_key] = {
        "data": data,
        "content_type": content_type,
        "timestamp": now,
    }

    # Lưu Disk Cache
    try:
        disk_path = os.path.join(CACHE_DIR, cache_key)
        meta_path = disk_path + ".meta"
        with open(disk_path, "wb") as f:
            f.write(data)
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(f"{now}\n{content_type}")
    except Exception as e:
        logger.warning(f"Lỗi khi ghi disk cache: {e}")


@media_proxy_bp.route("/api/media-proxy", methods=["GET"])
def media_proxy():
    """
    Endpoint: GET /api/media-proxy?url=<IMAGE_URL>
    - Nhận URL ảnh Facebook hoặc ảnh bên ngoài.
    - Trả về binary dữ liệu ảnh kèm Content-Type chuẩn và HTTP Cache-Control.
    - Có retry 1 lần khi timeout.
    """
    url = request.args.get("url")
    if not url:
        return jsonify({"error": "Thiếu tham số url"}), 400

    if not _is_safe_url(url):
        return jsonify({"error": "URL không hợp lệ hoặc bị từ chối bảo mật"}), 400

    cache_key = _get_cache_key(url)

    # 1. Kiểm tra Cache
    cached_data, cached_type = _get_from_cache(cache_key)
    if cached_data:
        return Response(
            cached_data,
            mimetype=cached_type,
            headers={
                "Cache-Control": f"public, max-age={CACHE_TTL_SECONDS}, immutable",
                "X-Proxy-Cache": "HIT",
            },
        )

    # 2. Tải ảnh từ Server đích (kèm cơ chế Retry 1 lần)
    data = None
    content_type = "image/jpeg"
    max_retries = 2

    for attempt in range(max_retries):
        try:
            resp = requests.get(
                url,
                headers=FB_FETCH_HEADERS,
                timeout=6,
                stream=True,
            )
            if resp.status_code == 200:
                data = resp.content
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                break
            else:
                logger.warning(f"Tải ảnh thất bại lần {attempt+1} (HTTP {resp.status_code}): {url}")
        except Exception as error:
            logger.warning(f"Lỗi kết nối lần {attempt+1}: {error}")
            time.sleep(0.3)

    if not data:
        # Nếu cả 2 lần đều thất bại, trả về mã 502 để Client kích hoạt fallback
        return jsonify({"error": "Không thể tải ảnh từ nguồn đích", "url": url}), 502

    # 3. Lưu vào Cache
    _save_to_cache(cache_key, data, content_type)

    # 4. Phản hồi cho Trình duyệt Client
    return Response(
        data,
        mimetype=content_type,
        headers={
            "Cache-Control": f"public, max-age={CACHE_TTL_SECONDS}, immutable",
            "X-Proxy-Cache": "MISS",
        },
    )
