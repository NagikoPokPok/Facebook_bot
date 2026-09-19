import asyncio
from dataclasses import dataclass, field
import datetime
import logging
import re
from typing import Optional
from urllib.parse import urlparse, urljoin

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
# e.g.: https://www.threads.net/@zuck/post/CuZsgfWLyiI, https://www.threads.com/t/CuZsgfWLyiI,
# https://www.threads.com/post/CuZsgfWLyiI, or https://www.threads.com/share/IqfJJdeHW/
THREADS_POST_REGEX = re.compile(
    r"^https?://(?:www\.)?threads\.(?:net|com)/(?:@(?P<handle>[a-zA-Z0-9._]+)/post/(?P<post_id>[a-zA-Z0-9_-]+)|t/(?P<tid>[a-zA-Z0-9_-]+)|post/(?P<pid>[a-zA-Z0-9_-]+)|share/(?P<share_id>[a-zA-Z0-9_-]+))",
    re.IGNORECASE,
)

# Header matching Facebook internal crawler which Threads serves full OpenGraph tags to
SCRAPER_HEADERS = {
    "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Regex to parse author name & handle from og:title:
# e.g.: "Mark Zuckerberg (@zuck) on Threads", "Mark Zuckerberg (@zuck) trên Threads"
TITLE_AUTHOR_REGEX = re.compile(
    r"^(?P<name>.+?)\s*\(@(?P<handle>[a-zA-Z0-9._]+)\)\s*(?:on|trên|•|·|\-)\s*Threads",
    re.IGNORECASE,
)

# ponytail: Bắt trường hợp og:title chỉ có handle: "@xmawmx trên Threads", "xmawmx on Threads", "@xmawmx • Threads"
TITLE_HANDLE_ONLY_REGEX = re.compile(
    r"^@?(?P<handle>[a-zA-Z0-9._]+)\s*(?:on|trên|•|·|\-)\s*Threads",
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
        # ponytail: Cache profile HTML for 60 seconds (max 50 profiles)
        self.profile_cache: TTLCache[str, str] = TTLCache(maxsize=50, ttl=60)

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

        domain = (parsed.netloc or "").lower().split(":")[0]
        # Enforce whitelist against SSRF attacks
        if domain not in ALLOWED_DOMAINS:
            raise ThreadsInvalidURLError(
                f"Tên miền không được hỗ trợ ({domain}). Chỉ chấp nhận threads.net hoặc threads.com."
            )

        match = THREADS_POST_REGEX.match(url)
        if not match:
            raise ThreadsInvalidURLError(
                "Đường dẫn không đúng định dạng bài viết Threads (ví dụ: https://www.threads.net/@user/post/xxxx hoặc https://www.threads.com/share/xxxx)."
            )

        handle = match.group("handle")
        post_id = match.group("post_id") or match.group("tid") or match.group("pid")
        share_id = match.group("share_id")

        # Standardize canonical URL form
        # ponytail: Chuẩn hóa canonical URL theo dạng chuẩn threads.net, giữ nguyên share_id nếu là link share
        if handle and post_id:
            canonical_url = f"https://www.threads.net/@{handle}/post/{post_id}"
            ident = post_id
        elif post_id:
            canonical_url = f"https://www.threads.net/t/{post_id}"
            ident = post_id
        elif share_id:
            canonical_url = f"https://www.threads.net/share/{share_id}"
            ident = share_id
        else:
            canonical_url = url
            ident = ""

        return canonical_url, handle, ident

    async def _resolve_share_url(self, share_url: str) -> tuple[str, Optional[str], str]:
        """
        ponytail: Resolve Threads /share/ shortlink to canonical post URL via HTTP 302 Location header.
        """
        session = await self.get_session()
        try:
            async with session.get(share_url, allow_redirects=False) as resp:
                loc = resp.headers.get("Location")
                if loc:
                    if "error=invalid_post" in loc:
                        raise ThreadsPostNotFound("Bài viết này không tồn tại, đã bị xóa hoặc đang ở chế độ riêng tư.")
                    parsed_loc = urlparse(loc)
                    if not parsed_loc.netloc:
                        loc = urljoin(share_url, loc)
                    return self.validate_and_normalize_url(loc)
        except ThreadsPostNotFound:
            raise
        except Exception as err:
            logger.debug(f"Could not pre-resolve share URL {share_url}: {err}")
        return share_url, None, ""

    async def fetch_post(self, url: str) -> ThreadsPost:
        """
        High-level fetch method with layered fallback and TTL caching.
        """
        canonical_url, extracted_handle, post_id = self.validate_and_normalize_url(url)

        # Layer 0: Check in-memory TTL Cache (check both original input and canonical)
        if url in self.post_cache:
            logger.info(f"Threads Cache HIT for input URL: {url}")
            return self.post_cache[url]
        if canonical_url in self.post_cache:
            logger.info(f"Threads Cache HIT for URL: {canonical_url}")
            return self.post_cache[canonical_url]

        # ponytail: Nếu là link chia sẻ /share/, resolve HTTP redirect để lấy canonical post URL và handle
        if "/share/" in canonical_url:
            resolved_url, resolved_handle, resolved_id = await self._resolve_share_url(canonical_url)
            if resolved_url != canonical_url:
                if resolved_url in self.post_cache:
                    logger.info(f"Threads Cache HIT for resolved URL: {resolved_url}")
                    cached_post = self.post_cache[resolved_url]
                    self.post_cache[canonical_url] = cached_post
                    self.post_cache[url] = cached_post
                    return cached_post
                canonical_url = resolved_url
                if resolved_handle:
                    extracted_handle = resolved_handle
                if resolved_id:
                    post_id = resolved_id

        logger.info(f"Threads Cache MISS for URL: {canonical_url} - Fetching from network")

        # Layer 1: Fast-Path Scraper (OpenGraph via Meta Scraper Headers)
        try:
            post = await self._fetch_og_scrape(canonical_url, extracted_handle, post_id)
            if post:
                self.post_cache[url] = post
                self.post_cache[canonical_url] = post
                if post.post_url and post.post_url not in self.post_cache:
                    self.post_cache[post.post_url] = post
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
                self.post_cache[url] = post
                self.post_cache[canonical_url] = post
                if post.post_url and post.post_url not in self.post_cache:
                    self.post_cache[post.post_url] = post
                return post
        except ThreadsPostNotFound:
            raise
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
        self.post_cache[url] = fallback_post
        self.post_cache[canonical_url] = fallback_post
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
            og_video = (
                self._get_meta(soup, "og:video")
                or self._get_meta(soup, "og:video:url")
                or self._get_meta(soup, "og:video:secure_url")
                or self._get_meta(soup, "twitter:player:stream")
            )
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
            else:
                # ponytail: Thử match dạng @handle on/trên/• Threads khi không có display name riêng
                handle_match = TITLE_HANDLE_ONLY_REGEX.match(og_title)
                if handle_match:
                    h = handle_match.group("handle").strip()
                    author_handle = h
                    if not author_name or author_name == "Threads User":
                        author_name = h

            # ponytail: Nếu author_handle vẫn rỗng hoặc mặc định, trích xuất từ og_url, final_url hoặc canonical_url
            if not author_handle or author_handle.lower() == "threads":
                for u in (og_url, final_url, canonical_url):
                    m = re.search(r"/@([a-zA-Z0-9._]+)", u or "")
                    if m:
                        author_handle = m.group(1)
                        if not author_name or author_name == "Threads User":
                            author_name = author_handle
                        break

            # Author profile URL & Avatar
            profile_url = f"https://www.threads.net/@{author_handle}" if author_handle else canonical_url
            avatar_url = await self._get_author_avatar(author_handle)

            # ponytail: Nếu trang cá nhân không có avatar, kiểm tra xem og:image của post có phải ảnh đại diện (-19)
            if not avatar_url and og_image and self._is_avatar_asset(og_image):
                avatar_url = og_image
                if author_handle:
                    self.avatar_cache[author_handle] = avatar_url

            # ponytail: Trích xuất ảnh gốc sạch (clean media) từ SSR JSON của profile (chia sẻ chung request với avatar)
            clean_images = await self._get_clean_post_images(author_handle, post_id)

            image_urls: list[str] = []
            if clean_images:
                # ponytail: có ảnh sạch từ SSR → dùng luôn, bỏ card
                image_urls = clean_images
            else:
                # ponytail: không có clean images → dùng og:image nhưng loại synthesized card
                if (
                    og_image
                    and not self._is_generic_meta_asset(og_image)
                    and not self._is_avatar_asset(og_image)
                    and not self._is_synthesized_card(og_image)
                ):
                    image_urls.append(og_image)

                # Look for multiple og:image tags if available
                all_og_images = soup.find_all("meta", property="og:image")
                for tag in all_og_images:
                    c = tag.get("content")
                    if (
                        c
                        and c not in image_urls
                        and not self._is_generic_meta_asset(c)
                        and not self._is_avatar_asset(c)
                        and not self._is_synthesized_card(c)
                    ):
                        image_urls.append(c)

            video_thumbnail = None
            if og_video:
                video_thumbnail = clean_images[0] if clean_images else og_image

            # ponytail: Ưu tiên link post trực tiếp từ og:url nếu có thay vì link /share/ hoặc /t/
            final_post_url = og_url if (og_url and "threads." in og_url and "/post/" in og_url) else canonical_url

            return ThreadsPost(
                author_name=author_name,
                author_handle=author_handle or "threads",
                author_avatar_url=avatar_url,
                text=og_desc.strip(),
                image_urls=image_urls,
                video_url=og_video,
                video_thumbnail_url=video_thumbnail,
                profile_url=profile_url,
                post_url=final_post_url,
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

    async def _fetch_profile_html(self, handle: Optional[str]) -> Optional[str]:
        """
        ponytail: Fetch and cache author profile HTML (60s TTL).
        Profile SSR JSON contains both author avatar and clean post media.
        """
        if not handle:
            return None
        handle = handle.lower().strip()
        if handle in self.profile_cache:
            return self.profile_cache[handle]

        try:
            profile_url = f"https://www.threads.net/@{handle}"
            session = await self.get_session()
            async with session.get(profile_url, headers=SCRAPER_HEADERS, allow_redirects=True) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    self.profile_cache[handle] = html
                    return html
        except Exception as err:
            logger.debug(f"Failed to fetch profile HTML for @{handle}: {err}")

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
            html = await self._fetch_profile_html(handle)
            if html:
                soup = BeautifulSoup(html, "html.parser")
                og_img = self._get_meta(soup, "og:image")
                if og_img and not self._is_generic_meta_asset(og_img):
                    self.avatar_cache[handle] = og_img
                    return og_img
        except Exception as err:
            logger.debug(f"Failed to extract avatar for @{handle}: {err}")

        return None

    async def _get_clean_post_images(self, handle: Optional[str], post_id: Optional[str]) -> list[str]:
        """
        ponytail: Trích xuất ảnh gốc sạch (clean media) từ SSR JSON của profile.
        Tận dụng profile HTML đã được tải và cache khi lấy avatar.
        """
        if not handle or not post_id:
            return []

        try:
            html = await self._fetch_profile_html(handle)
            if html:
                return self._extract_clean_images_from_ssr(html, post_id)
        except Exception as err:
            logger.debug(f"Failed to extract clean post images for @{handle}/{post_id}: {err}")

        return []

    @staticmethod
    def _extract_clean_images_from_ssr(html: str, post_id: str) -> list[str]:
        """
        ponytail: Trích xuất ảnh gốc sạch (t51.*-15) từ SSR JSON trong HTML profile Threads.
        Hỗ trợ cả single photo và carousel multi-photo post.
        Loại bỏ hoàn toàn card t39.92108-6 bị ghép text/khung.
        """
        if not html or not post_id:
            return []

        html_clean = html.replace(r"\/", "/")
        target = f'"code":"{post_id}"'
        idx = html_clean.find(target)
        if idx == -1:
            # Fallback tìm post_id dạng raw nếu không có "code":"
            idx = html_clean.find(post_id)
            if idx == -1:
                # ponytail: log SSR miss để debug — bài cũ có thể không nằm trong SSR profile
                logger.info(f"SSR miss: post_id={post_id} not found in profile HTML ({len(html_clean)} chars)")
                return []
            logger.info(f"SSR: post_id={post_id} found via raw match (not 'code' key) at idx={idx}")

        # Kiểm tra carousel_media trước post_id
        # Trong schema của Instagram/Threads, carousel_media đứng ngay trước post code
        prev_chunk = html_clean[max(0, idx - 35000) : idx]
        last_post_code = prev_chunk.rfind('"code":"')
        carousel_start = prev_chunk.find('"carousel_media":[')

        if carousel_start != -1 and (last_post_code == -1 or carousel_start > last_post_code):
            target_chunk = prev_chunk[carousel_start:]
        else:
            # Single image post: image_versions2 đứng ngay sau post code
            next_chunk = html_clean[idx : min(len(html_clean), idx + 8000)]
            target_chunk = next_chunk

        # Tìm tất cả link ảnh t51.*-15 trong đoạn JSON của post
        raw_urls = re.findall(
            r'https://[^\s"\'<>]*(?:fbcdn\.net|cdninstagram\.com)/v/t51\.[0-9]+-15/[^\s"\'<>]+',
            target_chunk,
        )
        if not raw_urls:
            # ponytail: post nằm trong SSR nhưng không có ảnh t51-15 → text-only hoặc video-only
            logger.info(f"SSR: post_id={post_id} found in SSR but no t51.*-15 image URLs in chunk ({len(target_chunk)} chars)")
            return []

        # Nhóm theo tên file asset để chọn bản độ phân giải cao nhất
        assets: dict[str, tuple[int, str]] = {}
        for u in raw_urls:
            u = u.replace("&amp;", "&").replace(r"\u0025", "%")
            fn = u.split("/")[-1].split("?")[0]

            # Đánh điểm độ phân giải
            score = 500
            if "1080x1080" in u or "s1080" in u:
                score = 1080
            elif "720x720" in u or "s720" in u:
                score = 720
            elif "648x648" in u or "s648" in u or "dst-jpg_e35_tt6" in u:
                score = 650
            elif "640x640" in u or "s640" in u:
                score = 640
            elif "480x480" in u or "s480" in u:
                score = 480
            elif "320x320" in u or "s320" in u:
                score = 320
            elif "240x240" in u or "s240" in u:
                score = 240
            elif "150x150" in u or "s150" in u:
                score = 150

            if fn not in assets or score > assets[fn][0]:
                assets[fn] = (score, u)

        return [u for score, u in assets.values()]

    @staticmethod
    def _is_synthesized_card(url: str) -> bool:
        """
        ponytail: Phát hiện ảnh Social Share Card do Meta tự tổng hợp (t39.92108-6),
        vốn bị đóng khung trắng, cắt cúp (crop 1200x628) và dán đè text + avatar lên ảnh.
        """
        if not url:
            return False
        return "t39.92108-6" in url or "/t39.92108-6/" in url

    @staticmethod
    def _is_avatar_asset(url: str) -> bool:
        """
        ponytail: Phát hiện ảnh đại diện người dùng từ CDN Instagram (t51.*-19).
        Không đưa nhầm ảnh đại diện vào danh sách ảnh đính kèm bài viết.
        """
        if not url:
            return False
        return "-19/" in url or "t51.2885-19" in url or "t51.82787-19" in url or "anonymous_profile_pic" in url

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
        bad_keywords = ["kHwIMM5b8PW.webp", "GC4Jc-xramm.ico", "0Qa-AOmHi0c.ico", ".ico", "anonymous_profile_pic"]
        return any(kw in url for kw in bad_keywords)
