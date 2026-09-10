import base64
import datetime
import json
import logging
import os
import re
import threading
import time
import traceback

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
import requests
# ponytail: Không import yt_dlp ở top-level (tốn 4.2s nạp module gây cold start timeout 3s của Discord). Lazy load khi cần.

# ==============================================================================
# BƯỚC 1: KHỞI TẠO CẤU HÌNH VÀ BIẾN MÔI TRƯỜNG
# - Cần nạp file .env để lấy các khóa bí mật (Secret Keys) phục vụ xác thực Discord.
# - Cần thiết lập logger để theo dõi và debug luồng dữ liệu khi chạy trên Lambda hoặc Local.
# ==============================================================================

load_dotenv()

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DISCORD_PUBLIC_KEY = os.environ.get("DISCORD_PUBLIC_KEY")
APPLICATION_ID = os.environ.get("APPLICATION_ID")

# Regex kiểm tra đường dẫn URL có đúng định dạng Facebook (bài viết, watch, reels, share)
FB_URL_REGEX = re.compile(
    r"(https?://(?:www\.|m\.|web\.|mbasic\.)?(?:facebook\.com|fb\.watch)/\S+)",
    re.IGNORECASE,
)

# Tái sử dụng HTTP Session để duy trì Connection Pooling, giảm thời gian bắt tay SSL/TLS
http_session = requests.Session()

# Headers giả lập trình duyệt để cào trực tiếp cả thẻ OpenGraph và kho dữ liệu JSON đầy đủ
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
}

# Khởi tạo khóa xác thực VerifyKey từ Discord Public Key (dùng thuật toán Ed25519)
verify_key = None
if DISCORD_PUBLIC_KEY:
    try:
        verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))
    except Exception as error:
        logger.error(f"Khong the khoi tao DISCORD_PUBLIC_KEY: {error}")

# ponytail: Lazy singleton boto3 lambda client có sẵn region để tái sử dụng connection pool
_lambda_client = None

def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        import boto3
        _lambda_client = boto3.client("lambda", region_name=os.environ.get("AWS_REGION", "ap-southeast-1"))
    return _lambda_client


# ==============================================================================
# BƯỚC 2: CÁC HÀM BỔ TRỢ ĐỊNH DẠNG SỐ LIỆU, THỜI GIAN VÀ XÁC THỰC DISCORD
# - _format_count: Chuyển đổi số lượng tương tác sang định dạng ngắn (ví dụ: 41.2K, 5.7M).
# - _format_time_ago: Tính thời gian tương đối (ví dụ: 6 ngày trước, 2 giờ trước).
# - _chunk_text: Chia nhỏ văn bản dài theo từng đoạn văn để không bị tràn giới hạn Discord.
# ==============================================================================

def _raw_body(event: dict) -> str:
    """
    Trích xuất nội dung body thô từ event của API Gateway / Flask.
    Nếu body bị mã hóa Base64 (isBase64Encoded=True), tiến hành giải mã utf-8.
    """
    body = event.get("body") or ""
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    if event.get("isBase64Encoded") and isinstance(body, str):
        body = base64.b64decode(body).decode("utf-8")
    return body


def _verify(event: dict):
    """
    Xác thực chữ ký số của request từ Discord gửi tới.
    1. Lấy 2 header bắt buộc: 'x-signature-ed25519' và 'x-signature-timestamp'.
    2. Lấy body gốc thông qua hàm _raw_body.
    3. Dùng verify_key để kiểm tra tính toàn vẹn của chuỗi (timestamp + body).
    4. Trả về (True, body) nếu hợp lệ, ngược lại trả về (False, body).
    """
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    signature = headers.get("x-signature-ed25519")
    timestamp = headers.get("x-signature-timestamp")
    body = _raw_body(event)

    if not verify_key:
        logger.error("verify_key chua duoc khoi tao! Kiem tra bien moi truong DISCORD_PUBLIC_KEY.")
        return False, body

    if not signature or not timestamp:
        logger.warning(f"Thieu header chu ky. Co cac headers: {list(headers.keys())}")
        return False, body

    try:
        verify_key.verify(f"{timestamp}{body}".encode(), bytes.fromhex(signature))
        logger.info("Xac thuc chu ky Ed25519 thanh cong!")
        return True, body
    except (BadSignatureError, ValueError) as err:
        logger.warning(f"Xac thuc chu ky that bai: {err}")
        return False, body


