"""
core/music/views.py

職責：
- MusicControls：五個控制按鈕（暫停/繼續、跳過、停止、循環切換、離開）
- QueueView：佇列翻頁與下拉管理面板
- 按鈕標籤從 settings.json 讀取，支援熱更新

Modification():

- 移植自 music_bot/ui/views.py，調整 import 路徑
- 循環按鈕標籤從 settings 讀取
- QueueView 新增歌曲下拉選單，可在 Embed 內移除歌曲或移到最前

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


def can_control_player(interaction: discord.Interaction, player: GuildPlayer) -> bool:
    """管理員，或與 Bot 位於同一語音頻道的成員，才能控制播放器。"""
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True

    bot_channel  = player.voice_channel
    user_channel = member.voice.channel if member.voice else None
    return bool(
        bot_channel
        and user_channel
        and bot_channel.id == user_channel.id
    )


async def require_player_control(
    interaction: discord.Interaction,
    player: GuildPlayer,
) -> bool:
    """檢查播放器控制權，失敗時直接回覆使用者。"""
    if can_control_player(interaction, player):
        return True

    from core.music.embeds import error_embed
    await interaction.response.send_message(
        embed=error_embed("你必須和 Bot 在同一個語音頻道才能操作；伺服器管理員不受此限制"),
        ephemeral=True,
    )
    return False


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

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await require_player_control(interaction, self.player)

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
    """佇列管理面板，提供翻頁、選曲、移除與移到最前。"""

    def __init__(self, player: GuildPlayer) -> None:
        super().__init__(timeout=120)
        self.player         = player
        self.page           = 1
        self._selected_song_ids: dict[int, str] = {}
        self._select: discord.ui.Select | None = None
        self._rebuild_select()

    def _total_pages(self) -> int:
        return max(1, (self.player.queue.size + 9) // 10)

    def _page_bounds(self) -> tuple[int, int]:
        start = (self.page - 1) * 10
        end   = min(start + 10, self.player.queue.size)
        return start, end

    def _rebuild_select(self) -> None:
        if self._select is not None:
            self.remove_item(self._select)
            self._select = None

        songs = self.player.queue.songs
        if not songs:
            return

        start, end = self._page_bounds()
        options = [
            discord.SelectOption(
                label       = f"{index + 1}. {song.title}"[:100],
                value       = song.queue_id,
                description = song.duration_str[:100],
            )
            for index, song in enumerate(songs[start:end], start=start)
        ]

        select = discord.ui.Select(
            placeholder = "選擇要調整的歌曲",
            options     = options,
            row         = 0,
        )
        select.callback = self._select_song
        self.add_item(select)
        self._select = select

    async def _select_song(self, interaction: discord.Interaction) -> None:
        if not await require_player_control(interaction, self.player):
            return
        assert self._select is not None
        selected_song_id = self._select.values[0]
        self._selected_song_ids[interaction.user.id] = selected_song_id
        selected_index = self.player.queue.index_of_id(selected_song_id)
        from core.music.embeds import success_embed
        await interaction.response.send_message(
            embed=success_embed(f"已選擇佇列第 {selected_index} 首"),
            ephemeral=True,
        )

    async def _update(self, interaction: discord.Interaction) -> None:
        from core.music.embeds import queue_embed
        self.page = max(1, min(self.page, self._total_pages()))
        self._rebuild_select()
        await interaction.response.edit_message(
            embed=queue_embed(self.player.queue, self.page),
            view=self,
        )

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, custom_id="queue:prev", row=1)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.page > 1:
            self.page -= 1
            self._selected_song_ids.pop(interaction.user.id, None)
        await self._update(interaction)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary, custom_id="queue:next", row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.page < self._total_pages():
            self.page += 1
            self._selected_song_ids.pop(interaction.user.id, None)
        await self._update(interaction)

    @discord.ui.button(label="移到最前", style=discord.ButtonStyle.primary, custom_id="queue:move_front", row=1)
    async def move_front(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from core.music.embeds import error_embed, success_embed

        if not await require_player_control(interaction, self.player):
            return
        selected_song_id = self._selected_song_ids.get(interaction.user.id)
        if selected_song_id is None:
            await interaction.response.send_message(embed=error_embed("請先從下拉選單選擇歌曲"), ephemeral=True)
            return

        if self.player.queue.move_by_id(selected_song_id, 1):
            self._selected_song_ids.pop(interaction.user.id, None)
            await self._update(interaction)
            await interaction.followup.send(embed=success_embed("已將歌曲移到佇列最前"), ephemeral=True)
        else:
            self._selected_song_ids.pop(interaction.user.id, None)
            await interaction.response.send_message(embed=error_embed("選擇的歌曲已不存在"), ephemeral=True)

    @discord.ui.button(label="移除", style=discord.ButtonStyle.danger, custom_id="queue:remove", row=1)
    async def remove_song(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from core.music.embeds import error_embed, success_embed

        if not await require_player_control(interaction, self.player):
            return
        selected_song_id = self._selected_song_ids.get(interaction.user.id)
        if selected_song_id is None:
            await interaction.response.send_message(embed=error_embed("請先從下拉選單選擇歌曲"), ephemeral=True)
            return

        removed = self.player.queue.remove_by_id(selected_song_id)
        if removed is None:
            self._selected_song_ids.pop(interaction.user.id, None)
            await interaction.response.send_message(embed=error_embed("選擇的歌曲已不存在"), ephemeral=True)
            return

        self._selected_song_ids.pop(interaction.user.id, None)
        await self._update(interaction)
        await interaction.followup.send(embed=success_embed(f"已移除：**{removed.title}**"), ephemeral=True)
