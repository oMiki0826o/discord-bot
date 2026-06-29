"""
cogs/utility/favorites.py

職責：
- 使用者音樂收藏清單（/fav）
- /fav add：將當前播放歌曲加入收藏
- /fav list：顯示個人收藏清單（Embed + 翻頁）
- /fav play：從收藏清單播放指定歌曲
- /fav remove：移除收藏
- /fav clear：清空收藏

Modification():

- 移植自 Bot-Firefly/core/music/favorites.py（原為 JSON 檔）
- 改用 SQLite（database/repository/favorites_repository.py）
- 以 Slash Commands Group 提供完整操作界面

"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import database.repository.favorites_repository as fav_repo
from core.music.service import get_player
from core.music.song    import Song
from core.system.settings import get
from utils.formatter      import format_duration

logger = logging.getLogger("bot.utility.favorites")
_PER_PAGE = 10


def _fav_embed(user: discord.User | discord.Member, favorites: list[dict], page: int) -> discord.Embed:
    total       = len(favorites)
    total_pages = max(1, (total + _PER_PAGE - 1) // _PER_PAGE)
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * _PER_PAGE
    chunk       = favorites[start : start + _PER_PAGE]

    embed = discord.Embed(
        title = f"{user.display_name} 的收藏清單",
        color = discord.Color.gold(),
    )
    if not favorites:
        embed.description = "收藏清單是空的，使用 `/fav add` 加入當前播放的歌曲"
    else:
        lines = [
            f"`{start+i+1}.` [{s['title']}]({s['url']}) `{format_duration(s['duration'])}`"
            for i, s in enumerate(chunk)
        ]
        embed.description = "\n".join(lines)
        embed.set_footer(
            text=f"第 {page}/{total_pages} 頁，共 {total} 首  |  {get('embed_footer.default','Firefly Bot')}"
        )
    return embed


class FavPager(discord.ui.View):
    def __init__(self, user: discord.User | discord.Member, favorites: list[dict]) -> None:
        super().__init__(timeout=120)
        self.user      = user
        self.favorites = favorites
        self.page      = 1

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.page > 1:
            self.page -= 1
        await interaction.response.edit_message(
            embed=_fav_embed(self.user, self.favorites, self.page), view=self,
        )

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        max_page = max(1, (len(self.favorites) + _PER_PAGE - 1) // _PER_PAGE)
        if self.page < max_page:
            self.page += 1
        await interaction.response.edit_message(
            embed=_fav_embed(self.user, self.favorites, self.page), view=self,
        )


class Favorites(commands.Cog):
    """音樂收藏清單指令。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    fav_group = app_commands.Group(name="fav", description="音樂收藏清單")

    # ── /fav add ──────────────────────

    @fav_group.command(name="add", description="將當前播放的歌曲加入收藏")
    async def cmd_add(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("此指令僅限伺服器使用。", ephemeral=True)
            return

        player = get_player(self.bot, interaction.guild)
        song   = player.current_song

        if not song:
            await interaction.response.send_message("目前沒有播放中的歌曲。", ephemeral=True)
            return

        added = fav_repo.add_favorite(
            str(interaction.user.id), song.title, song.webpage_url, song.duration,
        )

        if added:
            await interaction.response.send_message(
                f"已加入收藏：**{song.title}**", ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"**{song.title}** 已在收藏清單中。", ephemeral=True,
            )

    # ── /fav list ──────────────────────

    @fav_group.command(name="list", description="查看個人收藏清單")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_list(self, interaction: discord.Interaction) -> None:
        favs  = fav_repo.get_favorites(str(interaction.user.id))
        embed = _fav_embed(interaction.user, favs, 1)
        view  = FavPager(interaction.user, favs) if favs else None
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ── /fav play ──────────────────────

    @fav_group.command(name="play", description="從收藏清單播放指定編號的歌曲")
    @app_commands.describe(index="收藏清單中的編號（從 1 開始）")
    async def cmd_play(self, interaction: discord.Interaction, index: int) -> None:
        if not interaction.guild:
            await interaction.response.send_message("此指令僅限伺服器使用。", ephemeral=True)
            return

        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message("請先加入語音頻道。", ephemeral=True)
            return

        favs = fav_repo.get_favorites(str(interaction.user.id))
        if not favs:
            await interaction.response.send_message("收藏清單是空的。", ephemeral=True)
            return
        if not 1 <= index <= len(favs):
            await interaction.response.send_message(
                f"無效的編號，請輸入 1 到 {len(favs)} 之間的數字。", ephemeral=True,
            )
            return

        await interaction.response.defer()
        fav    = favs[index - 1]
        player = get_player(self.bot, interaction.guild)

        try:
            vc = member.voice.channel
            assert isinstance(vc, discord.VoiceChannel)
            await player.connect(vc)
            song = await player.add_song(fav["url"], member, interaction.channel)
        except Exception as e:
            logger.exception("[fav.play] 失敗 url=%s", fav["url"])
            await interaction.followup.send(f"播放失敗：{e}")
            return

        from core.music.embeds import added_song_embed, now_playing_embed
        from core.music.views  import MusicControls

        if player.queue.size > 0:
            await interaction.followup.send(embed=added_song_embed(song, player.queue.size))
        else:
            await interaction.followup.send(
                embed=now_playing_embed(song, player.queue),
                view=MusicControls(player),
            )

    # ── /fav remove ──────────────────────

    @fav_group.command(name="remove", description="移除收藏清單中的歌曲")
    @app_commands.describe(index="要移除的編號")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_remove(self, interaction: discord.Interaction, index: int) -> None:
        favs = fav_repo.get_favorites(str(interaction.user.id))
        if not 1 <= index <= len(favs):
            await interaction.response.send_message("無效的編號。", ephemeral=True)
            return

        fav     = favs[index - 1]
        removed = fav_repo.remove_favorite(str(interaction.user.id), fav["url"])

        if removed:
            await interaction.response.send_message(
                f"已移除收藏：**{fav['title']}**", ephemeral=True,
            )
        else:
            await interaction.response.send_message("移除失敗。", ephemeral=True)

    # ── /fav clear ──────────────────────

    @fav_group.command(name="clear", description="清空個人收藏清單")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_clear(self, interaction: discord.Interaction) -> None:
        count = fav_repo.clear_favorites(str(interaction.user.id))
        await interaction.response.send_message(
            f"已清空收藏清單（共 {count} 首）。", ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Favorites(bot))
