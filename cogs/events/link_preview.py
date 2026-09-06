
"""
cogs/events/link_preview.py

Link preview Cog.

Bilibili 特殊流程：
- 不呼叫 Bilibili API
- 不建立資訊 Embed
- 不下載影片
- 直接產生 vxbilibili.com 修復連結
- 回覆一則：
      bilibili（https://www.vxbilibili.com/video/BVxxxxxxxxxx/）
- 回覆成功後抑制原使用者訊息的 Discord Embed

其他平台維持原本的 LinkPreview 流程。
"""

from __future__ import annotations

import logging
from collections import OrderedDict

import discord
from discord.ext import commands

from core.link_preview.article import fetch_text
from core.link_preview.base import LinkPreview
from core.link_preview.detector import detect_links
from core.link_preview.flags import get_flag
from core.link_preview.registry import get_extractor
from core.link_preview.summarizer import summarize
from core.link_preview.summary_trigger import find_summary_request
from core.system.settings import get_int, get_str
from utils.discord_errors import friendly_http_error


logger = logging.getLogger("bot.events.link_preview")


# ─────────────────────────────────────────────
# Link Preview Cog
# ─────────────────────────────────────────────


class LinkPreviewCog(commands.Cog):
    """
    處理兩類功能：

    1. 被動預覽
       Bilibili / Instagram / Threads / Pinterest / Twitter / TikTok

    2. 關鍵字摘要
       「摘要」+ 任意網址

    Bilibili 使用特殊的極簡修復流程：
        bilibili（vxbilibili URL）
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # url -> LinkPreview
        #
        # 其他平台仍使用快取。
        # Bilibili 也會沿用此機制，但不會進行 Bilibili API 請求。
        self._cache: OrderedDict[str, LinkPreview] = OrderedDict()

    # ─────────────────────────────────────────
    # Message event
    # ─────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """只處理伺服器訊息；Bot 訊息與私訊略過。"""

        if message.author.bot or message.guild is None:
            return

        if not get_flag("link_preview.enabled", True):
            return

        try:
            await self._handle_summary_request(message)
            await self._handle_passive_previews(message)

        except Exception:
            logger.exception(
                "[連結預覽] on_message 發生未預期例外 author=%s",
                message.author,
            )

    # ─────────────────────────────────────────
    # Summary
    # ─────────────────────────────────────────

    async def _handle_summary_request(
        self,
        message: discord.Message,
    ) -> None:
        """
        「摘要」+ 網址的通用摘要功能。

        與被動預覽完全獨立。
        """

        keyword = get_str(
            "link_preview.summary_keyword",
            "摘要",
        )

        url = find_summary_request(
            message.content,
            keyword=keyword,
        )

        if url is None:
            return

        fail_message = get_str(
            "link_preview.summary_fail_message",
            "無法擷取這個網址的內容，可能是網站封鎖爬取或內容非純文字頁面。",
        )

        fetch_max_chars = get_int(
            "link_preview.summary_fetch_max_chars",
            6000,
        )

        text = await fetch_text(
            url,
            max_chars=fetch_max_chars,
        )

        if not text:
            await self._safe_reply(
                message,
                fail_message,
            )
            return

        result = await summarize(text)

        if not result:
            await self._safe_reply(
                message,
                fail_message,
            )
            return

        await self._safe_reply(
            message,
            f"**摘要**\n{result}",
        )

    # ─────────────────────────────────────────
    # Passive previews
    # ─────────────────────────────────────────

    async def _handle_passive_previews(
        self,
        message: discord.Message,
    ) -> None:
        """處理訊息中的支援平台連結。"""

        links = detect_links(message.content)

        if not links:
            return

        max_links = max(
            1,
            get_int(
                "link_preview.max_embeds_per_message",
                3,
            ),
        )

        for platform, url in links[:max_links]:
            await self._handle_link(
                message,
                platform,
                url,
            )

    async def _handle_link(
        self,
        message: discord.Message,
        platform: str,
        url: str,
    ) -> None:
        """
        處理單一連結。

        Bilibili：
            使用極簡修復流程。

        其他平台：
            使用原本的 LinkPreview 流程。
        """

        # ─────────────────────────────────────
        # Bilibili special flow
        # ─────────────────────────────────────

        if platform.lower() == "bilibili":
            await self._handle_bilibili(
                message,
                url,
            )
            return

        # ─────────────────────────────────────
        # Other platforms
        # ─────────────────────────────────────

        preview = await self._get_preview(
            platform,
            url,
        )

        if preview is None:
            logger.info(
                "[連結預覽] 擷取失敗，略過 platform=%s url=%s",
                platform,
                url,
            )
            return

        await self._maybe_summarize(preview)

        has_video = (
            bool(preview.embed_video_link)
            and get_flag(
                "link_preview.attach_video",
                True,
            )
        )

        embed = self._build_embed(
            preview,
            has_video=has_video,
        )

        try:
            if has_video:
                await message.reply(
                    content=preview.embed_video_link,
                    embed=embed,
                    mention_author=False,
                )
            else:
                await message.reply(
                    embed=embed,
                    mention_author=False,
                )

        except discord.HTTPException as exc:
            logger.error(
                "[連結預覽] 回覆失敗 url=%s reason=%s",
                url,
                friendly_http_error(exc),
            )
            return

        await self._try_suppress_original_embed(message)

    # ─────────────────────────────────────────
    # Bilibili
    # ─────────────────────────────────────────

    async def _handle_bilibili(
        self,
        message: discord.Message,
        url: str,
    ) -> None:
        """
        Bilibili 專用流程。

        最終只發送：

            [Bilibili](https://www.vxbilibili.com/video/BVxxxxxxxxxx/)

        不建立 Embed。

        不呼叫 Bilibili API。

        b23.tv 只會由 extractor 進行 HTTP redirect，
        用來取得 BVID。

        回覆成功後，抑制原訊息的 Discord Embed。
        """

        preview = await self._get_preview(
            "bilibili",
            url,
        )

        if preview is None:
            logger.info(
                "[Bilibili] 無法解析連結，略過 url=%s",
                url,
            )
            return

        fixed_link = preview.embed_video_link

        if not fixed_link:
            logger.info(
                "[Bilibili] 沒有可用修復連結 url=%s",
                url,
            )
            return

        content = f"[Bilibili]({fixed_link})"

        try:
            await message.reply(
                content=content,
                mention_author=False,
            )

        except discord.HTTPException as exc:
            logger.error(
                "[Bilibili] 回覆失敗 url=%s reason=%s",
                url,
                friendly_http_error(exc),
            )
            return

        # 回覆成功後，抑制原使用者訊息的 Discord Embed。
        #
        # 注意：
        # 這不會刪除使用者的原始訊息，
        # 只會移除 / 隱藏該訊息在 Discord 中的 Embed 預覽。
        await self._try_suppress_original_embed(message)

    # ─────────────────────────────────────────
    # Preview cache
    # ─────────────────────────────────────────

    async def _get_preview(
        self,
        platform: str,
        url: str,
    ) -> LinkPreview | None:
        """查快取，沒有才呼叫對應擷取器。"""

        cached = self._cache.get(url)

        if cached is not None:
            self._cache.move_to_end(url)
            return cached

        extractor = get_extractor(platform)

        if extractor is None:
            logger.warning(
                "[連結預覽] 找不到擷取器 platform=%s",
                platform,
            )
            return None

        try:
            preview = await extractor(url)

        except Exception:
            logger.exception(
                "[連結預覽] 擷取器發生例外 platform=%s url=%s",
                platform,
                url,
            )
            return None

        if preview is None:
            return None

        self._cache[url] = preview
        self._cache.move_to_end(url)

        limit = max(
            1,
            get_int(
                "link_preview.cache_size",
                200,
            ),
        )

        while len(self._cache) > limit:
            self._cache.popitem(last=False)

        return preview

    # ─────────────────────────────────────────
    # Automatic summary
    # ─────────────────────────────────────────

    async def _maybe_summarize(
        self,
        preview: LinkPreview,
    ) -> None:
        """其他平台的簡介夠長時才產生 AI 摘要。"""

        if not preview.description:
            return

        min_chars = get_int(
            "link_preview.summary_trigger_min_chars",
            60,
        )

        if len(preview.description) < min_chars:
            return

        preview.summary = await summarize(
            preview.description,
        )

    # ─────────────────────────────────────────
    # Embed builder
    # ─────────────────────────────────────────

    def _build_embed(
        self,
        preview: LinkPreview,
        *,
        has_video: bool = False,
    ) -> discord.Embed:
        """建立其他平台使用的資訊 Embed。"""

        max_desc_chars = get_int(
            "link_preview.embed_description_max_chars",
            800,
        )

        lines: list[str] = [
            preview.source_label,
            "",
        ]

        if preview.stats:
            lines.append(
                "　".join(
                    f"{stat.icon} {stat.value}"
                    for stat in preview.stats
                )
            )
            lines.append("")

        if preview.author:
            lines.append(
                f"**{preview.author}**"
            )

        if preview.title:
            lines.append(
                f"**{preview.title}**"
            )

        body = self._truncate(
            preview.summary or preview.description,
            max_desc_chars,
        )

        if body:
            lines.append("")
            lines.append(body)

        lines.append("")
        lines.append(
            f"[查看原始貼文]({preview.url})"
        )

        embed = discord.Embed(
            description="\n".join(lines),
            url=preview.url,
            color=preview.color,
        )

        embed.set_author(
            name=preview.platform_label,
        )

        if preview.thumbnail_url and not has_video:
            embed.set_image(
                url=preview.thumbnail_url,
            )

        embed.set_footer(
            text=preview.platform_label,
        )

        return embed

    # ─────────────────────────────────────────
    # Text helper
    # ─────────────────────────────────────────

    @staticmethod
    def _truncate(
        text: str | None,
        limit: int,
    ) -> str | None:
        """限制 Embed 文字長度。"""

        if text is None or len(text) <= limit:
            return text

        return (
            text[: max(0, limit - 1)].rstrip()
            + "..."
        )

    # ─────────────────────────────────────────
    # Suppress original embeds
    # ─────────────────────────────────────────

    async def _try_suppress_original_embed(
        self,
        message: discord.Message,
    ) -> None:
        """
        抑制原使用者訊息的 Discord Embed。

        這不是刪除訊息。

        效果：

            使用者原訊息：
            https://www.bilibili.com/video/...
            ↓
            Embed 被隱藏

            Bot：
            bilibili（https://www.vxbilibili.com/video/...）

        Bot 必須具備：
            Manage Messages
        """

        if message.guild is None:
            return

        me = message.guild.me

        if me is None:
            return

        permissions = message.channel.permissions_for(me)

        if not permissions.manage_messages:
            logger.debug(
                "[連結預覽] 沒有 Manage Messages 權限，"
                "無法抑制原始 Embed channel=%s message=%s",
                message.channel.id,
                message.id,
            )
            return

        try:
            await message.edit(
                suppress=True,
            )

        except discord.Forbidden:
            logger.warning(
                "[連結預覽] 權限不足，無法抑制原始 Embed message=%s",
                message.id,
            )

        except discord.HTTPException:
            logger.exception(
                "[連結預覽] 抑制原始 Embed 失敗 message=%s",
                message.id,
            )

    # ─────────────────────────────────────────
    # Safe reply
    # ─────────────────────────────────────────

    async def _safe_reply(
        self,
        message: discord.Message,
        content: str,
    ) -> None:
        """安全回覆訊息。"""

        try:
            await message.reply(
                content,
                mention_author=False,
            )

        except discord.HTTPException as exc:
            logger.error(
                "[連結預覽] 回覆訊息失敗 reason=%s",
                friendly_http_error(exc),
            )


# ─────────────────────────────────────────────
# Extension entry
# ─────────────────────────────────────────────


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(
        LinkPreviewCog(bot)
    )

