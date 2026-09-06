import os
from dotenv import load_dotenv
import requests

# ==============================================================================
# BƯỚC 1: NẠP CẤU HÌNH VÀ BIẾN MÔI TRƯỜNG
# - Cần APPLICATION_ID và BOT_TOKEN để xác thực với Discord REST API v10.
# ==============================================================================

load_dotenv()

APPLICATION_ID = os.environ.get("APPLICATION_ID")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Endpoint đăng ký Global Application Command với Discord API v10
url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands"

# ==============================================================================
# BƯỚC 2: KHỞI TẠO CẤU TRÚC SLASH COMMAND /fbembbed
# - name: Tên lệnh khi gõ trên Discord (/fbembbed).
# - description: Mô tả ngắn của lệnh hiển thị cho người dùng.
# - options: Danh sách tham số đầu vào (bắt buộc nhập URL bài viết Facebook).
# ==============================================================================

commands = {
    "name": "fbembbed",
    "description": "Embed a Facebook post in Discord",
    "options": [
        {
            "name": "url",
            "description": "The URL of the Facebook post to embed",
            "type": 3,  # 3 đại diện cho String type trong Discord API
            "required": True
        }
    ]
}

# ==============================================================================
# BƯỚC 3: GỬI REQUEST ĐĂNG KÝ COMMAND LÊN DISCORD API
# - Dùng method POST kèm Bot Token trong header Authorization.
# - Discord sẽ trả về mã 200/201 nếu đăng ký thành công.
# ==============================================================================

response = requests.post(
    url,
    json=commands,
    headers={"Authorization": f"Bot {BOT_TOKEN}"}
)

print(response.status_code, response.json())