"""
cogs/events/message.py

職責：
- 私訊轉發：將一般使用者發送的 DM 轉傳給 Bot Owner
- Owner 回覆橋接：Owner 在 DM 中回覆轉發訊息時，自動送回原私訊者
- 提供 last_dm_user_id 屬性，供 /reply 指令快速回覆最近一筆私訊

Modification():

- 修正 Owner 解析：原本直接使用 application_info().owner，
  當 Bot 應用程式由 Discord Team 擁有時可能解析到無法私訊的對象，
  造成轉發 owner.send() 靜默失敗（只留一行 error log），
  外部觀察起來就像「完全沒有轉發」。改用 utils.owner_resolver
  集中解析（同時正確處理 Team／個人帳號），且每次都先嘗試重新解析
  快取值，避免使用永久卡住的錯誤結果。
- 修正 last_dm_user_id 永遠讀不到的問題：原設計把「記住寄件者」
  與「轉發成功」綁在一起，只要 owner.send() 失敗（例如 Owner
  本人關閉私訊、與 Bot 無共同伺服器等任何原因），_dm_map 就不會
  被寫入，/reply 也就永遠查不到人。現在改為「收到 DM 當下」就先
  記錄寄件者到獨立的 _recent_senders，與轉發是否成功完全脫鉤；
  _dm_map 則保留原本「轉發訊息 ID → 寄件者」的用途，供 Owner
  直接回覆（reply）轉發訊息時使用。
- 轉發失敗時提升記錄詳細度（含例外代碼與可能原因），避免問題
  只留下一行難以排查的 log。

"""

from __future__ import annotations

import logging
from collections import OrderedDict

import discord
from discord.ext import commands

from core.system.settings import get_int, get_str
from utils.discord_errors import friendly_http_error
from utils.owner_resolver import resolve_owner_id

logger = logging.getLogger("bot.events.message")


# ── 私訊橋接 Cog ──────────────────────

