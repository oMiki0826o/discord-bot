"""
cogs/ai/ai_command.py

Modification():

- 新增本檔案：/ai slash 指令，讓使用者不需要 @提及 Bot 也能開始 AI
  對話，且可在私訊（單人聊天）中使用——mention 對話原本只能在伺服器
  頻道內觸發（訊息需要能 @提及到 Bot），私訊沒有這個入口。
- 與 cogs/ai/chat.py（mention 對話）共用 core.ai.attachment_utils
  （附件處理）與 core.ai.request_guard（並發鎖 + 冷卻），不重新
  實作一份幾乎一樣的邏輯，兩個入口的節流狀態也彼此共用，避免使用者
  交替使用兩種方式繞過冷卻限制。
- 附件參數比照 /say、/webhook 等既有指令的慣例，使用 file1 / file2 /
  file3 三個獨立的選填 discord.Attachment 參數（Discord slash 指令
  的選項無法宣告成「陣列」，仍固定使用最多 3 個附件是與既有指令一致
  的作法，而非隨意的新設計）。
- allowed_contexts(guilds=True, dms=True, private_channels=True)：
  與 say.py 的既有慣例一致，讓指令同時可在伺服器與各種私訊情境使用。
- 新增 model 選填參數（Discord Choice：flash／gemini／gemma），讓
  使用者可透過下拉選單明確指定本次對話要用的模型，不必再依賴 prompt
  文字內嵌關鍵字（如「用flash」）才能間接觸發覆寫；選項清單直接沿用
  agent_router.MODEL_CHOICES 的 key，兩處只維護一份對照表，避免
  日後新增或異動模型時兩邊各自為政、互相脫節。
- model_override 需要 core.ai.core.generate() 對應支援並轉呼叫
  agent_router.route(prompt, model_override=...)；本檔僅負責把
  Discord 端選到的原始字串往下傳，不在此處解析或驗證模型名稱
  （該職責屬於 agent_router，見 agent_router.py 的說明）。

職責：
- 接收使用者輸入的 prompt、選填的手動指定模型、與最多 3 個附件，
  呼叫 core.ai.core.generate() 取得回覆，依長度決定直接回覆文字或
  改傳 .txt 附件。
- Discord 互動有 3 秒內必須回應的限制，AI 生成通常需要數秒到數十秒，
  因此一開始就 defer()，讓 Discord 顯示官方的「思考中」互動狀態，
  真正的回覆改用 followup 送出。
"""

from __future__ import annotations

import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.ai.agent_router import MODEL_CHOICES
from core.ai.attachment_utils import process_attachments
from core.ai.core import generate
from core.ai.request_guard import check_cooldown, cooldown_message, lock_for
from core.system.settings import get_int, get_str

logger = logging.getLogger("bot.ai.ai_command")

# ── /ai 指令可選模型（Discord 下拉選單顯示文字） ──────────────────────
# key 必須與 agent_router.MODEL_CHOICES 完全一致；下方 assert 於
# 模組載入時檢查，避免兩處清單日後修改時彼此脫節而不自知。

_MODEL_CHOICE_LABELS: dict[str, str] = {
    "flash":  "Flash（預設・綜合能力較強）",
    "gemini": "Gemini（支援即時網路搜尋）",
    "gemma":  "Gemma（輕量・回覆較快）",
}

assert _MODEL_CHOICE_LABELS.keys() == MODEL_CHOICES.keys(), (
    "ai_command._MODEL_CHOICE_LABELS 與 agent_router.MODEL_CHOICES 的 "
    "key 不一致，請同步更新兩者"
)


# ── Cog ──────────────────────

class AICommand(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ai", description="與 AI 對話（伺服器頻道或私訊皆可使用）")
    @app_commands.describe(
        prompt = "想問 AI 的內容",
        model  = "手動指定模型（選填，預設自動判斷）",
        file1  = "附件 1（選填，圖片或文件皆可）",
        file2  = "附件 2（選填）",
        file3  = "附件 3（選填）",
    )
    @app_commands.choices(model=[
        app_commands.Choice(name=label, value=key)
        for key, label in _MODEL_CHOICE_LABELS.items()
    ])
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def ai_command(
        self,
        interaction: discord.Interaction,
        prompt:      str,
        model:       app_commands.Choice[str] | None = None,
        file1:       discord.Attachment | None = None,
        file2:       discord.Attachment | None = None,
        file3:       discord.Attachment | None = None,
    ) -> None:
        user_id = interaction.user.id
        lock = lock_for(user_id)

        # ── 鎖定檢查（與 mention 對話共用同一份鎖，防止並發請求） ──────────────────────
        if lock.locked():
            await interaction.response.send_message(
                get_str("ai.busy_message", "正在處理上一個請求，請稍後"),
                ephemeral=True,
            )
            return

        # ── 冷卻檢查（與 mention 對話共用同一份冷卻紀錄） ──────────────────────
        if not check_cooldown(user_id):
            await interaction.response.send_message(cooldown_message(), ephemeral=True)
            return

        # ── 3 秒內必須回應：先 defer，顯示官方「思考中」狀態 ──────────────────────
        # 公開（非 ephemeral），讓最終回覆的呈現方式與 @提及對話一致，
        # 頻道內其他人也能看到問答內容。
        await interaction.response.defer(thinking=True)

        async with lock:
            attachments = [a for a in (file1, file2, file3) if a is not None]
            files, image_parts = await process_attachments(attachments)

            try:
                text = await generate(
                    user           = interaction.user,
                    prompt         = prompt,
                    channel_id     = str(interaction.channel_id),
                    files          = files,
                    image_parts    = image_parts,
                    model_override = model.value if model is not None else None,
                )
                await self._send_response(interaction, text)

            except Exception as e:
                logger.exception("[ai_command] error user=%s: %s", user_id, e)
                template = get_str("ai.error_message_template", "錯誤：{error}")
                try:
                    error_message = template.format(error=type(e).__name__)
                except (KeyError, ValueError):
                    error_message = f"錯誤：{type(e).__name__}"
                await interaction.followup.send(error_message)

    # ── 送出回覆 ──────────────────────

    async def _send_response(
        self,
        interaction: discord.Interaction,
        text:        str,
    ) -> None:
        """
        決策流程與 cogs/ai/chat.py 的 send_response() 一致，只是投遞
        方式改用 interaction.followup（defer 之後就不能再用
        interaction.response）：
        1. text 為空 → 送出「（回覆為空）」提示
        2. text ≤ ai.max_reply_length → 直接送出文字
        3. text > ai.max_reply_length → 改傳 .txt 附件
        """
        if not text or not text.strip():
            await interaction.followup.send(get_str("ai.empty_reply_message", "（回覆為空）"))
            return

        if len(text) <= max(1, get_int("ai.max_reply_length", 1500)):
            await interaction.followup.send(text)
            return

        buf  = io.BytesIO(text.encode("utf-8"))
        file = discord.File(buf, filename="response.txt")
        await interaction.followup.send(
            content = get_str("ai.long_reply_notice", "回覆內容較長，請見附件"),
            file    = file,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AICommand(bot))