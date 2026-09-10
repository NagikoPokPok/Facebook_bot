import asyncio
from dataclasses import dataclass, field
import datetime
import logging
import re
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
from cachetools import TTLCache

logger = logging.getLogger(__name__)

# ==============================================================================
# 1. DATA MODELS & EXCEPTIONS
# ==============================================================================

class ThreadsError(Exception):
    """Base exception for Threads fetching errors."""
    pass


class ThreadsInvalidURLError(ThreadsError):
    """Raised when URL does not match valid Threads domain or structure."""
    pass


class ThreadsPostNotFound(ThreadsError):
    """Raised when a post does not exist, was deleted, or is private."""
    pass


@dataclass
class ThreadsPost:
    author_name: str
    author_handle: str
    author_avatar_url: Optional[str]
    text: str
    image_urls: list[str] = field(default_factory=list)
    video_url: Optional[str] = None
    video_thumbnail_url: Optional[str] = None
    profile_url: str = ""
    post_url: str = ""
    posted_at: Optional[datetime.datetime] = None
    is_fallback: bool = False


# ==============================================================================
# 2. CONSTANTS & VALIDATION
# ==============================================================================

# Whitelist allowed domains strictly to prevent SSRF
ALLOWED_DOMAINS = {"threads.net", "www.threads.net", "threads.com", "www.threads.com"}

# Regex matching Threads post URLs:
# e.g.: https://www.threads.net/@zuck/post/CuZsgfWLyiI or https://www.threads.com/t/CuZsgfWLyiI
THREADS_POST_REGEX = re.compile(
    r"^https?://(?:www\.)?threads\.(?:net|com)/(?:@(?P<handle>[a-zA-Z0-9._]+)/post/(?P<post_id>[a-zA-Z0-9_-]+)|t/(?P<tid>[a-zA-Z0-9_-]+))",
    re.IGNORECASE,
)

