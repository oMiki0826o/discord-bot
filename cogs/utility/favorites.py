"""
cogs/utility/favorites.py

職責：
- 使用者音樂收藏清單（/fav）
- /fav add：加入收藏（可直接提供網址/關鍵字，或留空使用目前播放中的歌曲）
- /fav list：顯示個人收藏清單（Embed + 翻頁）
- /fav play：從收藏清單播放指定歌曲
- /fav remove：移除收藏
- /fav clear：清空收藏
- /fav menu：開啟互動選單（取得清單／加入／載入單曲／載入全部／刪除）

Modification():

- /fav add 新增 query 參數：提供網址或搜尋關鍵字時直接解析並加入收藏，
  不再要求「必須正在播放中」；省略 query 時維持原行為（加入當前播放歌曲）。
- 新增 /fav menu：單一進入點的互動選單，比照需求顯示五個操作項目
  （取得清單／加入指定歌曲／載入指定收藏／載入全部收藏／刪除指定收藏），
  以 Select 選單呈現，選曲操作重用既有分頁邏輯（每頁 10 首，未超過
  Discord Select 25 個選項上限）。
- 新增「載入全部收藏」：依序將收藏清單全部加入播放佇列，單一失敗不中斷
  其餘曲目，結束後回報成功／失敗統計；上限可由 settings.json 的
  music.favorites_load_all_limit 調整，避免一次性處理過多曲目卡住事件迴圈。
- /fav play、/fav remove 與選單共用同一組核心邏輯（_play_favorite_core /
  _remove_favorite_core），避免重複實作相同的索引驗證與資料庫操作。
- 分頁大小、批次上限等數值改由 settings.json 讀取（music.favorites_per_page /
  music.favorites_load_all_limit），不再寫死在程式碼中。
- core.music.embeds / core.music.views 維持沿用既有的延遲匯入慣例
  （與 core/music/player.py、core/music/views.py 一致），避免任何
  模組載入順序疑慮。

"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import database.repository.favorites_repository as fav_repo
from core.music.service   import get_player
from core.music.song      import Song
from core.system.settings import get, get_int
from utils.formatter       import format_duration

logger = logging.getLogger("bot.utility.favorites")


# ── 設定讀取（每次呼叫即時讀取，支援熱更新） ──────────────────────

def _per_page() -> int:
    return max(1, get_int("music.favorites_per_page", 10))


def _load_all_limit() -> int:
    return max(1, get_int("music.favorites_load_all_limit", 50))


# ── 共用工具 ──────────────────────

def _truncate(text: str, limit: int = 100) -> str:
    """截斷文字至 Discord SelectOption 上限（label/description 皆為 100 字元）。"""
    return text if len(text) <= limit else text[: limit - 3] + "..."


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
        embed.description = "收藏清單是空的，使用 `/fav add` 或 `/fav menu` 加入歌曲"
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


def _make_back_button(cog: "Favorites", member: discord.User | discord.Member) -> discord.ui.Button:
    """
    建立一顆「返回選單」按鈕，可加入任何 View（避免在多個 View 類別中重複定義）。

    cog._build_menu 在此檔案中定義於本函式之後，但屬性查找在按鈕「被點擊時」
    才會實際執行，屆時 Favorites 類別必定已完整定義，故無需擔心呼叫順序。
    """
    button = discord.ui.Button(label="返回選單", style=discord.ButtonStyle.secondary, row=1)

    async def _callback(interaction: discord.Interaction) -> None:
        embed, view = cog._build_menu(member)
        await interaction.response.edit_message(embed=embed, view=view)

    button.callback = _callback
    return button


class _BackButtonView(discord.ui.View):
    """僅含一顆「返回選單」按鈕的 View，用於各操作完成後的結果畫面。"""

    def __init__(self, cog: "Favorites", member: discord.User | discord.Member, timeout: int = 120) -> None:
        super().__init__(timeout=timeout)
        self.add_item(_make_back_button(cog, member))


# ── 純瀏覽分頁器（/fav list 與選單共用） ──────────────────────

class FavPager(discord.ui.View):
    """最愛清單瀏覽器（純翻頁，不可選曲）。"""

    def __init__(
        self,
        user:      discord.User | discord.Member,
        favorites: list[dict],
        cog:       "Favorites | None" = None,
        timeout:   int = 120,
    ) -> None:
        super().__init__(timeout=timeout)
        self.user      = user
        self.favorites = favorites
        self.page      = 1
        if cog is not None:
            self.add_item(_make_back_button(cog, user))

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, row=0)
    async def prev(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.page > 1:
            self.page -= 1
        await interaction.response.edit_message(
            embed=_fav_embed(self.user, self.favorites, self.page), view=self,
        )

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary, row=0)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        total_pages = max(1, (len(self.favorites) + _per_page() - 1) // _per_page())
        if self.page < total_pages:
            self.page += 1
        await interaction.response.edit_message(
            embed=_fav_embed(self.user, self.favorites, self.page), view=self,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True


# ── 瀏覽 + 選曲面板（選單的「載入指定」／「刪除指定」共用） ──────────────────────

class FavSongPicker(discord.ui.View):
    """
    最愛清單瀏覽 + 選曲面板。

    mode="play"   → 選取後播放該曲目（需先加入語音頻道）
    mode="remove" → 選取後從收藏移除該曲目

    下拉選單依「目前頁面」動態重建，每頁固定 _per_page() 首
    （預設 10，遠低於 Discord Select 25 個選項上限），翻頁時重新產生選項。
    """

    def __init__(
        self,
        cog:       "Favorites",
        member:    discord.User | discord.Member,
        favorites: list[dict],
        mode:      str,
        timeout:   int = 120,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cog       = cog
        self.member    = member
        self.favorites = favorites
        self.mode      = mode
        self.page      = 1
        self._select: discord.ui.Select | None = None
        self.add_item(_make_back_button(cog, member))
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

        start, chunk = self._chunk()
        options = [
            discord.SelectOption(
                label       = _truncate(f"{start + i + 1}. {s['title']}"),
                value       = str(start + i),
                description = format_duration(s["duration"]),
            )
            for i, s in enumerate(chunk)
        ]
        placeholder = "選擇要播放的歌曲" if self.mode == "play" else "選擇要刪除的歌曲"

        select = discord.ui.Select(placeholder=placeholder, options=options, row=0)
        select.callback = self._on_select
        self.add_item(select)
        self._select = select

    async def _on_select(self, interaction: discord.Interaction) -> None:
        assert self._select is not None
        index = int(self._select.values[0])

        if self.mode == "play":
            await self.cog.handle_picker_play(interaction, self.member, index)
        else:
            await self.cog.handle_picker_remove(interaction, self.member, index)

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary, row=1)
    async def prev(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.page > 1:
            self.page -= 1
        self._rebuild_select()
        await interaction.response.edit_message(
            embed=_fav_embed(self.member, self.favorites, self.page), view=self,
        )

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary, row=1)
    async def next(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.page < self._total_pages:
            self.page += 1
        self._rebuild_select()
        await interaction.response.edit_message(
            embed=_fav_embed(self.member, self.favorites, self.page), view=self,
        )

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True


# ── 「加入指定歌曲」彈出表單 ──────────────────────

class AddFavoriteModal(discord.ui.Modal, title="加入指定歌曲"):
    """從選單觸發，輸入網址或關鍵字後直接解析並加入收藏。"""

    query_input: discord.ui.TextInput = discord.ui.TextInput(
        label       = "YouTube 網址或搜尋關鍵字",
        placeholder = "https://www.youtube.com/watch?v=... 或歌曲名稱",
        required    = True,
        max_length  = 500,
    )

    def __init__(self, cog: "Favorites", member: discord.User | discord.Member) -> None:
        super().__init__()
        self.cog    = cog
        self.member = member

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.cog.add_by_query(interaction, self.member, self.query_input.value)


# ── 主選單 ──────────────────────

class FavMenuView(discord.ui.View):
    """最愛歌曲互動選單：五個操作項目，對應需求逐一列出。"""

    def __init__(self, cog: "Favorites", member: discord.User | discord.Member, timeout: int = 120) -> None:
        super().__init__(timeout=timeout)
        self.cog    = cog
        self.member = member

    @discord.ui.select(
        placeholder = "請選擇你要執行的操作",
        options = [
            discord.SelectOption(label="1. 取得最愛歌曲清單",     value="list"),
            discord.SelectOption(label="2. 加入指定歌曲（url）",  value="add"),
            discord.SelectOption(label="3. 載入指定的最愛歌曲",   value="play_one"),
            discord.SelectOption(label="4. 載入全部的最愛歌曲",   value="play_all"),
            discord.SelectOption(label="5. 從最愛清單刪除指定歌曲", value="remove"),
        ],
    )
    async def on_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        choice = select.values[0]

        if choice == "list":
            favs = await fav_repo.get_favorites(str(self.member.id))
            await interaction.response.edit_message(
                embed = _fav_embed(self.member, favs, 1),
                view  = FavPager(self.member, favs, cog=self.cog),
            )
        elif choice == "add":
            await interaction.response.send_modal(AddFavoriteModal(self.cog, self.member))
        elif choice == "play_one":
            await self.cog.show_picker(interaction, self.member, mode="play")
        elif choice == "play_all":
            await self.cog.play_all(interaction, self.member)
        elif choice == "remove":
            await self.cog.show_picker(interaction, self.member, mode="remove")

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True


# ── Cog ──────────────────────

class Favorites(commands.Cog):
    """音樂收藏清單指令。"""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    fav_group = app_commands.Group(name="fav", description="音樂收藏清單")

    # ── /fav add ──────────────────────

    @fav_group.command(name="add", description="加入收藏：提供網址/關鍵字可直接加入，留空則加入目前播放中的歌曲")
    @app_commands.describe(query="YouTube 網址或搜尋關鍵字（留空則使用目前播放中的歌曲）")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_add(self, interaction: discord.Interaction, query: str | None = None) -> None:
        await interaction.response.defer(ephemeral=True)

        if query:
            await self.add_by_query(interaction, interaction.user, query)
            return

        from core.music.embeds import error_embed, info_embed, success_embed

        if not interaction.guild:
            await interaction.followup.send(
                embed=error_embed("未提供 query 時僅限伺服器使用（需要正在播放的歌曲）"), ephemeral=True,
            )
            return

        player = get_player(self.bot, interaction.guild)
        song   = player.current_song
        if not song:
            await interaction.followup.send(
                embed=error_embed("目前沒有播放中的歌曲，請提供 query 參數或先播放歌曲"), ephemeral=True,
            )
            return

        added = await fav_repo.add_favorite(
            str(interaction.user.id), song.title, song.webpage_url, song.duration,
        )
        embed = (
            success_embed(f"已加入收藏：{song.title}") if added
            else info_embed(f"{song.title} 已在收藏清單中")
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def add_by_query(
        self,
        interaction: discord.Interaction,
        member:      discord.User | discord.Member,
        query:       str,
    ) -> None:
        """
        解析 query（網址或關鍵字）並加入收藏。
        呼叫前 interaction 必須已 defer／回應過，本函式只使用 followup。
        """
        from core.music.embeds import error_embed, info_embed, success_embed

        try:
            song = await Song.from_query(query, member)
        except Exception as exc:
            logger.warning("[fav.add] 解析失敗 query=%s: %s", query, exc)
            await interaction.followup.send(
                embed=error_embed(f"無法解析此網址或關鍵字：{exc}"), ephemeral=True,
            )
            return

        added = await fav_repo.add_favorite(str(member.id), song.title, song.webpage_url, song.duration)
        embed = (
            success_embed(f"已加入收藏：{song.title}") if added
            else info_embed(f"{song.title} 已在收藏清單中")
        )
        await interaction.followup.send(embed=embed, ephemeral=True, view=_BackButtonView(self, member))

    # ── /fav list ──────────────────────

    @fav_group.command(name="list", description="查看個人收藏清單")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_list(self, interaction: discord.Interaction) -> None:
        favs = await fav_repo.get_favorites(str(interaction.user.id))
        await interaction.response.send_message(
            embed = _fav_embed(interaction.user, favs, 1),
            view  = FavPager(interaction.user, favs) if favs else None,
            ephemeral = True,
        )

    # ── /fav play ──────────────────────

    @fav_group.command(name="play", description="從收藏清單播放指定編號的歌曲")
    @app_commands.describe(index="收藏清單中的編號（從 1 開始，可用 /fav list 確認）")
    async def cmd_play(self, interaction: discord.Interaction, index: int) -> None:
        from core.music.embeds import error_embed

        if not interaction.guild:
            await interaction.response.send_message(embed=error_embed("此指令僅限伺服器使用"), ephemeral=True)
            return

        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.send_message(embed=error_embed("請先加入語音頻道"), ephemeral=True)
            return

        await interaction.response.defer()
        embed, view = await self.play_favorite_core(interaction, member, index - 1)
        if view is None:
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, view=view)

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
        from core.music.embeds import added_song_embed, error_embed, now_playing_embed
        from core.music.views  import MusicControls

        _, fav = await _get_favorite_at(str(member.id), index)
        if fav is None:
            return error_embed("找不到該筆收藏，可能已被移除或編號錯誤"), None

        player = get_player(self.bot, member.guild)
        try:
            await player.connect(member.voice.channel)
            song = await player.add_song(fav["url"], member, interaction.channel)
        except Exception as exc:
            logger.exception("[fav.play] 播放失敗 url=%s", fav["url"])
            return error_embed(f"播放失敗：{exc}"), None

        if player.queue.size > 0:
            return added_song_embed(song, player.queue.size), MusicControls(player)
        return now_playing_embed(song, player.queue), MusicControls(player)

    # ── /fav remove ──────────────────────

    @fav_group.command(name="remove", description="移除收藏清單中的歌曲")
    @app_commands.describe(index="要移除的編號（可用 /fav list 確認）")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_remove(self, interaction: discord.Interaction, index: int) -> None:
        embed = await self.remove_favorite_core(str(interaction.user.id), index - 1)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def remove_favorite_core(self, user_id: str, index: int) -> discord.Embed:
        """執行「移除指定收藏」核心邏輯（0-based index），回傳結果 Embed。"""
        from core.music.embeds import error_embed, success_embed

        _, fav = await _get_favorite_at(user_id, index)
        if fav is None:
            return error_embed("找不到該筆收藏，可能已被移除或編號錯誤")

        await fav_repo.remove_favorite(user_id, fav["url"])
        return success_embed(f"已移除收藏：{fav['title']}")

    # ── /fav clear ──────────────────────

    @fav_group.command(name="clear", description="清空個人收藏清單")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_clear(self, interaction: discord.Interaction) -> None:
        count = await fav_repo.clear_favorites(str(interaction.user.id))
        await interaction.response.send_message(
            f"已清空收藏清單（共 {count} 首）。", ephemeral=True,
        )

    # ── /fav menu ──────────────────────

    @fav_group.command(name="menu", description="開啟最愛歌曲互動選單")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def cmd_menu(self, interaction: discord.Interaction) -> None:
        embed, view = self._build_menu(interaction.user)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    def _build_menu(
        self,
        member: discord.User | discord.Member,
    ) -> tuple[discord.Embed, "FavMenuView"]:
        """建立選單 Embed + View，供 /fav menu 與各「返回選單」按鈕共用。"""
        embed = discord.Embed(
            title       = "最愛歌曲",
            description = "請選擇你要執行的操作",
            color       = discord.Color.gold(),
        )
        embed.set_footer(text=get("embed_footer.default", "Firefly Bot"))
        return embed, FavMenuView(self, member)

    # ── 選單分流：載入指定 / 刪除指定（瀏覽 + 選曲面板） ──────────────────────

    async def show_picker(
        self,
        interaction: discord.Interaction,
        member:      discord.User | discord.Member,
        mode:        str,
    ) -> None:
        from core.music.embeds import error_embed

        favs = await fav_repo.get_favorites(str(member.id))
        if not favs:
            await interaction.response.edit_message(
                embed=error_embed("收藏清單是空的，請先用「加入指定歌曲」新增"),
                view=_BackButtonView(self, member),
            )
            return

        await interaction.response.edit_message(
            embed=_fav_embed(member, favs, 1),
            view=FavSongPicker(self, member, favs, mode=mode),
        )

    async def handle_picker_play(
        self,
        interaction: discord.Interaction,
        member:      discord.User | discord.Member,
        index:       int,
    ) -> None:
        from core.music.embeds import error_embed, info_embed

        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.edit_message(
                embed=error_embed("請先加入語音頻道"), view=_BackButtonView(self, member),
            )
            return

        await interaction.response.edit_message(embed=info_embed("正在載入歌曲..."), view=None)
        embed, view = await self.play_favorite_core(interaction, member, index)
        await interaction.edit_original_response(
            embed=embed, view=view or _BackButtonView(self, member),
        )

    async def handle_picker_remove(
        self,
        interaction: discord.Interaction,
        member:      discord.User | discord.Member,
        index:       int,
    ) -> None:
        embed = await self.remove_favorite_core(str(member.id), index)
        await interaction.response.edit_message(embed=embed, view=_BackButtonView(self, member))

    # ── 選單分流：載入全部 ──────────────────────

    async def play_all(self, interaction: discord.Interaction, member: discord.User | discord.Member) -> None:
        from core.music.embeds import error_embed, info_embed, success_embed

        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            await interaction.response.edit_message(
                embed=error_embed("請先加入語音頻道"), view=_BackButtonView(self, member),
            )
            return

        favs = await fav_repo.get_favorites(str(member.id))
        if not favs:
            await interaction.response.edit_message(
                embed=error_embed("收藏清單是空的"), view=_BackButtonView(self, member),
            )
            return

        limit   = _load_all_limit()
        targets = favs[:limit]

        await interaction.response.edit_message(
            embed=info_embed(f"正在載入 {len(targets)} 首收藏歌曲，請稍候..."), view=None,
        )

        player = get_player(self.bot, member.guild)
        try:
            await player.connect(member.voice.channel)
        except Exception as exc:
            logger.exception("[fav.play_all] 無法連接語音頻道")
            await interaction.edit_original_response(
                embed=error_embed(f"無法連接語音頻道：{exc}"), view=_BackButtonView(self, member),
            )
            return

        succeeded = 0
        failed: list[str] = []
        for fav in targets:
            try:
                await player.add_song(fav["url"], member, interaction.channel)
                succeeded += 1
            except Exception as exc:
                logger.warning("[fav.play_all] 加入失敗 url=%s: %s", fav["url"], exc)
                failed.append(fav["title"])

        summary = f"成功加入 **{succeeded}** 首"
        if len(favs) > limit:
            summary += f"（收藏共 {len(favs)} 首，單次上限 {limit} 首）"
        if failed:
            preview = "、".join(failed[:5])
            if len(failed) > 5:
                preview += f" 等共 {len(failed)} 首"
            summary += f"\n無法加入：{preview}"

        embed = success_embed(summary) if succeeded else error_embed(summary)
        await interaction.edit_original_response(embed=embed, view=_BackButtonView(self, member))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Favorites(bot))