def _json_response(payload: dict) -> dict:
    """
    Đóng gói payload thành response HTTP 200 JSON chuẩn kèm CORS headers.
    """
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Signature-Ed25519, X-Signature-Timestamp",
        },
        "body": json.dumps(payload, ensure_ascii=False),
    }



def _format_count(count_value) -> str:
    """
    Chuyển đổi số lượng like/comment/share thành chuỗi hiển thị gọn đẹp (ví dụ: 41K, 1.9K, 5.7M).
    """
    if count_value is None:
        return None
    if isinstance(count_value, str):
        cleaned = count_value.replace(",", "").replace(".", "").strip()
        if cleaned.isdigit():
            count_value = int(cleaned)
        else:
            return count_value.strip()
    try:
        num = int(count_value)
        if num >= 1_000_000:
            return f"{num / 1_000_000:.1f}M".replace(".0M", "M")
        elif num >= 1_000:
            return f"{num / 1_000:.1f}K".replace(".0K", "K")
        return str(num)
    except (ValueError, TypeError):
        return str(count_value)


def _format_time_ago(ts) -> str:
    """
    Chuyển đổi timestamp Unix hoặc chuỗi ngày YYYYMMDD thành khoảng thời gian tương đối.
    """
    if not ts:
        return None
    try:
        unix_ts = None
        if isinstance(ts, str) and len(ts) == 8 and ts.isdigit():
            dt = datetime.datetime.strptime(ts, "%Y%m%d").replace(tzinfo=datetime.timezone.utc)
            unix_ts = dt.timestamp()
        else:
            unix_ts = float(ts)

        diff = max(0, int(time.time() - unix_ts))
        if diff < 60:
            return "Vừa xong"
        elif diff < 3600:
            return f"{diff // 60} phút trước"
        elif diff < 86400:
            return f"{diff // 3600} giờ trước"
        elif diff < 2592000:
            return f"{diff // 86400} ngày trước"
        elif diff < 31536000:
            return f"{diff // 2592000} tháng trước"
        return f"{diff // 31536000} năm trước"
    except Exception:
        return None


def _chunk_text(text: str, max_chunk_size: int = 3800) -> list:
    """
    Chia nhỏ văn bản quá dài thành các khối nhỏ (mỗi khối <= max_chunk_size ký tự)
    theo ranh giới đoạn văn (\\n\\n hoặc \\n) để tránh làm đứt câu chữ.
    """
    if not text or len(text) <= max_chunk_size:
        return [text] if text else []

    chunks = []
    current_text = text
    while len(current_text) > max_chunk_size:
        split_idx = current_text.rfind("\n\n", 0, max_chunk_size)
        if split_idx == -1:
            split_idx = current_text.rfind("\n", 0, max_chunk_size)
        if split_idx == -1:
            split_idx = current_text.rfind(". ", 0, max_chunk_size)
            if split_idx != -1:
                split_idx += 1
        if split_idx == -1:
            split_idx = current_text.rfind(" ", 0, max_chunk_size)
        if split_idx == -1:
            split_idx = max_chunk_size

        chunks.append(current_text[:split_idx].strip())
        current_text = current_text[split_idx:].strip()

    if current_text:
        chunks.append(current_text)

    return chunks


