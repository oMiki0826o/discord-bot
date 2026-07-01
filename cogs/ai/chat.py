"""
cogs/ai/chat.py

職責：
- 監聽 Discord mention 訊息，作為 AI 對話的薄入口
- 處理附件分流（圖片多模態 Part / file_parser 解析）
- 管理每位使用者的並發鎖與冷卻機制
- 長回覆自動轉換為 .txt 附件

Modification():

- 使用每位使用者獨立 asyncio.Lock 取代全域 bool，避免並發請求競態
- 附件上限、冷卻秒數與使用者提示文案改由 settings.json 控制
- AI listener 會略過已被辨識為前綴指令的訊息，避免與 mention 對話互相干擾
- 附件仍分流為 file_parser 解析結果或 Gemini 圖片 Part，單一附件失敗不終止整體流程
- 補上 from __future__ import annotations（原版遺漏，Python 3.11+ 的 PEP 563 相容性）
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
from pathlib import Path

import discord
from discord.ext import commands
from google.genai import types

from core.ai.core import generate
from core.ai.file_parser import parse as parse_file
from core.ai.file_parser.constants import IMAGE_EXTENSIONS, MAX_IMAGE_SIZE
from core.ai.file_parser.models import ParsedFile
from core.system.settings import get_float, get_int, get_str

logger = logging.getLogger("bot.ai.chat")

# ── 圖片 MIME 對照 ──────────────────────

_IMAGE_MIME: dict[str, str] = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif":  "image/gif",
}

# ── Cog ──────────────────────

class Chat(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._user_locks: dict[int, asyncio.Lock] = {}
        self._user_cooldown: dict[int, float] = {}

    # ── 清理 Mention ──────────────────────

    def parse_prompt(self, content: str) -> str:
        """

