"""
cogs/ai/chat.py

修正（整合附件解析）：
- on_message 新增附件處理：偵測 message.attachments，依副檔名分流
  - 圖片（IMAGE_EXTENSIONS）：直接讀取 bytes 組成 Gemini types.Part，
    不經過 file_parser（Gemini 系列本身即多模態模型，省去 OCR/Vision）
  - 其他副檔名：寫入暫存檔後呼叫 file_parser.parse()，取得 ParsedFile，
    暫存檔案於 finally 區塊保證刪除，避免磁碟堆積
  - 附件數量上限 MAX_ATTACHMENTS，防止單則訊息夾帶過多檔案拖慢回應
  - 單一附件處理失敗只記錄 log，不中斷整體流程（呼應 file_parser 設計原則）
- handle_ai() 新增 files / image_parts 參數，轉交 generate()
- 其餘職責不變：監聽 on_message、管理冷卻與請求鎖定、長回覆轉 .txt 附件
"""

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
from core.system.settings import get as _s
from core.ai.file_parser import parse as parse_file
from core.ai.file_parser.models import ParsedFile
from core.ai.file_parser.constants import IMAGE_EXTENSIONS, MAX_IMAGE_SIZE

logger = logging.getLogger("bot.ai.chat")

# ── 全域狀態 ──────────────────────────────────────────────────────────
# Bot 重啟後自動清空，不需要持久化

user_locks:    dict[int, bool]  = {}
user_cooldown: dict[int, float] = {}

# 冷卻秒數 / 回覆長度上限：從 settings.json 動態讀取，免重啟即生效
MAX_ATTACHMENTS  = 5       # 單則訊息最多處理的附件數量

# 副檔名 → MIME type（image Part 需要，Discord content_type 有時為 None）
_IMAGE_MIME: dict[str, str] = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif":  "image/gif",
}

# ── Cog ──────────────────────────────────────────────────────────────

class Chat(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── 清理 Mention ────────────────────────────────────────────────

    def parse_prompt(self, content: str) -> str:
        """
        從使用者訊息移除 bot 的 mention 標記，取得純文字 prompt。
        Discord mention 有兩種格式：<@ID> 與 <@!ID>（舊格式含驚嘆號）。
        """
        return (
            content
            .replace(f"<@{self.bot.user.id}>", "")
            .replace(f"<@!{self.bot.user.id}>", "")
            .strip()
        )

    # ── Cooldown 檢查 ────────────────────────────────────────────────

    def check_cooldown(self, user_id: int) -> bool:
        """
        回傳 True 表示通過冷卻、可以繼續；False 表示還在冷卻中。
        同時更新最後請求時間戳。
        使用 monotonic clock，不受系統時鐘調整影響。
        """
        now  = asyncio.get_running_loop().time()
        last = user_cooldown.get(user_id, 0.0)
        if now - last < float(_s('ai.cooldown_seconds', 3.0)):
            return False
        user_cooldown[user_id] = now
        return True

    # ── 附件處理 ─────────────────────────────────────────────────────

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

        for attachment in attachments[:MAX_ATTACHMENTS]:
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

        if len(attachments) > MAX_ATTACHMENTS:
            logger.info(
                "[process_attachments] 附件數量 %d 超過上限 %d，僅處理前 %d 個",
                len(attachments), MAX_ATTACHMENTS, MAX_ATTACHMENTS,
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

    # ── 送出回覆 ─────────────────────────────────────────────────────

    async def send_response(
        self,
        thinking: discord.Message,
        text:     str,
        original: discord.Message,
    ) -> None:
        """
        決策流程：
        1. text 為空 → edit 為（回覆為空），避免 error 50006
        2. text ≤ int(_s('ai.max_reply_length', 1500)) → edit「思考中...」訊息
        3. text > int(_s('ai.max_reply_length', 1500)) → 刪除「思考中...」，改傳 .txt 附件
        """
        if not text or not text.strip():
            await self._safe_edit(thinking, "（回覆為空）")
            return

        if len(text) <= int(_s('ai.max_reply_length', 1500)):
            await self._safe_edit(thinking, text)
            return

        # ── 長回覆：轉成 txt 附件 ──────────────────────────────
        try:
            await thinking.delete()
        except discord.HTTPException:
            pass   # 已被刪除或無權限，忽略

        buf  = io.BytesIO(text.encode("utf-8"))
        file = discord.File(buf, filename="response.txt")
        await original.reply(content="回覆內容較長，請見附件", file=file)

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

    # ── AI 主流程 ─────────────────────────────────────────────────────

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

        # ── 冷卻檢查 ───────────────────────────────────────────
        if not self.check_cooldown(user_id):
            await message.reply(f"請稍等 {float(_s('ai.cooldown_seconds', 3.0))} 秒再試")
            return

        # ── 鎖定檢查（防止並發請求）───────────────────────────
        if user_locks.get(user_id):
            await message.reply("正在處理上一個請求，請稍後")
            return

        user_locks[user_id] = True

        # ── 附件解析（鎖定後才處理，避免並發請求重複下載）────
        files, image_parts = await self.process_attachments(message.attachments)

        thinking = await message.reply("思考中...")

        try:
            text = await generate(
                message.author, prompt,
                files=files, image_parts=image_parts,
            )
            await self.send_response(thinking, text, original=message)

        except Exception as e:
            logger.exception(
                "[handle_ai] error user=%s: %s", user_id, e,
            )
            await self._safe_edit(thinking, f"錯誤：{type(e).__name__}")

        finally:
            # 無論成功或失敗都要解鎖，否則使用者永久無法再發請求
            user_locks[user_id] = False

    # ── 事件入口 ──────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """只在被 mention 時才回應，忽略 bot 自己的訊息。"""
        if message.author.bot:
            return
        if self.bot.user not in message.mentions:
            return

        prompt = self.parse_prompt(message.content)
        if not prompt and not message.attachments:
            await message.reply("請輸入想問的內容")
            return

        # 只夾帶附件、沒有文字時，給一個預設提示讓 AI 知道要看附件
        if not prompt:
            prompt = "請看一下這個附件並告訴我內容。"

        await self.handle_ai(message, prompt)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Chat(bot))
