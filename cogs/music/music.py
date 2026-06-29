"""
cogs/music/music.py

職責：
- 音樂播放的全部 Slash Commands 與事件監聽
- /play /playlist /skip /pause /resume /stop /nowplaying
- /queue /shuffle /loop /volume /remove /move /clear /history /leave /status

Modification():

- 移植自 music_bot/cogs/music.py，調整所有 import 路徑
- _check_voice() 統一處理語音頻道前置驗證
- on_voice_state_update 整合至本 cog（不與 VoiceChannel JTC 衝突，各自監聽獨立事件）

"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.music.queue   import LoopMode
from core.music.service import get_player, remove_player, get_manager
from core.music.embeds  import (
    added_song_embed, error_embed, history_embed, info_embed,
    now_playing_embed, playlist_added_embed, queue_embed, success_embed,
)
from core.music.views import MusicControls, QueueView

log = logging.getLogger("bot.music")


class Music(commands.Cog):
    """音樂播放相關的全部 Slash Commands 與事件監聽。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── 前置驗證 ──────────────────────

    async def _check_voice(self, interaction: discord.Interaction) -> discord.VoiceChannel | None:
        """確認使用者已在語音頻道中，否則自動回應錯誤並回傳 None。"""
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message(
                embed=error_embed("你必須先加入語音頻道"), ephemeral=True,
            )
            return None
        ch = member.voice.channel
        if not isinstance(ch, discord.VoiceChannel):
            await interaction.response.send_message(
                embed=error_embed("請加入一般語音頻道（非 Stage）"), ephemeral=True,
            )
            return None
        return ch

    async def _check_active(self, interaction: discord.Interaction) -> bool:
        """確認目前有音樂播放或暫停中，否則自動回應錯誤。"""
        player = get_player(self.bot, interaction.guild)
        if not player.is_active:
            await interaction.response.send_message(
                embed=error_embed("目前沒有播放中的音樂"), ephemeral=True,
            )
            return False
        return True

    # ── /play ──────────────────────

    @app_commands.command(name="play", description="播放單曲（支援 YouTube URL 或搜尋關鍵字）")
    @app_commands.describe(query="YouTube URL 或搜尋關鍵字")
    async def cmd_play(self, interaction: discord.Interaction, query: str) -> None:
        channel = await self._check_voice(interaction)
        if not channel:
            return

        await interaction.response.defer()

        player     = get_player(self.bot, interaction.guild)
        was_active = player.is_active

        try:
            await player.connect(channel)
            song = await player.add_song(
                query,
                requester = interaction.user,
                channel   = interaction.channel,
            )
        except Exception as exc:
            log.exception("[%s] /play 失敗", interaction.guild.name)
            await interaction.followup.send(embed=error_embed(str(exc)))
            return

        if was_active:
            await interaction.followup.send(
                embed=added_song_embed(song, player.queue.size),
            )
        else:
            await interaction.followup.send(
                embed=now_playing_embed(song, player.queue),
                view=MusicControls(player),
            )

    # ── /playlist ──────────────────────

    @app_commands.command(name="playlist", description="加入整個 YouTube 播放清單")
    @app_commands.describe(url="YouTube 播放清單 URL")
    async def cmd_playlist(self, interaction: discord.Interaction, url: str) -> None:
        channel = await self._check_voice(interaction)
        if not channel:
            return

        await interaction.response.defer()
        player = get_player(self.bot, interaction.guild)

        try:
            await player.connect(channel)
            songs = await player.add_playlist(
                url,
                requester = interaction.user,
                channel   = interaction.channel,
            )
        except Exception as exc:
            log.exception("[%s] /playlist 失敗", interaction.guild.name)
            await interaction.followup.send(embed=error_embed(str(exc)))
            return

        if not songs:
            await interaction.followup.send(embed=error_embed("播放清單為空或無法解析"))
            return

        await interaction.followup.send(embed=playlist_added_embed(songs))

    # ── /skip ──────────────────────

    @app_commands.command(name="skip", description="跳過當前歌曲")
    async def cmd_skip(self, interaction: discord.Interaction) -> None:
        if not await self._check_active(interaction):
            return
        get_player(self.bot, interaction.guild).skip()
        await interaction.response.send_message(embed=success_embed("已跳過當前歌曲"))

    # ── /pause ──────────────────────

    @app_commands.command(name="pause", description="暫停播放")
    async def cmd_pause(self, interaction: discord.Interaction) -> None:
        player = get_player(self.bot, interaction.guild)
        if player.pause():
            await interaction.response.send_message(embed=success_embed("已暫停"))
        else:
            await interaction.response.send_message(
                embed=error_embed("目前沒有播放中的音樂"), ephemeral=True,
            )

    # ── /resume ──────────────────────

    @app_commands.command(name="resume", description="繼續播放")
    async def cmd_resume(self, interaction: discord.Interaction) -> None:
        player = get_player(self.bot, interaction.guild)
        if player.resume():
            await interaction.response.send_message(embed=success_embed("已繼續播放"))
        else:
            await interaction.response.send_message(
                embed=error_embed("目前沒有暫停中的音樂"), ephemeral=True,
            )

    # ── /stop ──────────────────────

    @app_commands.command(name="stop", description="停止播放並清空佇列（保持連線）")
    async def cmd_stop(self, interaction: discord.Interaction) -> None:
        await get_player(self.bot, interaction.guild).stop()
        await interaction.response.send_message(embed=success_embed("已停止播放，佇列已清空"))

    # ── /nowplaying ──────────────────────

    @app_commands.command(name="nowplaying", description="查看目前播放的歌曲")
    async def cmd_nowplaying(self, interaction: discord.Interaction) -> None:
        player = get_player(self.bot, interaction.guild)
        song   = player.current_song

        if not song:
            await interaction.response.send_message(
                embed=error_embed("目前沒有播放中的音樂"), ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=now_playing_embed(song, player.queue),
            view=MusicControls(player),
        )

    # ── /queue ──────────────────────

    @app_commands.command(name="queue", description="查看播放佇列（支援翻頁）")
    async def cmd_queue(self, interaction: discord.Interaction) -> None:
        player = get_player(self.bot, interaction.guild)
        await interaction.response.send_message(
            embed=queue_embed(player.queue, page=1),
            view=QueueView(player),
        )

    # ── /shuffle ──────────────────────

    @app_commands.command(name="shuffle", description="隨機打亂播放佇列")
    async def cmd_shuffle(self, interaction: discord.Interaction) -> None:
        player = get_player(self.bot, interaction.guild)
        if player.queue.is_empty:
            await interaction.response.send_message(
                embed=error_embed("佇列為空，無法打亂"), ephemeral=True,
            )
            return
        player.queue.shuffle()
        await interaction.response.send_message(embed=success_embed("佇列已隨機排列"))

    # ── /loop ──────────────────────

    @app_commands.command(name="loop", description="設定循環模式")
    @app_commands.describe(mode="循環模式選項")
    @app_commands.choices(mode=[
        app_commands.Choice(name="關閉",     value="off"),
        app_commands.Choice(name="單首循環", value="single"),
        app_commands.Choice(name="佇列循環", value="queue"),
    ])
    async def cmd_loop(self, interaction: discord.Interaction, mode: str) -> None:
        _mode_map = {"off": LoopMode.OFF, "single": LoopMode.SINGLE, "queue": LoopMode.QUEUE}
        _msg_map  = {"off": "已關閉循環", "single": "已開啟單首循環", "queue": "已開啟佇列循環"}
        get_player(self.bot, interaction.guild).set_loop(_mode_map[mode])
        await interaction.response.send_message(embed=success_embed(_msg_map[mode]))

    # ── /volume ──────────────────────

    @app_commands.command(name="volume", description="調整音量（0–200）")
    @app_commands.describe(volume="音量數值，0–200（100 為正常音量）")
    async def cmd_volume(
        self,
        interaction: discord.Interaction,
        volume:      app_commands.Range[int, 0, 200],
    ) -> None:
        get_player(self.bot, interaction.guild).set_volume(volume / 100.0)
        await interaction.response.send_message(embed=success_embed(f"音量已設定為 {volume}%"))

    # ── /remove ──────────────────────

    @app_commands.command(name="remove", description="從佇列移除指定歌曲")
    @app_commands.describe(index="歌曲在佇列中的編號（從 1 開始）")
    async def cmd_remove(self, interaction: discord.Interaction, index: int) -> None:
        player  = get_player(self.bot, interaction.guild)
        removed = player.queue.remove(index)

        if removed:
            await interaction.response.send_message(embed=success_embed(f"已移除：**{removed.title}**"))
        else:
            await interaction.response.send_message(
                embed=error_embed(f"找不到編號 {index} 的歌曲，請用 /queue 確認"), ephemeral=True,
            )

    # ── /move ──────────────────────

    @app_commands.command(name="move", description="移動佇列中歌曲的位置")
    @app_commands.describe(from_index="要移動的歌曲編號", to_index="移動後的目標位置")
    async def cmd_move(
        self,
        interaction: discord.Interaction,
        from_index:  int,
        to_index:    int,
    ) -> None:
        player = get_player(self.bot, interaction.guild)
        if player.queue.move(from_index, to_index):
            await interaction.response.send_message(
                embed=success_embed(f"已將第 {from_index} 首移至第 {to_index} 位"),
            )
        else:
            await interaction.response.send_message(
                embed=error_embed("無效的位置編號，請用 /queue 確認"), ephemeral=True,
            )

    # ── /clear ──────────────────────

    @app_commands.command(name="clear", description="清空整個播放佇列")
    async def cmd_clear(self, interaction: discord.Interaction) -> None:
        get_player(self.bot, interaction.guild).queue.clear()
        await interaction.response.send_message(embed=success_embed("播放佇列已清空"))

    # ── /history ──────────────────────

    @app_commands.command(name="history", description="查看最近播放記錄（最多 10 首）")
    async def cmd_history(self, interaction: discord.Interaction) -> None:
        player = get_player(self.bot, interaction.guild)
        await interaction.response.send_message(embed=history_embed(player.queue))

    # ── /leave ──────────────────────

    @app_commands.command(name="leave", description="讓 Bot 離開語音頻道")
    async def cmd_leave(self, interaction: discord.Interaction) -> None:
        player = get_player(self.bot, interaction.guild)
        if not player.is_connected:
            await interaction.response.send_message(
                embed=error_embed("Bot 目前不在語音頻道中"), ephemeral=True,
            )
            return
        await player.disconnect()
        await interaction.response.send_message(embed=success_embed("已離開語音頻道"))

    # ── /status ──────────────────────

    @app_commands.command(name="musicstatus", description="查看所有伺服器的音樂播放狀態")
    @app_commands.default_permissions(administrator=True)
    async def cmd_status(self, interaction: discord.Interaction) -> None:
        manager = get_manager()
        players = manager.all_players()
        active  = [(gid, p) for gid, p in players.items() if p.is_active]

        lines = [f"目前共在 **{len(active)}** 個伺服器播放音樂："]
        for gid, p in active:
            guild = self.bot.get_guild(gid)
            name  = guild.name if guild else f"Guild {gid}"
            song  = p.current_song
            lines.append(
                f"  - **{name}**：{song.title if song else '無'}"
                f"  （佇列 {p.queue.size} 首）"
            )

        await interaction.response.send_message(embed=info_embed("\n".join(lines)), ephemeral=True)

    # ── 事件監聽 ──────────────────────

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after:  discord.VoiceState,
    ) -> None:
        """
        處理：
        1. Bot 被強制踢出語音頻道 → 清理播放器
        2. 頻道內只剩 Bot → 啟動閒置計時器
        """
        # Bot 被踢出
        if member == self.bot.user and after.channel is None:
            player = get_player(self.bot, member.guild)
            await player.disconnect()
            return

        # 頻道只剩 Bot
        vc = member.guild.voice_client
        if vc and before.channel and before.channel.id == vc.channel.id:
            human = [m for m in vc.channel.members if not m.bot]
            if not human:
                get_player(self.bot, member.guild)._start_idle_timer()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Bot 離開伺服器時清理播放器實例。"""
        remove_player(guild.id)
        log.info("Guild %d（%s）的播放器已清除", guild.id, guild.name)


# ── extension 進入點 ──────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
