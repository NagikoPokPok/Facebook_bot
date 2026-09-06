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
import yt_dlp

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

    if not verify_key or not signature or not timestamp:
        return False, body
    try:
        verify_key.verify(f"{timestamp}{body}".encode(), bytes.fromhex(signature))
        return True, body
    except (BadSignatureError, ValueError):
        return False, body


def _json_response(payload: dict) -> dict:
    """
    Đóng gói payload thành response HTTP 200 JSON chuẩn cho API Gateway / Flask.
    """
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
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
    Thu thập toàn bộ dữ liệu Facebook và thống kê tương tác với thời gian phản hồi siêu tốc (< 2-3s).
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

    # Phân loại link: Chỉ gọi yt-dlp nếu URL thực sự là Video/Reel
    is_video_link = bool(re.search(r"/(?:reel|watch|videos|share/v|r)/", url, re.IGNORECASE))

    # 1. Xử lý đường dẫn Video / Reel bằng yt-dlp với timeout chặt chẽ
    if is_video_link:
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
                "noplaylist": True,
                "socket_timeout": 4,
                "cachedir": False,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    raw_title = info.get("title") or ""
                    data["author"] = info.get("uploader") or info.get("channel")
                    data["description"] = info.get("description")
                    data["image"] = info.get("thumbnail")
                    data["video_url"] = info.get("url")
                    if info.get("webpage_url"):
                        data["url"] = info.get("webpage_url")

                    # Lấy chỉ số thống kê từ yt-dlp
                    data["likes"] = info.get("like_count")
                    data["comments"] = info.get("comment_count")
                    data["shares"] = info.get("repost_count") or info.get("share_count")
                    data["timestamp"] = info.get("timestamp") or info.get("upload_date")

                    # Phân tích chỉ số reactions/views nếu có sẵn trong tiêu đề Facebook
                    reactions_match = re.search(r"([\d\.,]+[KMB]?)\s*reactions?", raw_title, re.I)
                    if reactions_match and not data["likes"]:
                        data["likes"] = reactions_match.group(1)

                    comments_match = re.search(r"([\d\.,]+[KMB]?)\s*(?:comments?|bình luận)", raw_title, re.I)
                    if comments_match and not data["comments"]:
                        data["comments"] = comments_match.group(1)

                    shares_match = re.search(r"([\d\.,]+[KMB]?)\s*(?:shares?|chia sẻ)", raw_title, re.I)
                    if shares_match and not data["shares"]:
                        data["shares"] = shares_match.group(1)

                    # Làm sạch tiêu đề (loại bỏ phần thống kê 186K views · 2.6K reactions)
                    if " | " in raw_title:
                        parts = raw_title.split(" | ")
                        data["title"] = parts[1] if len(parts) > 1 else parts[0]
                    else:
                        data["title"] = raw_title
        except Exception as error:
            logger.warning(f"yt-dlp extract failed: {error}")

    # 2. Xử lý bài viết văn bản / ảnh bằng 1 Request duy nhất (lấy cả OpenGraph + Full Text JSON + Stats)
    if not data["video_url"] or not data["description"]:
        try:
            clean_url = re.sub(r"[?&](?:rdid|share_url|__cft__|__tn__)=[^&]*", "", data["url"] or url)
            resp = http_session.get(clean_url, headers=HEADERS, allow_redirects=True, timeout=4)
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
            data["video_url"] = data["video_url"] or og_video
            data["url"] = str(resp.url)

            # Trích xuất toàn bộ bài viết không bị cắt ngắn từ cấu trúc Relay/GraphQL JSON trong HTML
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
                rx_reactions = re.findall(r'"reaction_count":\{"count":(\d+)\}', html)
                if rx_reactions:
                    data["likes"] = rx_reactions[0]

            if not data["comments"]:
                rx_comments = re.findall(r'"(?:total_comment_count|total_count)":(\d+)', html)
                if rx_comments:
                    data["comments"] = rx_comments[0]

            if not data["shares"]:
                rx_shares = re.findall(r'"share_count":\{"count":(\d+)\}', html)
                if rx_shares:
                    data["shares"] = rx_shares[0]

            if not data["timestamp"]:
                rx_time = re.findall(r'"(?:creation_time|publish_time)":(\d{10})', html)
                if rx_time:
                    data["timestamp"] = rx_time[0]

        except Exception as error:
            logger.warning(f"Fast-path fetch error: {error}")

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


def _process_slash_command(interaction: dict):
    """
    Hàm xử lý ngầm (Background Task) cho Slash Command /fbembbed:
    1. Trích xuất URL Facebook từ command options trong interaction data.
    2. Kiểm tra tính hợp lệ của URL bằng regex.
    3. Thu thập dữ liệu bài viết Facebook bằng hàm _fetch_fb_data (tốc độ cao).
    4. Nếu là Video: Đặt link stream .mp4 với nhãn [▶️ Video](url) để kích hoạt Video Player trực tiếp.
    5. Nếu là Bài viết: Đóng gói vào Embed (Khung viền màu xanh).
    6. Gửi dữ liệu đã tạo qua Webhook Followup để hoàn tất lệnh.
    """
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

        content_lines = []
        if header_parts:
            content_lines.append(" • ".join(header_parts))
        if caption:
            content_lines.append(caption)

        # Đặt thanh thống kê Like/Comment/Share và nguồn ở dưới nội dung
        if stats_bar:
            content_lines.append(stats_bar)
        if meta_bar:
            content_lines.append(meta_bar)

        # Đặt link stream .mp4 dưới dạng nhãn ngắn gọn [▶️ Video](url)
        # Thay thế hoàn toàn dòng link 500 ký tự thô, đồng thời kích hoạt trình phát video HTML5 của Discord!
        content_lines.append(f"[▶️ Video]({data['video_url']})")

        _send_followup(token, {
            "content": "\n\n".join(content_lines),
            "components": components
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
    logger.info("=== INCOMING EVENT ===")

    # 1. Xác thực chữ ký số request
    try:
        ok, body = _verify(event)
        if not ok:
            return {
                "statusCode": 401,
                "headers": {"Content-Type": "text/plain"},
                "body": "Invalid request signature",
            }
    except Exception as error:
        logger.error(f"Loi xac thuc chu ky: {str(error)}")
        logger.error(traceback.format_exc())
        return {
            "statusCode": 401,
            "headers": {"Content-Type": "text/plain"},
            "body": "Invalid request signature",
        }

    # 2. Phân tích nội dung JSON của interaction
    interaction = json.loads(body)

    # 3. Phản hồi yêu cầu Ping từ Discord (Type 1)
    if interaction.get("type") == 1:
        return _json_response({"type": 1})

    # 4. Xử lý Application Command / Slash Command (Type 2)
    if interaction.get("type") == 2:
        # Chạy tác vụ cào dữ liệu trên luồng nền (Background Thread)
        bg_thread = threading.Thread(target=_process_slash_command, args=(interaction,))
        bg_thread.daemon = True
        bg_thread.start()

        # Trả về ngay Type 5 (DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE) trong < 50ms để tránh timeout 3 giây của Discord
        return _json_response({"type": 5})

    return {"statusCode": 400, "body": "Unhandled interaction type"}