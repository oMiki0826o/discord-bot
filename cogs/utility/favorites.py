"""
cogs/utility/favorites.py

職責：
- 使用者音樂收藏清單（由 /music 面板進入）
- 加入、顯示、播放、刪除或清空個人收藏

Modification():

- 收斂 Slash Commands：/fav 改為 /favorite，僅保留 add / list；
  播放、刪除與清空改放在 /favorite list 的互動面板。
- /favorite add 改為僅接受 http(s) 單曲 URL，拒絕 playlist URL 與搜尋關鍵字。
- 移除舊 /fav menu、/fav play、/fav remove、/fav clear 的死碼與互動類別。
- 移除 /favorite Slash 群組，收藏功能完整收旂至 /music。

"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

import discord
from discord.ext import commands

import database.repository.favorites_repository as fav_repo
from core.music.service   import get_player
from core.music.song      import Song
from core.music.url       import is_youtube_url
from core.system.settings import get, get_int
from utils.formatter       import format_duration

logger = logging.getLogger("bot.utility.favorites")

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


# ── 設定讀取（每次呼叫即時讀取，支援熱更新） ──────────────────────

def _per_page() -> int:
    return max(1, get_int("music.favorites_per_page", 10))


# ── 共用工具 ──────────────────────

def _truncate(text: str, limit: int = 100) -> str:
    """截斷文字至 Discord SelectOption 上限（label/description 皆為 100 字元）。"""
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _is_http_url(text: str) -> bool:
    return bool(_URL_RE.match(text.strip()))


def _is_playlist_url(text: str) -> bool:
    parsed = urlparse(text.strip())
    query  = parse_qs(parsed.query)
    return "list" in query or parsed.path.strip("/").lower() in {"playlist", "watch_videos"}


async def _get_favorite_at(user_id: str, index: int) -> tuple[list[dict], dict | None]:
    """
    取得使用者完整收藏清單，以及 0-based 索引指定的項目。
    索引超出範圍時項目回傳 None，呼叫端應顯示「找不到該筆收藏」訊息。
    """
    favs = await fav_repo.get_favorites(user_id)
    if 0 <= index < len(favs):
        return favs, favs[index]
    return favs, None


def _fav_embed(
    user:      discord.User | discord.Member,
    favorites: list[dict],
    page:      int,
) -> discord.Embed:
    per_page    = _per_page()
    total       = len(favorites)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * per_page
    chunk       = favorites[start : start + per_page]

    embed = discord.Embed(
        title = f"{user.display_name} 的收藏清單",
        color = discord.Color.gold(),
    )
    if not favorites:
        embed.description = "收藏清單是空的，請從 `/music` 面板加入歌曲"
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


# ── 收藏清單管理面板 ──────────────────────

class FavoriteListView(discord.ui.View):
    """收藏清單面板：下拉選歌後可播放、刪除或清空收藏。"""

    def __init__(
        self,
        cog:       "Favorites",
        member:    discord.User | discord.Member,
        favorites: list[dict],
        timeout:   int = 120,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cog            = cog
        self.member         = member
        self.favorites      = favorites
        self.page           = 1
        self.selected_index: int | None = None
        self._select: discord.ui.Select | None = None
        self._rebuild_select()

    @property
    def _total_pages(self) -> int:
        return max(1, (len(self.favorites) + _per_page() - 1) // _per_page())

    def _chunk(self) -> tuple[int, list[dict]]:
        start = (self.page - 1) * _per_page()
        return start, self.favorites[start : start + _per_page()]

    def _rebuild_select(self) -> None:
        if self._select is not None:
            self.remove_item(self._select)
            self._select = None

        if not self.favorites:
            return

        start, chunk = self._chunk()
        options = [
            discord.SelectOption(
                label       = _truncate(f"{start + i + 1}. {s['title']}"),
                value       = str(start + i),
                description = format_duration(s["duration"]),
            )
            for i, s in enumerate(chunk)
        ]
        select = discord.ui.Select(
            placeholder = "選擇收藏歌曲",
            options     = options,
            row         = 0,
        )
        select.callback = self._on_select
        self.add_item(select)
        self._select = select

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self.favorites = await fav_repo.get_favorites(str(self.member.id))
        self.page = max(1, min(self.page, self._total_pages))
        self._rebuild_select()
        await interaction.response.edit_message(
            embed=_fav_embed(self.member, self.favorites, self.page),
            view=self if self.favorites else None,
        )

    async def _on_select(self, interaction: discord.Interaction) -> None:
        assert self._select is not None
        self.selected_index = int(self._select.values[0])
        from core.music.embeds import success_embed
        await interaction.response.send_message(
            embed=success_embed(f"已選擇收藏第 {self.selected_index + 1} 首"),
            ephemeral=True,
        )

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, row=1)
    async def prev(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.page > 1:
            self.page -= 1
            self.selected_index = None
        await self._refresh(interaction)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.page < self._total_pages:
            self.page += 1
            self.selected_index = None
        await self._refresh(interaction)

    @discord.ui.button(label="播放", style=discord.ButtonStyle.primary, row=1)
    async def play_selected(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        from core.music.embeds import error_embed, info_embed

        if self.selected_index is None:
            await interaction.response.send_message(embed=error_embed("請先從下拉選單選擇收藏"), ephemeral=True)
            return
        if not isinstance(self.member, discord.Member) or not self.member.voice or not self.member.voice.channel:
            await interaction.response.send_message(embed=error_embed("請先加入語音頻道"), ephemeral=True)
            return

        await interaction.response.edit_message(embed=info_embed("正在載入收藏歌曲..."), view=None)
        embed, view = await self.cog.play_favorite_core(interaction, self.member, self.selected_index)
        await interaction.edit_original_response(embed=embed, view=view or self)

    @discord.ui.button(label="刪除", style=discord.ButtonStyle.danger, row=1)
    async def remove_selected(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        from core.music.embeds import error_embed

        if self.selected_index is None:
            await interaction.response.send_message(embed=error_embed("請先從下拉選單選擇收藏"), ephemeral=True)
            return

        embed = await self.cog.remove_favorite_core(str(self.member.id), self.selected_index)
        self.selected_index = None
        self.favorites = await fav_repo.get_favorites(str(self.member.id))
        self.page = max(1, min(self.page, self._total_pages))
        self._rebuild_select()
        await interaction.response.edit_message(
            embed=_fav_embed(self.member, self.favorites, self.page),
            view=self if self.favorites else None,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="清空", style=discord.ButtonStyle.danger, row=2)
    async def clear_all(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        count = await fav_repo.clear_favorites(str(self.member.id))
        self.favorites = []
        self.selected_index = None
        await interaction.response.edit_message(
            embed=_fav_embed(self.member, self.favorites, 1),
            view=None,
        )
        await interaction.followup.send(f"已清空收藏清單（共 {count} 首）。", ephemeral=True)

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True


# ── Cog ──────────────────────

class Favorites(commands.Cog):
    """音樂收藏清單指令。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /favorite add ──────────────────────

    async def add_by_query(
        self,
        interaction: discord.Interaction,
        member:      discord.User | discord.Member,
        query:       str,
    ) -> None:
        """
        解析單曲 URL 並加入收藏。
        呼叫前 interaction 必須已 defer／回應過，本函式只使用 followup。
        """
        from core.music.embeds import error_embed, info_embed, success_embed

        if not _is_http_url(query):
            await interaction.followup.send(
                embed=error_embed("請提供有效的單曲 URL（需以 http:// 或 https:// 開頭）"),
                ephemeral=True,
            )
            return
        if not is_youtube_url(query):
            await interaction.followup.send(
                embed=error_embed("收藏僅支援 YouTube 或 YouTube Music 單曲網址"),
                ephemeral=True,
            )
            return
        if _is_playlist_url(query):
            await interaction.followup.send(
                embed=error_embed("收藏僅限單曲 URL，播放清單請手動選擇 /play 的歌單模式"),
                ephemeral=True,
            )
            return

        try:
            song = await Song.from_query(query, member)
        except Exception as exc:
            logger.warning("[favorite.add] 解析失敗 query=%s: %s", query, exc)
            await interaction.followup.send(
                embed=error_embed(f"無法解析此單曲 URL：{exc}"), ephemeral=True,
            )
            return

        added = await fav_repo.add_favorite(str(member.id), song.title, song.webpage_url, song.duration)
        embed = (
            success_embed(f"已加入收藏：{song.title}") if added
            else info_embed(f"{song.title} 已在收藏清單中")
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /favorite list ──────────────────────

    # ── 收藏播放核心 ──────────────────────

    async def play_favorite_core(
        self,
        interaction: discord.Interaction,
        member:      discord.Member,
        index:       int,
    ) -> tuple[discord.Embed, discord.ui.View | None]:
        """
        執行「播放指定收藏」核心邏輯（0-based index），回傳 (embed, view)。
        view 為 None 時代表錯誤情況，呼叫端應以 ephemeral 顯示。
        """
        from core.music.embeds import added_song_embed, error_embed, info_embed, now_playing_embed
        from core.music.views  import MusicControls

        _, fav = await _get_favorite_at(str(member.id), index)
        if fav is None:
            return error_embed("找不到該筆收藏，可能已被移除或編號錯誤"), None

        player = get_player(self.bot, member.guild)
        was_active = player.is_active
        try:
            await player.connect(member.voice.channel)
            song = await player.add_song(fav["url"], member, interaction.channel)
        except Exception as exc:
            logger.exception("[fav.play] 播放失敗 url=%s", fav["url"])
            return error_embed(f"播放失敗：{exc}"), None

        if was_active:
            return added_song_embed(song, player.queue.size), MusicControls(player)

        # 收藏面板是私人訊息；新開始播放時另外發送公開的目前播放。
        if interaction.channel is None:
            return error_embed("已開始播放，但找不到可發送公開通知的文字頻道"), None
        try:
            await interaction.channel.send(
                embed=now_playing_embed(song, player.queue),
                view=MusicControls(player),
            )
        except discord.HTTPException as exc:
            logger.warning("[fav.play] 無法發送公開的目前播放通知: %s", exc)
            return error_embed("已開始播放，但 Bot 無法在此頻道發送公開通知"), None
        return info_embed(f"已開始播放：{song.title}（公開通知已發送）"), None

    # ── 收藏刪除核心 ──────────────────────

    async def remove_favorite_core(self, user_id: str, index: int) -> discord.Embed:
        """執行「移除指定收藏」核心邏輯（0-based index），回傳結果 Embed。"""
        from core.music.embeds import error_embed, success_embed

        _, fav = await _get_favorite_at(user_id, index)
        if fav is None:
            return error_embed("找不到該筆收藏，可能已被移除或編號錯誤")

        await fav_repo.remove_favorite(user_id, fav["url"])
        return success_embed(f"已移除收藏：{fav['title']}")

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Favorites(bot))