# ==============================================================================
# BƯỚC 3: CÀO DỮ LIỆU BÀI VIẾT, VIDEO VÀ CHỈ SỐ TƯƠNG TÁC (STATS EXTRACTION)
# - Thu thập: Video stream (.mp4), Caption đầy đủ, Tác giả, Ảnh thumbnail.
# - Thu thập chỉ số: Likes/Reactions, Comments, Shares, Thời gian đăng bài.
# ==============================================================================

def _fetch_fb_data(url: str) -> dict:
    """
    Thu thập toàn bộ dữ liệu Facebook và thống kê tương tác với thời gian phản hồi siêu tốc (< 1.5-2s).
    """
    data = {
        "title": None,
        "description": None,
        "image": None,
        "video_url": None,
        "author": None,
        "site_name": "Facebook",
        "url": url,
        "likes": None,
        "comments": None,
        "shares": None,
        "timestamp": None,
    }

    # Phân loại link: Xác định URL có phải là Video/Reel/Watch hay không
    is_video_link = bool(re.search(r"/(?:reel|watch|videos|share/v|r)/", url, re.IGNORECASE))

    # ponytail: BƯỚC 1: FAST-PATH (1 request HTTP duy nhất ~1s) cào cả OpenGraph, Full-text, Stats & Direct Video MP4
    try:
        clean_url = re.sub(r"[?&](?:rdid|share_url|__cft__|__tn__)=[^&]*", "", data["url"] or url)
        resp = http_session.get(clean_url, headers=HEADERS, allow_redirects=True, timeout=5)
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        def og(property_name: str) -> str:
            tag = soup.find("meta", property=property_name)
            return tag["content"].strip() if tag and tag.get("content") else None

        og_title = og("og:title")
        og_desc = og("og:description")
        og_image = og("og:image")
        og_video = og("og:video") or og("og:video:secure_url") or og("og:video:url")

        invalid_titles = ["error", "error facebook", "facebook", "đăng nhập hoặc đăng ký để xem"]
        if og_title and og_title.lower() not in invalid_titles:
            data["title"] = data["title"] or og_title
            data["author"] = data["author"] or og_title

        data["image"] = data["image"] or og_image
        data["url"] = str(resp.url)

        # ponytail: Trích xuất trực tiếp CDN link MP4 từ JSON nhúng trong HTML của Facebook (không cần yt-dlp)
        video_matches = re.findall(
            r'"(?:playable_url|playable_url_quality_hd|browser_native_hd_url|browser_native_sd_url)"\s*:\s*"(https?[^"]+)"',
            html,
        )
        for v_match in video_matches:
            try:
                decoded_v = json.loads(f'"{v_match}"')
                if decoded_v.startswith("http"):
                    data["video_url"] = decoded_v
                    break
            except Exception:
                clean_v = v_match.replace(r"\/", "/")
                if clean_v.startswith("http"):
                    data["video_url"] = clean_v
                    break

        if not data["video_url"] and og_video:
            data["video_url"] = og_video

        # Trích xuất toàn bộ bài viết không bị cắt ngắn từ Relay/GraphQL JSON
        matches = re.findall(r'"(?:message|body|text)":\{"text":"(.*?)"\}', html)
        longest_text = ""
        for match in matches:
            try:
                decoded = json.loads(f'"{match}"')
                if len(decoded) > len(longest_text):
                    longest_text = decoded
            except Exception:
                pass

        if longest_text:
            data["description"] = longest_text
        elif og_desc and "xem bài viết, ảnh và nội dung khác" not in og_desc.lower():
            data["description"] = og_desc

        # Trích xuất số liệu likes/comments/shares và creation_time từ HTML JSON
        if not data["likes"]:
            rx_reactions = re.findall(r'"reaction_count"\s*:\s*\{\s*"count"\s*:\s*(\d+)\}', html)
            if rx_reactions:
                data["likes"] = rx_reactions[0]

        if not data["comments"]:
            rx_comments = re.findall(r'"(?:total_comment_count|total_count)"\s*:\s*(\d+)', html)
            if rx_comments:
                data["comments"] = rx_comments[0]

        if not data["shares"]:
            rx_shares = re.findall(r'"share_count"\s*:\s*\{\s*"count"\s*:\s*(\d+)\}', html)
            if rx_shares:
                data["shares"] = rx_shares[0]

        if not data["timestamp"]:
            rx_time = re.findall(r'"(?:creation_time|publish_time)"\s*:\s*(\d{10})', html)
            if rx_time:
                data["timestamp"] = rx_time[0]

    except Exception as error:
        logger.warning(f"Fast-path fetch error: {error}")

    # ponytail: BƯỚC 2: CHỈ FALLBACK SANG yt-dlp KHI LÀ VIDEO MÀ FAST-PATH CHƯA BÓC ĐƯỢC LINK STREAM
    if is_video_link and not data.get("video_url"):
        try:
            import yt_dlp
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "extract_flat": False,
                "noplaylist": True,
                "socket_timeout": 6,
                "cachedir": False,
                "check_formats": False,  # ponytail: Không probe từng format stream giúp tiết kiệm 2-3s
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    raw_title = info.get("title") or ""
                    data["author"] = data["author"] or info.get("uploader") or info.get("channel")
                    data["description"] = data["description"] or info.get("description")
                    data["image"] = data["image"] or info.get("thumbnail")
                    data["video_url"] = data["video_url"] or info.get("url")
                    if info.get("webpage_url"):
                        data["url"] = info.get("webpage_url")

                    data["likes"] = data["likes"] or info.get("like_count")
                    data["comments"] = data["comments"] or info.get("comment_count")
                    data["shares"] = data["shares"] or info.get("repost_count") or info.get("share_count")
                    data["timestamp"] = data["timestamp"] or info.get("timestamp") or info.get("upload_date")

                    if " | " in raw_title:
                        parts = raw_title.split(" | ")
                        data["title"] = data["title"] or (parts[1] if len(parts) > 1 else parts[0])
                    elif not data["title"]:
                        data["title"] = raw_title
        except Exception as error:
            logger.warning(f"yt-dlp fallback failed: {error}")

    return data