# Header matching Facebook internal crawler which Threads serves full OpenGraph tags to
SCRAPER_HEADERS = {
    "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Regex to parse author name & handle from og:title:
# e.g.: "Mark Zuckerberg (@zuck) on Threads" or "Mark Zuckerberg (@zuck) · Threads"
TITLE_AUTHOR_REGEX = re.compile(
    r"^(?P<name>.+?)\s*\(@(?P<handle>[a-zA-Z0-9._]+)\)\s*(?:on|•|·|\-)\s*Threads",
    re.IGNORECASE,
)


# ==============================================================================
# 3. FETCHER ENGINE
# ==============================================================================

class ThreadsFetcher:
    """
    Asynchronous Threads Post Fetcher using layered fallback:
    - Layer 0: In-memory TTL Cache (10 mins for posts, 1 hour for profile avatars).
    - Layer 1: Fast-Path Scraper with Meta Crawler UA (extracts full OpenGraph data).
    - Layer 2: Official Tokenless oEmbed (graph.threads.net/oembed).
    - Layer 3: Graceful fallback ThreadsPost with minimal metadata.
    """

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._session = session
        self._owns_session = session is None
        # Cache 500 posts for 10 minutes (600s)
        self.post_cache: TTLCache[str, ThreadsPost] = TTLCache(maxsize=500, ttl=600)
        # Cache 1000 avatars for 1 hour (3600s)
        self.avatar_cache: TTLCache[str, str] = TTLCache(maxsize=1000, ttl=3600)

    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create reusable ClientSession."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=8.0)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._owns_session = True
        return self._session

    async def close(self):
        """Close managed session if owned."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def validate_and_normalize_url(url: str) -> tuple[str, Optional[str], str]:
        """
        Validates URL against domain whitelist and regex structure.
        Returns (canonical_post_url, handle, post_identifier).
        Raises ThreadsInvalidURLError on any violation.
        """
        if not url or not isinstance(url, str):
            raise ThreadsInvalidURLError("Đường dẫn Threads không được để trống.")

        url = url.strip()
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            raise ThreadsInvalidURLError("Giao thức URL không hợp lệ (yêu cầu http hoặc https).")

        domain = (parsed.netloc or "").lower()
        # Enforce whitelist against SSRF attacks
        if domain not in ALLOWED_DOMAINS:
            raise ThreadsInvalidURLError(
                f"Tên miền không được hỗ trợ ({domain}). Chỉ chấp nhận threads.net hoặc threads.com."
            )

        match = THREADS_POST_REGEX.match(url)
        if not match:
            raise ThreadsInvalidURLError(
                "Đường dẫn không đúng định dạng bài viết Threads (ví dụ: https://www.threads.net/@user/post/xxxx)."
            )

        handle = match.group("handle")
        post_id = match.group("post_id") or match.group("tid")
        
        # Standardize canonical URL form
        if handle:
            canonical_url = f"https://www.threads.net/@{handle}/post/{post_id}"
        else:
            canonical_url = f"https://www.threads.net/t/{post_id}"

        return canonical_url, handle, post_id

    async def fetch_post(self, url: str) -> ThreadsPost:
        """
        High-level fetch method with layered fallback and TTL caching.
        """
        canonical_url, extracted_handle, post_id = self.validate_and_normalize_url(url)

        # Layer 0: Check in-memory TTL Cache
        if canonical_url in self.post_cache:
            logger.info(f"Threads Cache HIT for URL: {canonical_url}")
            return self.post_cache[canonical_url]

        logger.info(f"Threads Cache MISS for URL: {canonical_url} - Fetching from network")

        # Layer 1: Fast-Path Scraper (OpenGraph via Meta Scraper Headers)
        try:
            post = await self._fetch_og_scrape(canonical_url, extracted_handle, post_id)
            if post:
                self.post_cache[canonical_url] = post
                return post
        except ThreadsPostNotFound:
            # Propagate 404 / private post immediately, do not fallback
            raise
        except Exception as err:
            logger.warning(f"Layer 1 (OG Scrape) failed for {canonical_url}: {err}. Trying Layer 2 (oEmbed)...")

        # Layer 2: Official Tokenless oEmbed (graph.threads.net/oembed)
        try:
            post = await self._fetch_oembed(canonical_url, extracted_handle, post_id)
            if post:
                self.post_cache[canonical_url] = post
                return post
        except Exception as err:
            logger.warning(f"Layer 2 (oEmbed) failed for {canonical_url}: {err}. Falling back to Layer 3...")

        # Layer 3: Graceful Minimal Fallback
        fallback_post = ThreadsPost(
            author_name=extracted_handle or "Threads User",
            author_handle=extracted_handle or "threads",
            author_avatar_url=None,
            text="Không thể tải nội dung xem trước của bài viết này. Nhấn nút bên dưới để xem trực tiếp trên Threads.",
            image_urls=[],
            video_url=None,
            video_thumbnail_url=None,
            profile_url=f"https://www.threads.net/@{extracted_handle}" if extracted_handle else "https://www.threads.net",
            post_url=canonical_url,
            is_fallback=True,
        )
        return fallback_post

    async def _fetch_og_scrape(
        self, canonical_url: str, extracted_handle: Optional[str], post_id: str
    ) -> Optional[ThreadsPost]:
        """
        Layer 1: Scrapes OpenGraph meta tags using Facebook Crawler User-Agent.
        """
        session = await self.get_session()
        async with session.get(canonical_url, headers=SCRAPER_HEADERS, allow_redirects=True) as resp:
            final_url = str(resp.url)
            text_data = await resp.text()

            # Check if Meta redirected to invalid post error
            if "error=invalid_post" in final_url or resp.status == 404:
                raise ThreadsPostNotFound("Bài viết này không tồn tại, đã bị xóa hoặc đang ở chế độ riêng tư.")

            soup = BeautifulSoup(text_data, "html.parser")

            # Extract meta tags
            og_title = self._get_meta(soup, "og:title") or ""
            og_desc = self._get_meta(soup, "og:description") or self._get_meta(soup, "description") or ""
            og_image = self._get_meta(soup, "og:image") or self._get_meta(soup, "twitter:image")
            og_video = self._get_meta(soup, "og:video") or self._get_meta(soup, "og:video:url")
            og_url = self._get_meta(soup, "og:url") or canonical_url

            # If title indicates Login page or empty content, it is deleted or private
            if "threads • log in" in og_title.lower() or "threads · log in" in og_title.lower():
                raise ThreadsPostNotFound("Bài viết này không tồn tại, đã bị xóa hoặc đang ở chế độ riêng tư.")

            if not og_title and not og_desc and not og_image:
                return None

            # Parse Author Name and Handle from title
            author_name = extracted_handle or "Threads User"
            author_handle = extracted_handle or ""
            title_match = TITLE_AUTHOR_REGEX.match(og_title)
            if title_match:
                author_name = title_match.group("name").strip()
                author_handle = title_match.group("handle").strip()
            elif " (@" in og_title:
                try:
                    parts = og_title.split(" (@")
                    author_name = parts[0].strip()
                    author_handle = parts[1].split(")")[0].strip()
                except Exception:
                    pass

            # Ignore generic default login cards as images
            image_urls: list[str] = []
            if og_image and not self._is_generic_meta_asset(og_image):
                image_urls.append(og_image)

            # Look for multiple og:image tags if available
            all_og_images = soup.find_all("meta", property="og:image")
            for tag in all_og_images:
                c = tag.get("content")
                if c and c not in image_urls and not self._is_generic_meta_asset(c):
                    image_urls.append(c)

            # Author profile URL & Avatar
            profile_url = f"https://www.threads.net/@{author_handle}" if author_handle else canonical_url
            avatar_url = await self._get_author_avatar(author_handle)

            video_thumbnail = og_image if og_video else None

            return ThreadsPost(
                author_name=author_name,
                author_handle=author_handle or "threads",
                author_avatar_url=avatar_url,
                text=og_desc.strip(),
                image_urls=image_urls,
                video_url=og_video,
                video_thumbnail_url=video_thumbnail,
                profile_url=profile_url,
                post_url=canonical_url,
                is_fallback=False,
            )

    async def _fetch_oembed(
        self, canonical_url: str, extracted_handle: Optional[str], post_id: str
    ) -> Optional[ThreadsPost]:
        """
        Layer 2: Calls official tokenless oEmbed API (graph.threads.net/oembed).
        """
        oembed_url = f"https://graph.threads.net/oembed?url={canonical_url}"
        session = await self.get_session()
        async with session.get(oembed_url) as resp:
            if resp.status == 200:
                data = await resp.json()
                author_name = data.get("author_name") or extracted_handle or "Threads User"
                author_handle = extracted_handle or "threads"
                profile_url = f"https://www.threads.net/@{author_handle}"

                # Extract text if possible from blockquote html
                html = data.get("html", "")
                text = ""
                if html:
                    soup = BeautifulSoup(html, "html.parser")
                    text = soup.get_text(separator=" ", strip=True)
                    if "View on Threads" in text:
                        text = text.replace("View on Threads", "").strip()

                avatar_url = await self._get_author_avatar(author_handle) if author_handle else None

                return ThreadsPost(
                    author_name=author_name,
                    author_handle=author_handle,
                    author_avatar_url=avatar_url,
                    text=text or "Nhấn vào nút để xem bài viết trên Threads.",
                    image_urls=[],
                    video_url=None,
                    video_thumbnail_url=None,
                    profile_url=profile_url,
                    post_url=canonical_url,
                    is_fallback=False,
                )
            elif resp.status in (400, 404):
                raise ThreadsPostNotFound("Bài viết này không tồn tại, đã bị xóa hoặc đang ở chế độ riêng tư.")
            return None

    async def _get_author_avatar(self, handle: Optional[str]) -> Optional[str]:
        """
        Retrieves author avatar image URL with in-memory TTL caching (1 hour).
        """
        if not handle:
            return None

        handle = handle.lower().strip()
        if handle in self.avatar_cache:
            return self.avatar_cache[handle]

        try:
            profile_url = f"https://www.threads.net/@{handle}"
            session = await self.get_session()
            async with session.get(profile_url, headers=SCRAPER_HEADERS, allow_redirects=True) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, "html.parser")
                    og_img = self._get_meta(soup, "og:image")
                    if og_img and not self._is_generic_meta_asset(og_img):
                        self.avatar_cache[handle] = og_img
                        return og_img
        except Exception as err:
            logger.debug(f"Failed to fetch avatar for @{handle}: {err}")

        return None

    @staticmethod
    def _get_meta(soup: BeautifulSoup, prop_or_name: str) -> Optional[str]:
        """Helper to extract content of meta tag by property or name."""
        tag = soup.find("meta", property=prop_or_name)
        if not tag:
            tag = soup.find("meta", attrs={"name": prop_or_name})
        return tag.get("content") if tag else None

    @staticmethod
    def _is_generic_meta_asset(url: str) -> bool:
        """Filters out default Threads placeholder logos and login icons."""
        if not url:
            return True
        bad_keywords = ["kHwIMM5b8PW.webp", "GC4Jc-xramm.ico", "0Qa-AOmHi0c.ico"]
        return any(kw in url for kw in bad_keywords)