class Messenger(commands.Cog):
    """私訊轉發與 Owner 回覆橋接。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        # forward_message_id -> sender_user_id（Owner 回覆橋接用）
        self._dm_map: OrderedDict[int, int] = OrderedDict()

        # sender_user_id -> 最後一次私訊時間戳（/reply 預設目標用；
        # 與轉發是否成功完全無關，只要收到 DM 就會記錄）
        self._recent_senders: OrderedDict[int, float] = OrderedDict()

        self._owner_id:   int | None          = None
        self._owner_user: discord.User | None = None

    # ── 公開屬性 ──────────────────────

    @property
    def last_dm_user_id(self) -> int | None:
        """
        回傳最近一筆私訊者的使用者 ID。

        讀取 _recent_senders（而非 _dm_map），因此即使轉發給 Owner
        失敗，/reply 依然能找到最近私訊 Bot 的人。
        """
        if not self._recent_senders:
            return None
        return next(reversed(self._recent_senders))

    # ── 訊息事件入口 ──────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """只處理 DM；伺服器訊息交給 commands.Bot 預設流程。"""
        if message.author.bot or message.guild is not None:
            return

        if await self._handle_owner_reply(message):
            return

        # ── 無論轉發是否成功，先記住寄件者 ──────────────────────
        self._remember_sender(message.author.id)

        await self._forward_dm_to_owner(message)

    # ── Owner 解析 ──────────────────────

    async def _resolve_owner_id(self) -> int | None:
        """
        解析 Owner 使用者 ID。

        每次呼叫都先嘗試重新解析（resolve_owner_id 內部已有快取邏輯，
        成本低），避免快取到「尚未就緒」或錯誤的結果後永久卡住。
        """
        owner_id = await resolve_owner_id(self.bot)
        if owner_id is not None:
            self._owner_id = owner_id
        return self._owner_id

    async def _resolve_owner(self) -> discord.User | None:
        """取得 Bot Owner 的 discord.User 物件，內部結果會快取。"""
        if self._owner_user is not None:
            return self._owner_user

        owner_id = await self._resolve_owner_id()
        if owner_id is None:
            logger.warning(
                "[DM] 無法解析 Owner ID（config.OWNER_ID 未設定，"
                "且 bot.is_owner() 尚未能判定），私訊轉發暫時無法使用"
            )
            return None

        user = self.bot.get_user(owner_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(owner_id)
            except discord.HTTPException as exc:
                logger.error(
                    "[DM] 取得 Owner 使用者失敗 owner_id=%s: %s",
                    owner_id, friendly_http_error(exc),
                )
                return None

        self._owner_user = user
        return user

    # ── 寄件者 / DM 映射維護 ──────────────────────

    def _remember_sender(self, sender_user_id: int) -> None:
        """
        記錄最近私訊 Bot 的使用者，與轉發是否成功無關。

        使用 OrderedDict 模擬有界 LRU：同一使用者再次私訊時移到尾端，
        確保 last_dm_user_id 永遠反映「最後互動」而非「第一次互動」。
        """
        self._recent_senders[sender_user_id] = discord.utils.utcnow().timestamp()
        self._recent_senders.move_to_end(sender_user_id)

        limit = max(1, get_int("dm.recent_senders_limit", 200))
        while len(self._recent_senders) > limit:
            self._recent_senders.popitem(last=False)

    def _remember_forward(self, forward_message_id: int, sender_user_id: int) -> None:
        """記住 Owner 端轉發訊息與原私訊者的對應關係（供回覆橋接使用）。"""
        self._dm_map[forward_message_id] = sender_user_id
        self._dm_map.move_to_end(forward_message_id)

        limit = max(1, get_int("dm.forward_map_limit", 200))
        while len(self._dm_map) > limit:
            self._dm_map.popitem(last=False)

    # ── 私訊轉發 ──────────────────────

    async def _forward_dm_to_owner(self, message: discord.Message) -> None:
        """將一般使用者私訊轉發給 Owner。"""
        owner = await self._resolve_owner()
        if owner is None:
            return
        if message.author.id == owner.id:
            return

        logger.info("[DM] from=%s content=%r", message.author, message.content[:80])

        lines = [
            "**收到私訊**",
            f"來自：**{message.author}**（ID: `{message.author.id}`）",
        ]
        if message.content:
            lines.append(f"內容：{message.content}")

        try:
            forward_message = await owner.send("\n".join(lines))
            self._remember_forward(forward_message.id, message.author.id)
            logger.info("[DM] 已轉發給 Owner %s", owner)
        except discord.HTTPException as exc:
            # 提升記錄詳細度：附上人類可讀原因，避免只留一行難以排查的 log。
            logger.error(
                "[DM] 轉發失敗 owner=%s reason=%s",
                owner, friendly_http_error(exc),
            )
            return

        for attachment in message.attachments:
            try:
                await owner.send(f"附件：{attachment.url}")
            except discord.HTTPException as exc:
                logger.warning(
                    "[DM] 附件轉發失敗 filename=%s: %s",
                    attachment.filename, friendly_http_error(exc),
                )

    # ── Owner 回覆橋接 ──────────────────────

    async def _handle_owner_reply(self, message: discord.Message) -> bool:
        """Owner 在 DM 回覆轉發訊息時，將內容送回原私訊者。"""
        if message.reference is None or message.reference.message_id is None:
            return False

        owner_id = await self._resolve_owner_id()
        if owner_id is None or message.author.id != owner_id:
            return False

        sender_id = self._dm_map.get(message.reference.message_id)
        if sender_id is None:
            return False

        if not message.content and not message.attachments:
            return True

        try:
            user   = await self.bot.fetch_user(sender_id)
            prefix = get_str("dm.owner_reply_prefix", "**Bot 回覆：**\n")
            if message.content:
                await user.send(f"{prefix}{message.content}")
            for attachment in message.attachments:
                await user.send(f"{prefix}附件：{attachment.url}")
            logger.info("[DM回覆] 已轉發給使用者 %s", user)
        except discord.HTTPException as exc:
            logger.error(
                "[DM回覆] 失敗 sender_id=%s: %s",
                sender_id, friendly_http_error(exc),
            )

        return True


# ── Extension 入口 ──────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Messenger(bot))