# ==============================================================================
# BƯỚC 4: GỬI KẾT QUẢ CẬP NHẬT QUA DISCORD WEBHOOK FOLLOWUP
# - Với Video: Đặt link stream .mp4 dưới dạng nhãn gọn [▶️ Video](url) trong content
#   để Discord tự động kích hoạt Trình phát Video HTML5 trực tiếp.
# - Với Bài viết: Đóng gói vào Embed (Khung viền xanh) chuẩn đẹp.
# - Hiển thị thanh thống kê tương tác: 👍 {likes}   💬 {comments}   ↪️ {shares}.
# - Hiển thị footer bài viết: Facebook • {thời gian trước} • [Link](url).
# ==============================================================================

def _send_followup(token: str, payload: dict):
    """
    Gửi request PATCH tới Discord Interaction Webhook để cập nhật tin nhắn gốc (@original).
    Endpoint: https://discord.com/api/v10/webhooks/{APPLICATION_ID}/{token}/messages/@original
    """
    if not APPLICATION_ID or not token:
        logger.error("Thieu APPLICATION_ID hoac token de gui followup")
        return

    # Bảo vệ: Discord giới hạn message content tối đa 2000 ký tự
    if "content" in payload and payload["content"] and len(payload["content"]) > 2000:
        logger.warning(f"Followup content quá dài ({len(payload['content'])} ký tự), tự động cắt ngắn về 2000.")
        payload["content"] = payload["content"][:1996] + "..."

    url = f"https://discord.com/api/v10/webhooks/{APPLICATION_ID}/{token}/messages/@original"
    try:
        resp = requests.patch(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code not in (200, 204):
            logger.error(f"Followup that bai (code {resp.status_code}): {resp.text}")
            # Fallback nếu payload bị từ chối: gửi tin nhắn đơn giản để giải phóng trạng thái 'đang suy nghĩ...'
            try:
                requests.patch(
                    url,
                    json={"content": "⚠️ Có lỗi khi hiển thị bài viết này trên Discord (dữ liệu bài viết quá dài hoặc không hợp lệ)."},
                    headers={"Content-Type": "application/json"},
                    timeout=5,
                )
            except Exception:
                pass
        else:
            logger.info(f"Trang thai Followup goc Discord: {resp.status_code}")
    except Exception as error:
        logger.error(f"Loi khi gui followup den Discord: {error}")


def _send_new_followup(token: str, payload: dict):
    """
    Gửi request POST tạo tin nhắn Followup mới trong channel (dùng cho các phần tiếp theo của bài viết dài).
    Endpoint: https://discord.com/api/v10/webhooks/{APPLICATION_ID}/{token}
    """
    if not APPLICATION_ID or not token:
        return

    # Bảo vệ: Discord giới hạn message content tối đa 2000 ký tự
    if "content" in payload and payload["content"] and len(payload["content"]) > 2000:
        logger.warning(f"Followup bổ sung content quá dài ({len(payload['content'])} ký tự), tự động cắt ngắn về 2000.")
        payload["content"] = payload["content"][:1996] + "..."

    url = f"https://discord.com/api/v10/webhooks/{APPLICATION_ID}/{token}"
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code not in (200, 201, 204):
            logger.error(f"Followup bo sung that bai (code {resp.status_code}): {resp.text}")
        else:
            logger.info(f"Trang thai Followup bo sung Discord: {resp.status_code}")
    except Exception as error:
        logger.error(f"Loi khi gui followup bo sung den Discord: {error}")


def _build_stats_text(data: dict) -> tuple:
    """
    Xây dựng thanh thống kê tương tác (👍 Likes, 💬 Comments, ↪️ Shares)
    và dòng nguồn bài viết (Facebook • Thời gian • [Link]).
    """
    stats_items = []
    likes_str = _format_count(data.get("likes"))
    comments_str = _format_count(data.get("comments"))
    shares_str = _format_count(data.get("shares"))

    # Đảm bảo hiển thị đầy đủ icon tương tác
    stats_items.append(f"👍 {likes_str if likes_str else '0'}")
    if comments_str:
        stats_items.append(f"💬 {comments_str}")
    if shares_str:
        stats_items.append(f"↪️ {shares_str}")

    stats_bar = "   ".join(stats_items)

    time_ago = _format_time_ago(data.get("timestamp"))
    target_url = data.get("url") or "https://facebook.com"

    meta_parts = ["Facebook"]
    if time_ago:
        meta_parts.append(time_ago)
    meta_parts.append(f"[Link]({target_url})")

    meta_bar = " • ".join(meta_parts)
    return stats_bar, meta_bar


def _process_threads_command(interaction: dict):
    """
    Hàm xử lý ngầm (Background Task) cho Slash Command /threads và /th.
    """
    token = interaction.get("token")
    options = interaction.get("data", {}).get("options", [])
    threads_url = next((o["value"] for o in options if o["name"] == "url"), None)

    from threads_fetcher import ThreadsFetcher, ThreadsInvalidURLError, ThreadsPostNotFound
    from threads_embed_builder import build_threads_payload_dict
    import asyncio

    fetcher = ThreadsFetcher()
    try:
        fetcher.validate_and_normalize_url(threads_url)
    except ThreadsInvalidURLError as val_err:
        _send_followup(token, {"content": f"⚠️ {str(val_err)}"})
        return

    try:
        post = asyncio.run(fetcher.fetch_post(threads_url))
        payload = build_threads_payload_dict(post)
        _send_followup(token, payload)
    except ThreadsPostNotFound:
        _send_followup(token, {"content": "⚠️ Bài viết này không tồn tại, đã bị xóa hoặc đang ở chế độ riêng tư."})
    except Exception as err:
        logger.error(f"Lỗi khi xử lý lệnh Threads: {err}")
        _send_followup(token, {"content": "⚠️ Đã xảy ra lỗi trong quá trình xử lý bài viết Threads này."})
    finally:
        try:
            asyncio.run(fetcher.close())
        except Exception:
            pass


def _process_slash_command(interaction: dict):
    """
    Hàm xử lý ngầm (Background Task) cho Slash Commands (/fbembbed, /threads, /th):
    1. Kiểm tra tên lệnh (/fbembbed hoặc /threads, /th).
    2. Trích xuất URL từ options và kiểm tra hợp lệ.
    3. Thu thập dữ liệu và đóng gói gửi Webhook Followup.
    """
    cmd_name = (interaction.get("data", {}).get("name") or "").lower()
    if cmd_name in ("threads", "th"):
        _process_threads_command(interaction)
        return

    token = interaction.get("token")
    options = interaction.get("data", {}).get("options", [])
    fb_url = next((o["value"] for o in options if o["name"] == "url"), None)

    # 1. Kiểm tra URL đầu vào
    if not fb_url or not FB_URL_REGEX.match(fb_url):
        _send_followup(token, {
            "content": "Link Facebook không hợp lệ. Vui lòng cung cấp link bài viết hoặc video Facebook hợp lệ."
        })
        return


    # 2. Thu thập dữ liệu từ Facebook tốc độ cao
    try:
        data = _fetch_fb_data(fb_url)
    except Exception as error:
        logger.error(f"Loi khi thu thap du lieu: {error}")
        data = {}

    # 3. Xử lý trường hợp không lấy được dữ liệu (bài viết riêng tư / nhóm kín)
    if not (data.get("title") or data.get("description") or data.get("image") or data.get("video_url")):
        _send_followup(token, {
            "content": "Không thể lấy dữ liệu từ link Facebook đã cung cấp (bài viết có thể ở chế độ riêng tư hoặc nhóm kín)."
        })
        return

    try:
        # 4. Tạo nút bấm dẫn link về bài viết gốc trên Facebook
        components = [{
            "type": 1,
            "components": [{
                "type": 2,
                "label": "Xem trên Facebook",
                "style": 5,
                "url": data.get("url") or fb_url
            }]
        }]

        # Xây dựng thanh tương tác và nguồn
        stats_bar, meta_bar = _build_stats_text(data)

        # ==========================================================================
        # TRƯỜNG HỢP 1: NẾU LÀ VIDEO -> GỬI CONTENT ĐỂ KÍCH HOẠT VIDEO PLAYER
        # ==========================================================================
        if data.get("video_url"):
            header_parts = []
            if data.get("author"):
                header_parts.append(f"**{data['author']}**")
            if data.get("title") and data.get("title") != data.get("author"):
                header_parts.append(f"*{data['title']}*")

            caption = (data.get("description") or "").strip()

            video_link = f"[▶️ Video]({data['video_url']})"

            # Gom các phần cố định bên dưới (stats, meta, link video)
            bottom_elements = []
            if stats_bar:
                bottom_elements.append(stats_bar)
            if meta_bar:
                bottom_elements.append(meta_bar)
            bottom_elements.append(video_link)
            bottom_text = "\n\n".join(bottom_elements)

            header_text = " • ".join(header_parts)

            # Giới hạn an toàn của Discord cho content là 2000 ký tự
            overhead = (len(header_text) + 2 if header_text else 0) + len(bottom_text) + 2
            max_caption_len = max(200, 1900 - overhead)

            if len(caption) <= max_caption_len:
                content_lines = []
                if header_text:
                    content_lines.append(header_text)
                if caption:
                    content_lines.append(caption)
                content_lines.append(bottom_text)

                _send_followup(token, {
                    "content": "\n\n".join(content_lines),
                    "components": components
                })
                return

            # Nếu caption quá dài -> Chia nhỏ caption theo đoạn bằng _chunk_text
            caption_chunks = _chunk_text(caption, max_chunk_size=max_caption_len)
            total_parts = len(caption_chunks)

            first_lines = []
            if header_text:
                first_lines.append(header_text)
            if caption_chunks:
                first_lines.append(caption_chunks[0])
            first_lines.append(bottom_text)

            _send_followup(token, {
                "content": "\n\n".join(first_lines),
                "components": components
            })

            # Gửi các phần caption tiếp theo qua tin nhắn Followup mới
            for index in range(1, total_parts):
                _send_new_followup(token, {
                    "content": f"*(Phần {index + 1}/{total_parts})*\n\n{caption_chunks[index]}"
                })
            return

        # ==========================================================================
        # TRƯỜNG HỢP 2: BÀI VIẾT VĂN BẢN / ẢNH -> ĐÓNG GÓI TRONG EMBED KHUNG VIỀN
        # ==========================================================================
        full_text = (data.get("description") or "").strip()
        text_chunks = _chunk_text(full_text, max_chunk_size=3800)

        author_name = data.get("author") or "Facebook"
        author_info = {
            "name": author_name[:256],
            "url": data.get("url") or fb_url
        }

        stats_footer_section = f"\n\n{stats_bar}\n{meta_bar}"

        if not text_chunks:
            embed = {
                "title": (data.get("title") or "Bài viết Facebook")[:256],
                "url": data.get("url") or fb_url,
                "description": f"{stats_bar}\n{meta_bar}",
                "color": 0x1877F2,
                "author": author_info,
            }
            if data.get("image"):
                embed["image"] = {"url": data["image"]}

            _send_followup(token, {
                "embeds": [embed],
                "components": components
            })
            return

        total_parts = len(text_chunks)

        # Gửi Phần 1 vào Embed chính (@original)
        first_desc = text_chunks[0]
        if total_parts == 1:
            first_desc += stats_footer_section

        first_embed = {
            "description": first_desc,
            "color": 0x1877F2,
            "author": author_info,
        }
        if data.get("title") and data.get("title") != author_name:
            first_embed["title"] = data["title"][:256]

        if total_parts > 1:
            first_embed["footer"] = {"text": f"Phần 1/{total_parts}"}

        if total_parts == 1 and data.get("image"):
            first_embed["image"] = {"url": data["image"]}

        _send_followup(token, {
            "embeds": [first_embed],
            "components": components
        })

        # Nếu có các Phần tiếp theo (2, 3, 4...), gửi tiếp qua POST Followup Messages
        for index in range(1, total_parts):
            chunk_desc = text_chunks[index]
            if index == total_parts - 1:
                chunk_desc += stats_footer_section

            followup_embed = {
                "description": chunk_desc,
                "color": 0x1877F2,
                "footer": {"text": f"Phần {index + 1}/{total_parts}"}
            }
            if index == total_parts - 1 and data.get("image"):
                followup_embed["image"] = {"url": data["image"]}

            _send_new_followup(token, {
                "embeds": [followup_embed]
            })

    except Exception as err:
        logger.error(f"Loi bat ngo khi dong goi va gui tin nhan: {err}")
        logger.error(traceback.format_exc())
        _send_followup(token, {
            "content": "⚠️ Đã xảy ra lỗi trong quá trình xử lý bài viết Facebook này. Vui lòng thử lại sau."
        })


# ==============================================================================
# BƯỚC 5: LAMBDA HANDLER CHÍNH ĐÓN NHẬN REQUEST DISCORD
# - Nhận event từ API Gateway / Lambda Function URL / Flask.
# - Bước 5.1: Xác thực chữ ký số bảo mật Ed25519.
# - Bước 5.2: Nếu là Ping (Type 1) -> Phản hồi Pong (Type 1) ngay lập tức.
# - Bước 5.3: Nếu là Slash Command (Type 2) -> Khởi chạy luồng nền (Thread)
#   và phản hồi ngay Defer (Type 5) trong < 50ms để chống timeout 3 giây.
# ==============================================================================

def lambda_handler(event, context):
    """
    Handler chính xử lý toàn bộ request từ Discord tương tác với Lambda / Webhook.
    """
    # ponytail: Bắt tác vụ tự gọi ngầm (Async Lambda Invoke) để không bị Lambda đóng băng CPU
    if isinstance(event, dict) and event.get("async_task") == "process_slash_command":
        logger.info("=== RUNNING ASYNC BACKGROUND TASK ===")
        _process_slash_command(event.get("interaction", {}))
        return {"statusCode": 200, "body": "done"}

    method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or ""
    ).upper()

    # ponytail: Xử lý CORS Preflight (OPTIONS) từ trình duyệt
    if method == "OPTIONS":
        logger.info("=== HANDLING CORS OPTIONS PREFLIGHT ===")
        return {
            "statusCode": 200,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-Signature-Ed25519, X-Signature-Timestamp",
            },
            "body": "",
        }

    logger.info(f"=== INCOMING EVENT === Method: {method}")

    cors_headers = {
        "Content-Type": "text/plain",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Signature-Ed25519, X-Signature-Timestamp",
    }

    # 1. BẮT BUỘC: Xác thực chữ ký số Ed25519 TRƯỚC TIÊN cho mọi request (kể cả PING)
    # Discord gửi cả request hợp lệ lẫn chữ ký giả mạo để kiểm tra bảo mật của endpoint.
    # Nếu chữ ký không khớp, bắt buộc phải trả về 401 Unauthorized.
    try:
        ok, verified_body = _verify(event)
        if not ok:
            return {
                "statusCode": 401,
                "headers": cors_headers,
                "body": "Invalid request signature",
            }
        body = verified_body
    except Exception as error:
        logger.error(f"Loi xac thuc chu ky: {str(error)}")
        logger.error(traceback.format_exc())
        return {
            "statusCode": 401,
            "headers": cors_headers,
            "body": "Invalid request signature",
        }

    # 2. Sau khi xác thực chữ ký số thành công, phân tích JSON payload
    try:
        interaction = json.loads(body)
    except Exception:
        interaction = {}

    # 3. Phản hồi yêu cầu PING handshake (Type 1) từ Discord Developer Portal
    if interaction.get("type") == 1:
        logger.info("=== DISCORD PING HANDSHAKE (TYPE 1) VERIFIED -> RETURNING PONG ===")
        return _json_response({"type": 1})

    # 2. Xử lý Application Command / Slash Command (Type 2)
    if interaction.get("type") == 2:

        # ponytail: Nếu chạy trên AWS Lambda, kích hoạt Async Invoke chính nó bằng boto3 (có sẵn trong runtime, 0đ)
        # để trả về Type 5 < 50ms cho Discord mà tiến trình cào dữ liệu không bị đóng băng (freezing).
        if context and hasattr(context, "function_name"):
            try:
                _get_lambda_client().invoke(
                    FunctionName=context.function_name,
                    InvocationType="Event",
                    Payload=json.dumps({"async_task": "process_slash_command", "interaction": interaction}),
                )
            except Exception as err:
                logger.error(f"Lỗi gọi Async Lambda invoke: {err}, chuyển sang dùng thread dự phòng")
                threading.Thread(target=_process_slash_command, args=(interaction,), daemon=True).start()
        else:
            # Chạy thread bình thường khi test local với Flask app.py
            threading.Thread(target=_process_slash_command, args=(interaction,), daemon=True).start()

        # Trả về ngay Type 5 (DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE) trong < 50ms để tránh timeout 3 giây của Discord
        return _json_response({"type": 5})

    return {"statusCode": 400, "body": "Unhandled interaction type"}