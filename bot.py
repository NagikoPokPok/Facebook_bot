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
# BƯỚC 2: KHỞI TẠO CẤU TRÚC SLASH COMMANDS (/fbembbed, /threads, /th)
# ==============================================================================

commands = [
    {
        "name": "fbembbed",
        "description": "Embed a Facebook post or video in Discord",
        "options": [
            {
                "name": "url",
                "description": "The URL of the Facebook post to embed",
                "type": 3,  # String
                "required": True
            }
        ]
    },
    {
        "name": "threads",
        "description": "Fetch & hiển thị bài viết từ Threads dưới dạng rich embed đẹp",
        "options": [
            {
                "name": "url",
                "description": "The URL of the Threads post (threads.net or threads.com)",
                "type": 3,  # String
                "required": True
            }
        ]
    },
    {
        "name": "th",
        "description": "Alias rút gọn cho lệnh /threads",
        "options": [
            {
                "name": "url",
                "description": "The URL of the Threads post (threads.net or threads.com)",
                "type": 3,  # String
                "required": True
            }
        ]
    }
]

# ==============================================================================
# BƯỚC 3: GỬI REQUEST ĐĂNG KÝ COMMANDS LÊN DISCORD REST API v10
# - Dùng method PUT để bulk overwrite toàn bộ Global Application Commands.
# ==============================================================================

if APPLICATION_ID and BOT_TOKEN:
    response = requests.put(
        url,
        json=commands,
        headers={"Authorization": f"Bot {BOT_TOKEN}"}
    )
    print(f"Status: {response.status_code}")
    try:
        print(response.json())
    except Exception:
        print(response.text)
else:
    print("Thiếu APPLICATION_ID hoặc BOT_TOKEN trong môi trường (.env).")