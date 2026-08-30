from dotenv import load_dotenv
load_dotenv()

from flask import Flask, request, Response
from main import lambda_handler 

app = Flask(__name__)


@app.route("/interactions", methods=["POST"])
def interactions():
    # Convert HTTP request of Flask to the event format expected by the lambda_handler
    event = {
        "headers": dict(request.headers),
        "body": request.get_data(as_text=True),
        "isBase64Encoded": False,
    }

    result = lambda_handler(event, None)

    return Response(
        result.get("body", ""),
        status=result.get("statusCode", 200),
        headers=result.get("headers", {"Content-Type": "application/json"}),
    )


if __name__ == "__main__":
    app.run(port=5000)