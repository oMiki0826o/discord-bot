"""
cogs/system/monitor.py

職責：
- 週期性背景任務：定期檢查過去 1 小時的錯誤率，超過閾值時主動通知 Owner

設計說明：
- 直接沿用 core.logging.discord_error_handler 既有的「攔截 ERROR 等級
  log → 即時私訊 Owner」機制（見 core/logging/discord_error_handler.py），
  不另外建立新的通知管道；本檔只需在超標時 logger.error() 一次即可
- 使用 discord.ext.tasks.loop，啟動前等待 bot.ready_event，
  避免在 setup_hook（載入 extension、初始化資料庫）完成前就開始查詢
- 檢查間隔與錯誤率閾值由 settings.json 的 ai.alert_check_interval_seconds /
  ai.alert_error_rate_threshold 統一提供，可熱更新無需重啟
- 避免重複告警：以 _alert_active 旗標記錄「目前是否已通知過」，
  同一次持續超標只觸發一次 ERROR log；待錯誤率回到閾值以下後
  旗標重置，下次再超標才會再次觸發通知
- 樣本不足（total_requests 太少）時不告警，避免「剛啟動、樣本只有
  1~2 筆」就因單次失敗被誤判為高錯誤率
"""

from __future__ import annotations

import logging

from discord.ext import commands, tasks

from core.system.settings import get as _s
from core.ai.budget import get_global_stats

logger = logging.getLogger("bot.system.monitor")

_MIN_SAMPLE_SIZE = 5   # 請求數低於此值時不計算錯誤率告警，避免樣本太少誤判


class Monitor(commands.Cog):
    """定期檢查系統指標，異常時透過既有 ERROR log 管道通知 Owner。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._alert_active = False   # 目前是否已處於「已通知」狀態，避免重複告警

        # 啟動前依 config 設定實際檢查間隔（預設 300 秒，由 config 覆寫）
        self.check_loop.change_interval(seconds=int(_s('ai.alert_check_interval_seconds', 300)))
        self.check_loop.start()

    async def cog_unload(self) -> None:
        self.check_loop.cancel()

    @tasks.loop(seconds=300)
    async def check_loop(self) -> None:
        # getattr 而非 self.bot.ready_event：ready_event 是專案自訂的
        # DiscordBot（定義於 bot.py）才有的屬性，標準 commands.Bot 型別
        # 沒有此屬性。維持 bot 參數標註為通用的 commands.Bot
        # （與其他 cog 的 setup() 慣例一致），這裡用 getattr 安全存取，
        # 若不存在則直接跳過本次檢查，而不是讓型別系統假裝它一定存在。
        ready_event = getattr(self.bot, "ready_event", None)
        if ready_event is not None:
            await ready_event.wait()

        try:
            stats = get_global_stats(hours=1)
        except Exception:
            logger.exception("[monitor] 取得統計資料失敗")
            return

        total_events = stats["total_requests"] + stats["error_count"]
        if total_events < _MIN_SAMPLE_SIZE:
            return   # 樣本太少，跳過本次檢查

        error_rate = stats["error_rate"]
        threshold  = float(_s('ai.alert_error_rate_threshold', 0.15))

        if error_rate >= threshold:
            if not self._alert_active:
                self._alert_active = True
                logger.error(
                    "[monitor] 過去 1 小時錯誤率 %.1f%% 超過閾值 %.1f%%"
                    "（請求 %d 次，錯誤 %d 次，活躍使用者 %d 人）",
                    error_rate * 100, threshold * 100,
                    stats["total_requests"], stats["error_count"],
                    stats["active_users"],
                )
        else:
            self._alert_active = False   # 回到正常範圍，重置旗標，允許下次再告警


# ── extension 進入點 ──────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Monitor(bot))
