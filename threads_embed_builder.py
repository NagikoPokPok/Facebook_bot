import datetime
from typing import Optional

import discord

from threads_fetcher import ThreadsPost

# Threads Brand constants
THREADS_COLOR = 0x101010  # Premium dark theme
THREADS_ICON_URL = "https://static.cdninstagram.com/rsrc.php/yP/r/0Qa-AOmHi0c.ico"
MAX_DESCRIPTION_LENGTH = 900
MAX_GALLERY_IMAGES = 4


def truncate_description(text: str, post_url: str, max_length: int = MAX_DESCRIPTION_LENGTH) -> str:
    """
    Truncates description gracefully if it exceeds max_length,
    appending an ellipsis and markdown link to the full post.
    """
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_length:
        return text

    # Truncate at word boundary
    truncated = text[:max_length]
    last_space = truncated.rfind(" ")
    if last_space != -1 and last_space > max_length - 50:
        truncated = truncated[:last_space]

    return f"{truncated}… [xem đầy đủ]({post_url})"


def build_threads_embeds(post: ThreadsPost) -> tuple[list[discord.Embed], discord.ui.View]:
    """
    Constructs rich Discord Embeds and UI Action View for a Threads post:
    - Primary embed with author, formatted text, timestamp, and first image.
    - Additional gallery embeds (up to 4 images) using Discord's native multi-image grid.
    - View with action link buttons to the original post and author profile.
    """
    embeds: list[discord.Embed] = []
    
    # 1. Base description preparation
    description = truncate_description(post.text, post.post_url)
    if post.video_url or post.video_thumbnail_url:
        video_notice = "🎥 *Bài viết có video — nhấn nút bên dưới để xem*"
        description = f"{description}\n\n{video_notice}".strip()

    # 2. Main Embed
    main_embed = discord.Embed(
        description=description if description else None,
        color=THREADS_COLOR,
        url=post.post_url,
        timestamp=post.posted_at or datetime.datetime.now(datetime.timezone.utc),
    )

    # Author header
    author_name = f"{post.author_name} (@{post.author_handle})" if post.author_handle else post.author_name
    main_embed.set_author(
        name=author_name[:256],
        icon_url=post.author_avatar_url or THREADS_ICON_URL,
        url=post.profile_url or post.post_url,
    )

    # Footer
    footer_text = "Threads"
    if len(post.image_urls) > MAX_GALLERY_IMAGES:
        extra_count = len(post.image_urls) - MAX_GALLERY_IMAGES
        footer_text = f"Threads • +{extra_count} ảnh khác"
    
    main_embed.set_footer(
        text=footer_text,
        icon_url=THREADS_ICON_URL,
    )

    # Set image for Main Embed
    images_to_show = post.image_urls[:MAX_GALLERY_IMAGES]
    if images_to_show:
        main_embed.set_image(url=images_to_show[0])
    elif post.video_thumbnail_url:
        main_embed.set_image(url=post.video_thumbnail_url)

    embeds.append(main_embed)

    # 3. Multi-image Carousel / Gallery (Discord multi-embed grid)
    # Discord displays an image gallery grid when multiple embeds share the same url.
    if len(images_to_show) > 1:
        for extra_img in images_to_show[1:]:
            extra_embed = discord.Embed(url=post.post_url)
            extra_embed.set_image(url=extra_img)
            embeds.append(extra_embed)

    # 4. Action View (Buttons)
    view = discord.ui.View()
    view.add_item(
        discord.ui.Button(
            label="🔗 Xem bài viết gốc",
            style=discord.ButtonStyle.link,
            url=post.post_url,
        )
    )
    if post.profile_url and post.author_handle:
        view.add_item(
            discord.ui.Button(
                label="👤 Xem trang cá nhân",
                style=discord.ButtonStyle.link,
                url=post.profile_url,
            )
        )

    return embeds, view


def build_threads_payload_dict(post: ThreadsPost) -> dict:
    """
    Builds a raw Discord REST API payload dictionary for serverless / webhook environments
    (compatible with main.py / AWS Lambda / Flask).
    """
    embeds, view = build_threads_embeds(post)
    raw_embeds = [embed.to_dict() for embed in embeds]

    # Build components for Action Row
    components = []
    buttons = []
    buttons.append({
        "type": 2,  # Button
        "style": 5,  # Link
        "label": "🔗 Xem bài viết gốc",
        "url": post.post_url,
    })
    if post.profile_url and post.author_handle:
        buttons.append({
            "type": 2,
            "style": 5,
            "label": "👤 Xem trang cá nhân",
            "url": post.profile_url,
        })
    if buttons:
        components.append({
            "type": 1,  # Action Row
            "components": buttons,
        })

    return {
        "embeds": raw_embeds,
        "components": components,
    }
