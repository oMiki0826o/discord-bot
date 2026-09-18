"""
cogs/music/music.py

職責：
- 音樂播放的全部 Slash Commands 與事件監聽
- /play：快速播放，預設單曲
- /music：所有其他音樂功能的下拉式面板
- $musicstatus owner only prefix 指令

Modification():

- 移植自 music_bot/cogs/music.py，調整所有 import 路徑
- _check_voice() 統一處理語音頻道前置驗證
- on_voice_state_update 整合至本 cog（不與 VoiceChannel JTC 衝突，各自監聽獨立事件）
- 收斂音樂 Slash Commands：控制操作僅保留在 Embed 按鈕，移除
  skip / pause / resume / stop / loop / shuffle / nowplaying /
  volume / remove / move 等 Slash 入口
- /musicstatus 改為 owner only prefix $musicstatus
- /queue /clear /history /leave 與 /favorite 收旂至 /music 面板
- /playlist 併入 /play，統一使用 /play <url> [mode]

"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from core.music.service import get_player, remove_player, get_manager
from core.music.url import is_youtube_url
from core.music.queue import QueueFullError
from core.music.embeds  import (
    added_song_embed, error_embed, history_embed, info_embed,
    now_playing_embed, playlist_added_embed, queue_embed, success_embed,
)
from core.music.views import MusicControls, QueueView, require_player_control

log = logging.getLogger("bot.music")

class Music(commands.Cog):
    """音樂播放相關的全部 Slash Commands 與事件監聽。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── 前置驗證 ──────────────────────

    async def _check_voice(self, interaction: discord.Interaction) -> discord.VoiceChannel | None:
        """確認使用者已在語音頻道中，否則自動回應錯誤並回傳 None。"""
        if interaction.guild is None:
            await interaction.response.send_message(
                embed=error_embed("此指令僅限伺服器使用"), ephemeral=True,
            )
            return None
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

    async def _check_channel_move(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
    ) -> bool:
        """避免一般成員把仍有聽眾的播放器移到其他語音頻道。"""
        player = get_player(self.bot, interaction.guild)
        current = player.voice_channel
        if not player.is_connected or current is None or current.id == channel.id:
            return True

        member = interaction.user
        if isinstance(member, discord.Member) and member.guild_permissions.administrator:
            return True

        listeners = [voice_member for voice_member in current.members if not voice_member.bot]
        if not listeners:
            return True

        await interaction.response.send_message(
            embed=error_embed("Bot 正在其他仍有聽眾的語音頻道播放；只有伺服器管理員可以移動 Bot"),
            ephemeral=True,
        )
        return False

    async def _send_playlist_result(
        self,
        interaction: discord.Interaction,
        url: str,
        channel: discord.VoiceChannel,
    ) -> None:
        player = get_player(self.bot, interaction.guild)

        try:
            await player.connect(channel)
            songs, skipped = await player.add_playlist(
                url,
                requester = interaction.user,
                channel   = interaction.channel,
            )
        except ConnectionError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)))
            return
        except QueueFullError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)))
            return
        except Exception as exc:
            guild_name = interaction.guild.name if interaction.guild else "DM"
            log.exception("[%s] 播放清單加入失敗", guild_name)
            msg = str(exc) or type(exc).__name__
            await interaction.followup.send(embed=error_embed(msg))
            return

        if not songs:
            await interaction.followup.send(embed=error_embed("播放清單為空或所有影片均無法播放"))
            return

        await interaction.followup.send(embed=playlist_added_embed(songs, skipped=skipped))

    # ── /play ──────────────────────

    @app_commands.command(name="play", description="播放 YouTube 單曲（歌單需手動選擇）")
    @app_commands.describe(url="YouTube 單曲或播放清單 URL", mode="URL 類型，未選擇時預設為單曲")
    @app_commands.choices(mode=[
        app_commands.Choice(name="單曲", value="song"),
        app_commands.Choice(name="歌單", value="playlist"),
    ])
    async def cmd_play(
        self,
        interaction: discord.Interaction,
        url: str,
        mode: str = "song",
    ) -> None:
        channel = await self._check_voice(interaction)
        if not channel:
            return
        if not await self._check_channel_move(interaction, channel):
            return
        if not is_youtube_url(url):
            await interaction.response.send_message(
                embed=error_embed("僅支援 YouTube 或 YouTube Music 網址"),
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        if mode == "playlist":
            await self._send_playlist_result(interaction, url, channel)
            return

        player     = get_player(self.bot, interaction.guild)
        was_active = player.is_active

        try:
            await player.connect(channel)
            song = await player.add_song(
                url,
                requester = interaction.user,
                channel   = interaction.channel,
            )
        except ConnectionError as exc:
            # 語音頻道連接逾時或失敗，顯示具體說明
            await interaction.followup.send(embed=error_embed(str(exc)))
            return
        except QueueFullError as exc:
            await interaction.followup.send(embed=error_embed(str(exc)))
            return
        except Exception as exc:
            guild_name = interaction.guild.name if interaction.guild else "DM"
            log.exception("[%s] /play 失敗", guild_name)
            # 避免 str(exc) 為空（如 TimeoutError），補上類型名稱
            msg = str(exc) or type(exc).__name__
            await interaction.followup.send(embed=error_embed(msg))
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

    # ── /queue ──────────────────────

    @app_commands.command(name="music", description="開啟音樂功能面板")
    @app_commands.guild_only()
    async def cmd_music(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        player = get_player(self.bot, interaction.guild)
        await interaction.response.send_message(
            embed=_music_panel_embed(player),
            view=MusicPanelView(self, interaction.user.id),
            ephemeral=True,
        )

    # ── /clear ──────────────────────

    # ── /history ──────────────────────

    # ── /leave ──────────────────────

    # ── $musicstatus ──────────────────────

    @commands.command(name="musicstatus", hidden=True)
    @commands.is_owner()
    async def cmd_musicstatus(self, ctx: commands.Context) -> None:
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

        await ctx.reply(embed=info_embed("\n".join(lines)))

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

def _music_panel_embed(player) -> discord.Embed:
    current = player.current_song
    status = f"正在播放：**{current.title}**" if current else "目前沒有播放中的音樂"
    embed = info_embed(
        f"{status}\n待播佇列：**{player.queue.size}** 首\n\n"
        "請從下方選單選擇要使用的功能。"
    )
    embed.title = "音樂面板"
    return embed


class FavoriteAddModal(discord.ui.Modal, title="加入最愛歌曲"):
    url = discord.ui.TextInput(
        label="YouTube 單曲 URL",
        placeholder="https://www.youtube.com/watch?v=...",
        required=True,
        max_length=500,
    )

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        favorites = self.bot.get_cog("Favorites")
        if favorites is None:
            await interaction.response.send_message(
                embed=error_embed("收藏功能尚未載入，請稍後再試"), ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        await favorites.add_by_query(interaction, interaction.user, self.url.value)


class MusicPanelSelect(discord.ui.Select):
    def __init__(self, cog: Music) -> None:
        self.cog = cog
        super().__init__(
            placeholder="選擇音樂功能",
            options=[
                discord.SelectOption(label="目前播放與控制", value="now_playing", emoji="⏯️"),
                discord.SelectOption(label="播放佇列", value="queue", emoji="📜"),
                discord.SelectOption(label="播放記錄", value="history", emoji="🕘"),
                discord.SelectOption(label="我的最愛歌單", value="favorites", emoji="⭐"),
                discord.SelectOption(label="加入最愛歌曲", value="favorite_add", emoji="➕"),
                discord.SelectOption(label="清空播放佇列", value="clear", emoji="🧹"),
                discord.SelectOption(label="離開語音頻道", value="leave", emoji="👋"),
                discord.SelectOption(label="重新整理面板", value="refresh", emoji="🔄"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        player = get_player(self.cog.bot, interaction.guild)
        action = self.values[0]

        if action == "refresh":
            await interaction.response.edit_message(embed=_music_panel_embed(player), view=self.view)
            return
        if action == "favorite_add":
            await interaction.response.send_modal(FavoriteAddModal(self.cog.bot))
            return
        if action == "favorites":
            from cogs.utility.favorites import FavoriteListView, _fav_embed
            import database.repository.favorites_repository as fav_repo

            favorites_cog = self.cog.bot.get_cog("Favorites")
            if favorites_cog is None:
                await interaction.response.send_message(
                    embed=error_embed("收藏功能尚未載入，請稍後再試"), ephemeral=True,
                )
                return
            favorites = await fav_repo.get_favorites(str(interaction.user.id))
            await interaction.response.send_message(
                embed=_fav_embed(interaction.user, favorites, 1),
                view=FavoriteListView(favorites_cog, interaction.user, favorites) if favorites else None,
                ephemeral=True,
            )
            return
        if action == "queue":
            await interaction.response.send_message(
                embed=queue_embed(player.queue, page=1),
                view=QueueView(player),
                ephemeral=True,
            )
            return
        if action == "history":
            await interaction.response.send_message(embed=history_embed(player.queue), ephemeral=True)
            return
        if action == "now_playing":
            if player.current_song is None:
                await interaction.response.send_message(
                    embed=error_embed("目前沒有播放中的音樂"), ephemeral=True,
                )
                return
            await interaction.response.send_message(
                embed=now_playing_embed(player.current_song, player.queue),
                view=MusicControls(player),
                ephemeral=True,
            )
            return
        if action == "clear":
            if not await require_player_control(interaction, player):
                return
            player.queue.clear()
            await interaction.response.send_message(embed=success_embed("播放佇列已清空"), ephemeral=True)
            return

        if not player.is_connected:
            await interaction.response.send_message(
                embed=error_embed("Bot 目前不在語音頻道中"), ephemeral=True,
            )
            return
        if not await require_player_control(interaction, player):
            return
        await player.disconnect()
        await interaction.response.send_message(embed=success_embed("已離開語音頻道"), ephemeral=True)


class MusicPanelView(discord.ui.View):
    def __init__(self, cog: Music, user_id: int) -> None:
        super().__init__(timeout=300)
        self.user_id = user_id
        self.add_item(MusicPanelSelect(cog))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("這不是你的音樂面板。", ephemeral=True)
        return False


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
