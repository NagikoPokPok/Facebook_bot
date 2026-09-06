import os
from dotenv import load_dotenv
from flask import Flask, request, Response
from main import lambda_handler

# ==============================================================================
# BƯỚC 1: KHỞI TẠO BIẾN MÔI TRƯỜNG VÀ FLASK SERVER
# - Nạp file .env để lấy các giá trị cấu hình cần thiết.
# - Khởi tạo ứng dụng Flask để làm cầu nối nhận webhook HTTP từ Discord khi test local.
# ==============================================================================

load_dotenv()

app = Flask(__name__)


# ==============================================================================
# BƯỚC 2: ROUTE /interactions ĐÓN NHẬN REQUEST TỪ DISCORD
# - Chuyển đổi HTTP request từ Flask sang định dạng event của AWS Lambda.
# - Gọi lambda_handler để xử lý xác thực chữ ký số và phản hồi tương tác.
# - Trả kết quả về cho Discord với đúng HTTP status code và headers.
# ==============================================================================

@app.route("/interactions", methods=["POST"])
def interactions():
    # Chuyển đổi request của Flask thành định dạng event mà Lambda handler yêu cầu
    event = {
        "headers": dict(request.headers),
        "body": request.get_data(as_text=True),
        "isBase64Encoded": False,
    }

    # Chuyển tiếp event cho lambda_handler xử lý
    result = lambda_handler(event, None)

    # Đóng gói kết quả trả về cho Discord
    return Response(
        result.get("body", ""),
        status=result.get("statusCode", 200),
        headers=result.get("headers", {"Content-Type": "application/json"}),
    )


# ==============================================================================
# BƯỚC 3: CHẠY SERVER TẠI PORT 5000
# ==============================================================================

if __name__ == "__main__":
    app.run(port=5000)