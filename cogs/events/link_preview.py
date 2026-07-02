"""
cogs/events/link_preview.py

職責：
- 監聽伺服器訊息，涵蓋兩種獨立功能：
  1. 被動預覽：偵測 Discord 原生 Embed 支援不佳的連結（Bilibili、
     Instagram、Threads、Pinterest），自動擷取資訊並組成 Embed 回覆。
  2. 關鍵字摘要：訊息出現「摘要」等關鍵字並緊接任意網址時，爬取
     該網址的網頁純文字，透過 Gemma 生成摘要後回覆。

Modification():

- 新增本檔案：取代舊有的 cogs/events/bilibili.py，整合多平台連結
  預覽（Bilibili / Instagram / Threads / Pinterest）與關鍵字摘要。
- 新增 Pinterest 支援，被動預覽清單擴充為四種平台。
- 新增關鍵字觸發的通用網頁摘要，與被動預覽路徑完全獨立，
  只要訊息含關鍵字 + 任意網址就會觸發，不限定支援平台。
- Embed 內文加上長度防護（_truncate），避免極長的原始簡介超過
  Discord embed description 4096 字元上限。
- 行程內有界快取（OrderedDict）：避免同一連結短時間重複貼出時
  重複發送外部請求，大小由 settings.json link_preview.cache_size 控制。

設計原則：
- 平台判斷、擷取邏輯、摘要邏輯皆下放到 core/link_preview，本檔案
  只負責「訊息事件 → 呼叫核心邏輯 → 組裝 Embed → 回覆」。
- 新增平台時只需在 core/link_preview/registry.py 新增一筆，
  本 Cog 不需修改。
- 所有數量上限、關鍵字、逾時秒數等皆讀取 settings.json。
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
from core.link_preview.video import download_if_within_limit
from core.system.settings import get_int, get_str
from utils.discord_errors import friendly_http_error

logger = logging.getLogger("bot.events.link_preview")


# ── 連結預覽 Cog ──────────────────────

class LinkPreviewCog(commands.Cog):
    """
    處理兩類獨立功能：
    - 被動預覽：Bilibili / Instagram / Threads / Pinterest
    - 關鍵字摘要：「摘要」+ 任意網址
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # url -> LinkPreview：行程內有界 LRU 快取，避免短時間內
        # 相同連結重複觸發外部請求。Bot 重啟後清空，屬可接受行為。
        self._cache: OrderedDict[str, LinkPreview] = OrderedDict()

    # ── 訊息事件入口 ──────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """只處理伺服器訊息；私訊由 cogs/events/message.py 處理。"""
        if message.author.bot or message.guild is None:
            return
        if not get_flag("link_preview.enabled", True):
            return

        try:
            await self._handle_summary_request(message)
            await self._handle_passive_previews(message)
        except Exception:
            logger.exception(
                "[連結預覽] on_message 發生未預期例外 author=%s", message.author
            )

    # ── 關鍵字摘要 ──────────────────────

    async def _handle_summary_request(self, message: discord.Message) -> None:
        """
        「摘要」+ 網址觸發的通用摘要功能，不限定平台。

        與被動預覽完全獨立：即使網址是 Bilibili 等已支援平台，
        關鍵字觸發後仍會爬取網頁純文字另行摘要（來源不同）。
        關鍵字可由 link_preview.summary_keyword 設定（預設「摘要」）。
        """
        keyword = get_str("link_preview.summary_keyword", "摘要")
        url     = find_summary_request(message.content, keyword=keyword)
        if url is None:
            return

        fail_message  = get_str(
            "link_preview.summary_fail_message",
            "無法擷取這個網址的內容，可能是網站封鎖爬取或內容非純文字頁面。",
        )
        fetch_max_chars = get_int("link_preview.summary_fetch_max_chars", 6000)

        text = await fetch_text(url, max_chars=fetch_max_chars)
        if not text:
            await self._safe_reply(message, fail_message)
            return

        result = await summarize(text)
        if not result:
            await self._safe_reply(message, fail_message)
            return

        await self._safe_reply(message, f"**摘要**\n{result}")

    # ── 被動預覽 ──────────────────────

    async def _handle_passive_previews(self, message: discord.Message) -> None:
        """Bilibili / Instagram / Threads / Pinterest 連結的自動預覽。"""
        links = detect_links(message.content)
        if not links:
            return

        max_links = max(1, get_int("link_preview.max_embeds_per_message", 3))
        for platform, url in links[:max_links]:
            await self._handle_link(message, platform, url)

    async def _handle_link(
        self,
        message:  discord.Message,
        platform: str,
        url:      str,
    ) -> None:
        """處理單一連結：擷取、（選用）摘要、組裝 Embed、回覆。"""
        preview = await self._get_preview(platform, url)
        if preview is None:
            logger.info("[連結預覽] 擷取失敗，略過 platform=%s url=%s", platform, url)
            return

        await self._maybe_summarize(preview)

        embed = self._build_embed(preview)
        file  = await self._maybe_build_video_file(preview)

        try:
            if file is not None:
                await message.reply(embed=embed, file=file, mention_author=False)
            else:
                await message.reply(embed=embed, mention_author=False)
        except discord.HTTPException as exc:
            logger.error(
                "[連結預覽] 回覆失敗 url=%s reason=%s", url, friendly_http_error(exc)
            )
            return

        await self._try_suppress_original_embed(message)

    # ── 擷取（含快取） ──────────────────────

    async def _get_preview(self, platform: str, url: str) -> LinkPreview | None:
        """先查快取，沒有才呼叫對應擷取器並寫入快取。"""
        cached = self._cache.get(url)
        if cached is not None:
            self._cache.move_to_end(url)
            return cached

        extractor = get_extractor(platform)
        if extractor is None:
            return None

        try:
            preview = await extractor(url)
        except Exception:
            logger.exception(
                "[連結預覽] 擷取器發生例外 platform=%s url=%s", platform, url
            )
            return None

        if preview is None:
            return None

        self._cache[url] = preview
        self._cache.move_to_end(url)

        limit = max(1, get_int("link_preview.cache_size", 200))
        while len(self._cache) > limit:
            self._cache.popitem(last=False)

        return preview

    # ── 被動預覽的自動摘要 ──────────────────────

    async def _maybe_summarize(self, preview: LinkPreview) -> None:
        """簡介內容夠長時才呼叫 Gemma 生成摘要，避免短文字耗用 API 額度。"""
        if not preview.description:
            return
        min_chars = get_int("link_preview.summary_trigger_min_chars", 60)
        if len(preview.description) < min_chars:
            return
        preview.summary = await summarize(preview.description)

    # ── Embed 組裝 ──────────────────────

    def _build_embed(self, preview: LinkPreview) -> discord.Embed:
        """組裝 Embed：作者列（平台）、來源、統計、標題、說明、縮圖。"""
        max_desc_chars = get_int("link_preview.embed_description_max_chars", 800)

        lines: list[str] = [preview.source_label, ""]

        if preview.stats:
            lines.append("　".join(
                f"{stat.icon} {stat.value}" for stat in preview.stats
            ))
            lines.append("")

        if preview.author:
            lines.append(f"**{preview.author}**")

        if preview.title:
            lines.append(f"**{preview.title}**")

        body = self._truncate(preview.summary or preview.description, max_desc_chars)
        if body:
            lines.append("")
            lines.append(body)

        embed = discord.Embed(
            description = "\n".join(lines),
            url         = preview.url,
            color       = preview.color,
        )
        embed.set_author(name=preview.platform_label)
        if preview.thumbnail_url:
            embed.set_image(url=preview.thumbnail_url)
        embed.set_footer(text=preview.platform_label)
        return embed

    @staticmethod
    def _truncate(text: str | None, limit: int) -> str | None:
        """文字超過 limit 時截斷並附加省略符號，保護 Embed 長度上限。"""
        if text is None or len(text) <= limit:
            return text
        return text[: max(0, limit - 1)].rstrip() + "..."

    # ── 影片附件 ──────────────────────

    async def _maybe_build_video_file(self, preview: LinkPreview) -> discord.File | None:
        """
        影片網址存在時嘗試下載並包裝為附件；超過大小上限或下載失敗
        則回傳 None，退回「只顯示縮圖 + 連結」的呈現方式。
        """
        if not preview.video_url:
            return None
        if not get_flag("link_preview.attach_video", True):
            return None

        buffer = await download_if_within_limit(
            preview.video_url, referer=preview.url
        )
        if buffer is None:
            return None

        return discord.File(buffer, filename=f"{preview.platform}.mp4")

    # ── 抑制原生 Embed ──────────────────────

    async def _try_suppress_original_embed(self, message: discord.Message) -> None:
        """
        若 Bot 具備「管理訊息」權限，抑制原訊息可能產生的低品質原生
        Embed，避免畫面同時出現兩份預覽。權限不足時安靜略過。
        """
        permissions = message.channel.permissions_for(message.guild.me)
        if not permissions.manage_messages:
            return
        try:
            await message.edit(suppress=True)
        except discord.HTTPException:
            pass

    # ── 共用回覆工具 ──────────────────────

    async def _safe_reply(self, message: discord.Message, content: str) -> None:
        """統一的回覆包裝，失敗時記錄詳細原因而不中斷整體流程。"""
        try:
            await message.reply(content, mention_author=False)
        except discord.HTTPException as exc:
            logger.error(
                "[連結預覽] 回覆訊息失敗 reason=%s", friendly_http_error(exc)
            )


# ── Extension 入口 ──────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LinkPreviewCog(bot))
