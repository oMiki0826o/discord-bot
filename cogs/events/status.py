"""
cogs/events/status.py

職責：
- on_ready 時從 settings.json 套用 Bot 狀態
- $status <presence> <type> <text>：完整狀態指令（支援 custom 活動）
- $status_show：顯示目前狀態設定

設計說明：
- 狀態資料統一存放於 settings.json（bot.presence / bot.status_type / bot.status_text）
- 使用 core.system.settings.write_value() 持久化，免重啟即生效
- 與 cogs/system/owner.py 的 $game 互補：
    $game <文字>                → 快捷設定，預設 listening
    $status online playing 薩姆 → 完整語法，支援所有類型與在線狀態

Modification():

- 修正 _apply() 未接住 change_presence() 例外的問題：實測 log 出現
  連線剛重連、還不穩定時呼叫 change_presence() 拋出
  ClientConnectionResetError，沒有 try/except 會直接冒出到
  discord.py 的通用 on_ready 例外處理器，印出一長串看起來很嚴重
  但其實不影響其他功能的 traceback。狀態套用本來就是「盡力而為、
  失敗也無妨」的操作，改為捕捉例外並以 WARNING 記錄，不讓它繼續
  往外傳。
- 移植自 Bot-Firefly/cogs/events/status.py
- 移除獨立 status.json，改寫 settings.json（統一設定）
- 新增 custom 活動類型支援
- 所有 f-string 日誌改為 % 格式

"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from core.system.settings import get, write_value

logger = logging.getLogger("bot.events.status")

_STATUS_MAP: dict[str, discord.Status] = {
    "online":    discord.Status.online,
    "idle":      discord.Status.idle,
    "dnd":       discord.Status.dnd,
    "invisible": discord.Status.invisible,
}

_VALID_TYPES = frozenset({
    "custom", "playing", "watching", "listening", "competing",
})


def _build_activity(atype: str, text: str) -> discord.BaseActivity:
    """從類型字串與文字建立 Activity 物件。"""
    if atype == "custom":
        return discord.CustomActivity(name=text)
    if atype == "playing":
        return discord.Game(name=text)
    return discord.Activity(
        type=discord.ActivityType[atype],
        name=text,
    )


async def _apply(bot: commands.Bot) -> None:
    """
    從 settings.json 讀取設定並套用 Discord 狀態。

    change_presence() 需要透過目前的 WebSocket 連線送出封包；若
    on_ready 恰好在連線剛重連、尚未完全穩定的瞬間觸發（例如網路
    短暫中斷後的重連過程），呼叫這個函式有機率撞上連線正在關閉
    或還沒就緒的競態，拋出連線層級的例外（實測 log 出現過
    ClientConnectionResetError: Cannot write to closing transport）。
    這種失敗是暫時性的、非致命的——狀態套用失敗頂多讓 Bot 的顯示
    狀態暫時沒更新，不影響其他任何功能，下一次 on_ready 或
    $status 指令一樣能重新套用，因此不需要讓例外往外傳、更不需要
    讓它冒出到 discord.py 的通用 on_ready 例外處理器變成一長串
    看起來很嚴重、但其實不影響運作的 traceback。
    """
    presence = get("bot.presence",     "online")
    atype    = get("bot.status_type",  "listening")
    text     = get("bot.status_text",  "/play | @我")

    status   = _STATUS_MAP.get(presence, discord.Status.online)
    activity = _build_activity(atype, text)

    try:
        await bot.change_presence(status=status, activity=activity)
    except Exception as e:
        logger.warning(
            "[status] 套用狀態失敗（可能是連線剛重連尚未穩定，非致命）: %s", e
        )
        return

    logger.info("[status] 套用成功 presence=%s type=%s text=%r", presence, atype, text)


class Status(commands.Cog):
    """Bot 狀態管理。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── on_ready ──────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await _apply(self.bot)
        logger.info("[status] on_ready 狀態套用完成")

    # ── $status ──────────────────────

    @commands.command(name="status")
    @commands.is_owner()
    async def cmd_status(
        self,
        ctx:           commands.Context,
        presence:      str,
        activity_type: str,
        *,
        text:          str,
    ) -> None:
        """
        $status <presence> <type> <text>

        presence：online / idle / dnd / invisible
        type：    custom / playing / watching / listening / competing
        範例：    $status online playing Minecraft
                  $status dnd custom 維護中
        """
        presence      = presence.lower()
        activity_type = activity_type.lower()

        if presence not in _STATUS_MAP:
            await ctx.send(
                "無效的 presence。\n可用：`online` / `idle` / `dnd` / `invisible`"
            )
            return

        if activity_type not in _VALID_TYPES:
            await ctx.send(
                "無效的 activity 類型。\n"
                "可用：`custom` / `playing` / `watching` / `listening` / `competing`"
            )
            return

        # 持久化至 settings.json
        try:
            write_value("bot.presence",    presence)
            write_value("bot.status_type", activity_type)
            write_value("bot.status_text", text)
        except Exception as e:
            await ctx.send(f"寫入 settings.json 失敗：{e}")
            return

        # 立即套用
        await _apply(self.bot)

        await ctx.send(
            f"已更新 Bot 狀態\n"
            f"在線狀態：`{presence}`\n"
            f"類型：`{activity_type}`\n"
            f"內容：`{text}`"
        )
        logger.info(
            "[status] 由 %s 更新：presence=%s type=%s text=%r",
            ctx.author, presence, activity_type, text,
        )

    # ── $status_show ──────────────────────

    @commands.command(name="status_show")
    @commands.is_owner()
    async def cmd_status_show(self, ctx: commands.Context) -> None:
        """$status_show — 顯示目前的狀態設定（從 settings.json 讀取）"""
        presence = get("bot.presence",    "online")
        atype    = get("bot.status_type", "listening")
        text     = get("bot.status_text", "")

        await ctx.send(
            f"目前狀態設定\n"
            f"在線狀態：`{presence}`\n"
            f"類型：`{atype}`\n"
            f"內容：`{text}`"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Status(bot))
