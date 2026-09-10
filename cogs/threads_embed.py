import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from threads_fetcher import (
    ThreadsFetcher,
    ThreadsError,
    ThreadsInvalidURLError,
    ThreadsPostNotFound,
)
from threads_embed_builder import build_threads_embeds

logger = logging.getLogger(__name__)


class ThreadsEmbedCog(commands.Cog, name="Threads Embed"):
    """
    Discord Cog providing slash commands to fetch and embed Threads posts.
    """

    def __init__(self, bot: commands.Bot, fetcher: Optional[ThreadsFetcher] = None):
        self.bot = bot
        self.fetcher = fetcher or ThreadsFetcher()

    async def cog_unload(self):
        """Clean up HTTP sessions when cog is unloaded."""
        await self.fetcher.close()

    async def _handle_threads_command(self, interaction: discord.Interaction, url: str):
        """
        Shared execution flow for /threads and /th commands:
        1. Validates URL locally to prevent SSRF and invalid domain calls.
        2. Defers response immediately (avoids Discord 3.0s timeout).
        3. Asynchronously fetches post metadata via layered fallback engine.
        4. Handles edge cases (post deleted, private, invalid URL).
        5. Sends rich embeds + action view publicly in channel.
        """
        # 1. Validate URL before deferral or network request
        try:
            self.fetcher.validate_and_normalize_url(url)
        except ThreadsInvalidURLError as val_err:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"⚠️ {str(val_err)}",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(f"⚠️ {str(val_err)}", ephemeral=True)
            return

        # 2. Defer interaction (shows 'Bot is thinking...')
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True)

        # 3. Fetch data via layered fallback engine
        try:
            post = await self.fetcher.fetch_post(url)
        except ThreadsPostNotFound:
            await interaction.followup.send(
                "⚠️ Bài viết này không tồn tại, đã bị xóa hoặc đang ở chế độ riêng tư.",
                ephemeral=True,
            )
            return
        except ThreadsInvalidURLError as err:
            await interaction.followup.send(f"⚠️ {str(err)}", ephemeral=True)
            return
        except Exception as err:
            logger.error(f"Unexpected error in /threads command: {err}", exc_info=True)
            await interaction.followup.send(
                "⚠️ Đã xảy ra lỗi khi tải bài viết Threads. Vui lòng thử lại sau.",
                ephemeral=True,
            )
            return

        # 4. Build rich embed and action view
        embeds, view = build_threads_embeds(post)

        # 5. Publicly send embed result
        await interaction.followup.send(embeds=embeds, view=view)

    # ==========================================================================
    # SLASH COMMAND: /threads
    # ==========================================================================
    @app_commands.command(
        name="threads",
        description="Fetch & hiển thị bài viết từ Threads dưới dạng rich embed đẹp",
    )
    @app_commands.describe(url="Đường link bài viết Threads (threads.net hoặc threads.com)")
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
    async def threads_command(self, interaction: discord.Interaction, url: str):
        await self._handle_threads_command(interaction, url)

    # ==========================================================================
    # SLASH COMMAND ALIAS: /th
    # ==========================================================================
    @app_commands.command(
        name="th",
        description="Alias rút gọn của lệnh /threads",
    )
    @app_commands.describe(url="Đường link bài viết Threads (threads.net hoặc threads.com)")
    @app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)
    async def th_command(self, interaction: discord.Interaction, url: str):
        await self._handle_threads_command(interaction, url)

    # ==========================================================================
    # COOLDOWN ERROR HANDLER
    # ==========================================================================
    @threads_command.error
    @th_command.error
    async def on_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Bạn thao tác quá nhanh. Vui lòng đợi thêm {error.retry_after:.1f}s trước khi gửi lệnh tiếp theo."
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        else:
            logger.error(f"Command error: {error}")
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ Lỗi không xác định khi thực hiện lệnh.", ephemeral=True)
            else:
                await interaction.followup.send("⚠️ Lỗi không xác định khi thực hiện lệnh.", ephemeral=True)


async def setup(bot: commands.Bot):
    """Entry point for discord.py cog loading."""
    await bot.add_cog(ThreadsEmbedCog(bot))