from __future__ import annotations

        從使用者訊息移除 bot 的 mention 標記，取得純文字 prompt。
        Discord mention 有兩種格式：<@ID> 與 <@!ID>（舊格式含驚嘆號）。
        """
        if self.bot.user is None:
            return content.strip()

        return (
            content
            .replace(f"<@{self.bot.user.id}>", "")
            .replace(f"<@!{self.bot.user.id}>", "")
            .strip()
        )

    # ── 使用者狀態 ──────────────────────

    def _lock_for(self, user_id: int) -> asyncio.Lock:
        """取得使用者專屬鎖，避免同一使用者同時觸發多個 AI 請求。"""
        lock = self._user_locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[user_id] = lock
        return lock

    # ── Cooldown 檢查 ──────────────────────

    def check_cooldown(self, user_id: int) -> bool:
        """
        回傳 True 表示通過冷卻、可以繼續；False 表示還在冷卻中。
        同時更新最後請求時間戳。
        使用 monotonic clock，不受系統時鐘調整影響。
        """
        cooldown_seconds = max(0.0, get_float("ai.cooldown_seconds", 3.0))
        now = asyncio.get_running_loop().time()
        last = self._user_cooldown.get(user_id, 0.0)
        if now - last < cooldown_seconds:
            return False
        self._user_cooldown[user_id] = now
        return True

    def _cooldown_message(self) -> str:
        """依設定檔產生冷卻提示。"""
        seconds = max(0.0, get_float("ai.cooldown_seconds", 3.0))
        template = get_str("ai.cooldown_message_template", "請稍等 {seconds:g} 秒再試")
        try:
            return template.format(seconds=seconds)
        except (KeyError, ValueError):
            return f"請稍等 {seconds:g} 秒再試"

    # ── 附件處理 ──────────────────────

    async def process_attachments(
        self,
        attachments: list[discord.Attachment],
    ) -> tuple[list[ParsedFile], list[types.Part]]:
        """
        將附件分流為「file_parser 解析結果」與「圖片多模態 Part」。
        單一附件失敗只記錄 log，不影響其他附件或整體對話流程。
        """
        files:       list[ParsedFile] = []
        image_parts: list[types.Part] = []
        max_attachments = max(0, get_int("ai.max_attachments", 5))

        for attachment in attachments[:max_attachments]:
            ext = Path(attachment.filename).suffix.lower()
            try:
                if ext in IMAGE_EXTENSIONS:
                    part = await self._read_image(attachment, ext)
                    if part is not None:
                        image_parts.append(part)
                else:
                    parsed = await self._parse_attachment(attachment)
                    if parsed is not None:
                        files.append(parsed)
            except Exception as e:
                logger.warning(
                    "[process_attachments] 附件處理失敗 filename=%s: %s",
                    attachment.filename, e,
                )

        if len(attachments) > max_attachments:
            logger.info(
                "[process_attachments] 附件數量 %d 超過上限 %d，僅處理前 %d 個",
                len(attachments), max_attachments, max_attachments,
            )

        return files, image_parts

    async def _read_image(
        self,
        attachment: discord.Attachment,
        ext:        str,
    ) -> types.Part | None:
        """讀取圖片 bytes 並組成 Gemini multimodal Part。"""
        if attachment.size > MAX_IMAGE_SIZE:
            logger.info(
                "[_read_image] 圖片過大 filename=%s size=%d limit=%d",
                attachment.filename, attachment.size, MAX_IMAGE_SIZE,
            )
            return None

        data      = await attachment.read()
        mime_type = attachment.content_type or _IMAGE_MIME.get(ext, "image/png")
        return types.Part.from_bytes(data=data, mime_type=mime_type)

    async def _parse_attachment(
        self,
        attachment: discord.Attachment,
    ) -> ParsedFile | None:
        """寫入暫存檔後交由 file_parser 解析，確保暫存檔最終會被刪除。"""
        suffix  = Path(attachment.filename).suffix
        tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=suffix)
        tmp_path = Path(tmp_path_str)

        try:
            os.close(tmp_fd)   # 只需要路徑，立即關閉 fd 改用 attachment.save 寫入
            await attachment.save(tmp_path)
            return await parse_file(tmp_path, filename=attachment.filename)
        finally:
            tmp_path.unlink(missing_ok=True)

    # ── 送出回覆 ──────────────────────

    async def send_response(
        self,
        thinking: discord.Message,
        text:     str,
        original: discord.Message,
    ) -> None:
        """
        決策流程：
        1. text 為空 → edit 為（回覆為空），避免 error 50006
        2. text ≤ ai.max_reply_length → edit「思考中...」訊息
        3. text > ai.max_reply_length → 刪除「思考中...」，改傳 .txt 附件
        """
        if not text or not text.strip():
            await self._safe_edit(thinking, get_str("ai.empty_reply_message", "（回覆為空）"))
            return

        if len(text) <= max(1, get_int("ai.max_reply_length", 1500)):
            await self._safe_edit(thinking, text)
            return

        # ── 長回覆：轉成 txt 附件 ──────────────────────
        try:
            await thinking.delete()
        except discord.HTTPException:
            pass   # 已被刪除或無權限，忽略

        buf  = io.BytesIO(text.encode("utf-8"))
        file = discord.File(buf, filename="response.txt")
        await original.reply(content=get_str("ai.long_reply_notice", "回覆內容較長，請見附件"), file=file)

    async def _safe_edit(
        self,
        message: discord.Message,
        content: str,
    ) -> None:
        """
        包裝 message.edit()，靜默忽略 error 50006（空訊息）。
        其他 HTTPException 繼續往上傳遞，由 handle_ai 的 except 捕捉並 log。
        """
        try:
            await message.edit(content=content)
        except discord.HTTPException as e:
            if e.code == 50006:
                return
            raise

    # ── AI 主流程 ──────────────────────

    async def handle_ai(
        self,
        message: discord.Message,
        prompt:  str,
    ) -> None:
        """
        完整的 AI 請求流程：
        1. 冷卻 & 鎖定檢查
        2. 解析附件（圖片 Part / file_parser 結果）
        3. 送出「思考中...」佔位訊息
        4. 等待 generate() 回傳完整文字
        5. 根據長度決定更新方式
        """
        user_id = message.author.id
        lock = self._lock_for(user_id)

        # ── 鎖定檢查（防止並發請求） ──────────────────────
        if lock.locked():
            await message.reply(get_str("ai.busy_message", "正在處理上一個請求，請稍後"))
            return

        # ── 冷卻檢查 ──────────────────────
        if not self.check_cooldown(user_id):
            await message.reply(self._cooldown_message())
            return

        async with lock:
            # ── 附件解析（鎖定後才處理，避免並發請求重複下載） ──────────────────────
            files, image_parts = await self.process_attachments(message.attachments)

            thinking = await message.reply(get_str("ai.thinking_message", "思考中..."))

            try:
                text = await generate(
                    user=message.author,
                    prompt=prompt,
                    channel_id=str(message.channel.id),
                    files=files,
                    image_parts=image_parts,
                )
                await self.send_response(thinking, text, original=message)

            except Exception as e:
                logger.exception(
                    "[handle_ai] error user=%s: %s", user_id, e,
                )
                template = get_str("ai.error_message_template", "錯誤：{error}")
                try:
                    error_message = template.format(error=type(e).__name__)
                except (KeyError, ValueError):
                    error_message = f"錯誤：{type(e).__name__}"
                await self._safe_edit(thinking, error_message)

    # ── 指令訊息判斷 ──────────────────────

    async def _is_command_message(self, message: discord.Message) -> bool:
        """避免 AI listener 處理已被 commands.Bot 辨識為前綴指令的訊息。"""
        context = await self.bot.get_context(message)
        return context.valid

    # ── 事件入口 ──────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """只在被 mention 時才回應，忽略 bot 自己的訊息。"""
        if message.author.bot:
            return
        if await self._is_command_message(message):
            return

        bot_user = self.bot.user
        if bot_user is None or bot_user not in message.mentions:
            return

        prompt = self.parse_prompt(message.content)
        if not prompt and not message.attachments:
            await message.reply(get_str("ai.empty_prompt_message", "請輸入想問的內容"))
            return

        # ── 預設附件提示 ──────────────────────
        if not prompt:
            prompt = get_str("ai.default_attachment_prompt", "請看一下這個附件並告訴我內容。")

        await self.handle_ai(message, prompt)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Chat(bot))
