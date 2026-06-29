"""
core/music/views.py

職責：
- MusicControls：五個控制按鈕（暫停/繼續、跳過、停止、循環切換、離開）
- QueueView：佇列翻頁面板
- 按鈕標籤從 settings.json 讀取，支援熱更新

Modification():

- 移植自 music_bot/ui/views.py，調整 import 路徑
- 循環按鈕標籤從 settings 讀取

"""

from __future__ import annotations

import discord

from core.music.player import GuildPlayer
from core.music.queue  import LoopMode
from core.system.settings import get

# ── 循環切換順序 ──────────────────────

_LOOP_NEXT: dict[LoopMode, LoopMode] = {
    LoopMode.OFF:    LoopMode.SINGLE,
    LoopMode.SINGLE: LoopMode.QUEUE,
    LoopMode.QUEUE:  LoopMode.OFF,
}


def _loop_label(mode: LoopMode) -> str:
    key = {LoopMode.OFF: "off", LoopMode.SINGLE: "single", LoopMode.QUEUE: "queue"}[mode]
    defaults = {"off": "循環：關閉", "single": "循環：單首", "queue": "循環：佇列"}
    return get(f"music.loop_labels.{key}", defaults[key])


# ── 音樂控制面板 ──────────────────────

class MusicControls(discord.ui.View):
    """
    音樂播放控制面板。

    Row 0：暫停/繼續、跳過、停止
    Row 1：循環切換、離開頻道
    """

    def __init__(self, player: GuildPlayer) -> None:
        super().__init__(timeout=3600)
        self.player = player
        self._sync_loop_button()

    def _sync_loop_button(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == "music:loop":
                child.label = _loop_label(self.player.queue.loop_mode)
                break

    async def _require_active(self, interaction: discord.Interaction) -> bool:
        from core.music.embeds import error_embed
        if not self.player.is_active:
            await interaction.response.send_message(
                embed=error_embed("目前沒有播放中的音樂"), ephemeral=True,
            )
            return False
        return True

    # ── Row 0 ──────────────────────

    @discord.ui.button(
        label="暫停", style=discord.ButtonStyle.primary,
        custom_id="music:pause_resume", row=0,
    )
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from core.music.embeds import success_embed, error_embed

        if self.player.is_paused:
            self.player.resume()
            button.label = "暫停"
            msg = "已繼續播放"
        elif self.player.is_playing:
            self.player.pause()
            button.label = "繼續"
            msg = "已暫停"
        else:
            await interaction.response.send_message(
                embed=error_embed("目前沒有播放中的音樂"), ephemeral=True,
            )
            return

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=success_embed(msg), ephemeral=True)

    @discord.ui.button(
        label="跳過", style=discord.ButtonStyle.secondary,
        custom_id="music:skip", row=0,
    )
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from core.music.embeds import success_embed
        if not await self._require_active(interaction):
            return
        self.player.skip()
        await interaction.response.send_message(embed=success_embed("已跳過當前歌曲"), ephemeral=True)

    @discord.ui.button(
        label="停止", style=discord.ButtonStyle.danger,
        custom_id="music:stop", row=0,
    )
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from core.music.embeds import success_embed
        await self.player.stop()
        await interaction.response.send_message(embed=success_embed("已停止播放，佇列已清空"), ephemeral=True)

    # ── Row 1 ──────────────────────

    @discord.ui.button(
        label="循環：關閉", style=discord.ButtonStyle.secondary,
        custom_id="music:loop", row=1,
    )
    async def loop_toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from core.music.embeds import success_embed
        next_mode    = _LOOP_NEXT[self.player.queue.loop_mode]
        self.player.set_loop(next_mode)
        button.label = _loop_label(next_mode)
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            embed=success_embed(f"循環模式已設定為：{_loop_label(next_mode)}"), ephemeral=True,
        )

    @discord.ui.button(
        label="離開", style=discord.ButtonStyle.danger,
        custom_id="music:leave", row=1,
    )
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from core.music.embeds import success_embed
        await self.player.disconnect()
        await interaction.response.send_message(embed=success_embed("已離開語音頻道"), ephemeral=True)

    async def on_timeout(self) -> None:
        for child in self.children:
            child.disabled = True   # type: ignore[attr-defined]


# ── 佇列翻頁面板 ──────────────────────

class QueueView(discord.ui.View):
    """佇列分頁控制面板，提供上一頁 / 下一頁按鈕。"""

    def __init__(self, player: GuildPlayer) -> None:
        super().__init__(timeout=120)
        self.player = player
        self.page   = 1

    def _total_pages(self) -> int:
        return max(1, (self.player.queue.size + 9) // 10)

    async def _update(self, interaction: discord.Interaction) -> None:
        from core.music.embeds import queue_embed
        await interaction.response.edit_message(
            embed=queue_embed(self.player.queue, self.page),
            view=self,
        )

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, custom_id="queue:prev")
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.page > 1:
            self.page -= 1
        await self._update(interaction)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary, custom_id="queue:next")
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.page < self._total_pages():
            self.page += 1
        await self._update(interaction)
