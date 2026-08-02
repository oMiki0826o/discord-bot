"""
cogs/ai/chat.py

Modification():

- 修正 parse_prompt() 的 docstring：內容裡混入了一行貼錯位置的
  `from __future__ import annotations`（本來應該只出現在檔案最上方
  的 import 區，卻不小心被貼進函式的三引號說明文字中間）。不影響
  執行（純文字，Python 不會把它當成真的 import），但會誤導閱讀者，
  已清除。
- 附件處理（process_attachments 等）與並發鎖 / 冷卻邏輯
  （_lock_for / check_cooldown 等）抽到共用模組 core.ai.attachment_utils
  與 core.ai.request_guard：這兩塊邏輯原本是本 Cog 的實例方法，但
  完全不依賴 self，純粹是無狀態工具函式與跨入口都該共用的節流狀態。
  新增 /ai 這個 slash 指令後，若繼續留在這裡，新指令要嘛複製一份幾乎
  一樣的程式碼，要嘛冷卻與並發鎖各自獨立、使用者交替用 @mention 和
  /ai 就能繞過節流限制。抽出後兩個入口共用同一份邏輯與狀態。
- 使用每位使用者獨立 asyncio.Lock 取代全域 bool，避免並發請求競態
- 附件上限、冷卻秒數與使用者提示文案改由 settings.json 控制
- AI listener 會略過已被辨識為前綴指令的訊息，避免與 mention 對話互相干擾
- 附件仍分流為 file_parser 解析結果或 Gemini 圖片 Part，單一附件失敗不終止整體流程

職責：
- 監聽 Discord mention 訊息，作為 AI 對話的薄入口
- 送出「思考中...」佔位訊息，並依回覆長度決定編輯文字或改傳 .txt 附件
- 與 cogs/ai/ai_command.py（/ai 指令）共用 core.ai.attachment_utils
  與 core.ai.request_guard，是同一套 AI 對話能力的兩種呼叫方式
"""

from __future__ import annotations

import io
import logging

import discord
from discord.ext import commands

from core.ai.attachment_utils import process_attachments
from core.ai.core import generate
from core.ai.request_guard import check_cooldown, cooldown_message, lock_for
from core.system.settings import get_int, get_str

logger = logging.getLogger("bot.ai.chat")


# ── Cog ──────────────────────

class Chat(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── 清理 Mention ──────────────────────

    def parse_prompt(self, content: str) -> str:
        """
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
        lock = lock_for(user_id)

        # ── 鎖定檢查（防止並發請求） ──────────────────────
        if lock.locked():
            await message.reply(get_str("ai.busy_message", "正在處理上一個請求，請稍後"))
            return

        # ── 冷卻檢查 ──────────────────────
        if not check_cooldown(user_id):
            await message.reply(cooldown_message())
            return

        async with lock:
            # ── 附件解析（鎖定後才處理，避免並發請求重複下載） ──────────────────────
            files, image_parts = await process_attachments(message.attachments)

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
