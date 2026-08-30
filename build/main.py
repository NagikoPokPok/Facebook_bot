import base64
import json
import os
import re

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
import requests

load_dotenv() 

DISCORD_PUBLIC_KEY = os.environ.get("DISCORD_PUBLIC_KEY")

FB_url_REGEX = re.compile(
    r"(https?://(?:www\.|m\.|web\.|mbasic\.)?(?:facebook\.com|fb\.watch)/\S+)",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
}

verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))


def _raw_body(event: dict) -> str:
    ''' Function to extract the raw body from the event, decoding it if it's base64 encoded'''
    body = event.get("body", "")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    return body


def _verify(event: dict):
    ''' Function to verify the request signature from Discord'''
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    signature = headers.get("x-signature-ed25519")
    timestamp = headers.get("x-signature-timestamp")
    body = _raw_body(event)

    if not signature or not timestamp:
        return False, body
    try:
        verify_key.verify(f"{timestamp}{body}".encode(), bytes.fromhex(signature))
        return True, body
    except (BadSignatureError, ValueError):
        return False, body


def _fetch_fb_data(url: str) -> dict:
    """
    Fetches Facebook data from the given URL.
    Prefers the full text from the mbasic version (no JS needed, not truncated
    by Facebook), and falls back to Open Graph meta tags if that fails.

        1. Normalize the URL to the mbasic.facebook.com version to get plain
        HTML that's easier to parse.
        2. Try to find the block containing the main post content in the
        mbasic page.
        - Use a few common selectors since Facebook frequently changes its
        HTML structure over time.
        - If a content block is found, grab the longest text chunk inside it
        (usually the post caption).
        3. Also grab the thumbnail image and title from the mbasic page if
        available.
        4. If steps 2-3 fail to get a description or image (mbasic blocked,
        structure changed, private post...), fall back to the old approach:
        read Open Graph meta tags (og:title, og:description, og:image) from
        the original URL.
        5. Always return a dict with all 5 fields (title, description, image,
        site_name, url); any field that couldn't be fetched stays None, so
        lambda_handler can handle it when building the embed.
    """

    # Step 1: normalize URL to the mbasic version (strip www./m./web., use mbasic.)
    mbasic_url = re.sub(
        r"(?:www\.|m\.|web\.)?facebook\.com",
        "mbasic.facebook.com",
        url,
        flags=re.IGNORECASE,
    )

    data = {
        "title": None,
        "description": None,
        "image": None,
        "site_name": "Facebook",
        "url": url,
    }

    # Step 2: try to get full text + image + title from mbasic first
    try:
        resp = requests.get(mbasic_url, headers=HEADERS, allow_redirects=True, timeout=5)
        data["url"] = str(resp.url)
        soup = BeautifulSoup(resp.text, "html.parser")

        # Try a few common mbasic selectors, since Facebook changes class/id names often
        content_div = (
            soup.select_one("div#m_story_permalink_view")
            or soup.select_one("div[data-ft]")
            or soup.select_one("div.story_body_container")
        )

        if content_div:
            # Grab the longest text chunk inside the block - usually the actual post content
            # (other short chunks are typically button labels, author name, timestamp, etc.)
            candidates = [
                tag.get_text(" ", strip=True)
                for tag in content_div.find_all(["p", "div", "span"])
                if tag.get_text(strip=True)
            ]
            if candidates:
                full_text = max(candidates, key=len)
                if len(full_text) > 30:  # filter out junk text that's too short
                    data["description"] = full_text

            # Image: grab the first img tag inside the content block
            img_tag = content_div.find("img")
            if img_tag and img_tag.get("src"):
                data["image"] = img_tag["src"]

        # Title: read from the mbasic page's <title> tag
        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            data["title"] = title_tag.get_text(strip=True)

    except Exception:
        pass  # fine, will fall back to step 3

    # Step 3: if mbasic couldn't get description or image, fall back to Open Graph
    if not data["description"] and not data["image"]:
        try:
            resp = requests.get(url, headers=HEADERS, allow_redirects=True, timeout=5)
            soup = BeautifulSoup(resp.text, "html.parser")

            def og(property: str) -> str:
                tag = soup.find("meta", property=property)
                return tag["content"].strip() if tag and tag.get("content") else None

            data["title"] = data["title"] or og("og:title")
            data["description"] = data["description"] or og("og:description")
            data["image"] = data["image"] or og("og:image")
            data["site_name"] = og("og:site_name") or data["site_name"]
            data["url"] = str(resp.url)
        except Exception:
            pass  # if both approaches fail, lambda_handler will report "Failed to fetch"

    return data


def _json_response(payload: dict) -> dict:
    ''' Function to create a JSON response for API Gateway'''
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }


def lambda_handler(event, context):
    ''' Lambda function handler to process incoming requests from Discord'''

    # 1. Verify the request signature
    ok, body = _verify(event)
    if not ok:
        return {"statusCode": 401, "body": "Invalid request signature"}

    # 2. Parse and turn the body into a JSON object
    interaction = json.loads(body)

    # 3. Classify the type of interaction and respond accordingly
    # Type 1: Ping
    if interaction.get("type") == 1:
        return _json_response({"type": 1})

    # Type 2: Application Command
    ''' 
        1. Extract the command options from the interaction data
        2. Traverse the options to find the Facebook URL provided by the user.
        - Use the next() function to iterate through the options and find the one with the name "url".
        - If found, store the value of that option in the variable fb_url.
        3. Check if the fb_url is valid and matches the expected Facebook URL pattern using the FB_url_REGEX.
        - Fetch the Facebook data using the provided URL
        - Use try and except to handle any exceptions that may occur during the data fetching process
            + Like timeouts, network errors, or unexpected HTML structure.
        4. Check if the fetched data contains the necessary fields (title, description, image).
        - If any of these fields are missing, return an error response indicating that the data could
        not be fetched successfully.
        5. Embed the fetched data into a Discord message and return it as a response to the interaction.
        6. Return a 400 status code for any unhandled interaction types.
    '''
     # Slash command /fbembbed
    if interaction.get("type") == 2:
        options = interaction.get("data", {}).get("options", [])
        fb_url = next((o["value"] for o in options if o["name"] == "url"), None)

        if not fb_url or not FB_url_REGEX.match(fb_url):
            return _json_response({
                "type": 4,
                "data": {
                    "content": "Invalid Facebook URL provided. Please provide a valid Facebook post URL.",
                    "flags": 64  # Ephemeral message - only visible to the user who invoked the command
                }
            })

        try:
            data = _fetch_fb_data(fb_url)
        except Exception:
            data = {}

        if not (data.get("title") or data.get("description") or data.get("image")):
            return _json_response({
                "type": 4,
                "data": {
                    "content": "Failed to fetch data from the provided Facebook URL. Please ensure the URL is correct and try again.",
                    "flags": 64
                }
            })

        embed = {
            "title": (data.get("title") or "Bài viết Facebook")[:256],
            "description": (data.get("description") or "")[:2048],
            "url": data.get("url") or fb_url,
            "color": 0x1877F2,
        }

        if data.get("image"):
            embed["image"] = {"url": data["image"]}

        return _json_response({
            "type": 4,
            "data": {
                "embeds": [embed],
                "components": [{
                    "type": 1,
                    "components": [{
                        "type": 2,
                        "label": "Xem trên Facebook",
                        "style": 5,
                        "url": data.get("url") or fb_url
                    }]
                }],
            }
        })

    return {"statusCode": 400, "body": "Unhandled interaction type"}