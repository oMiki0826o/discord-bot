"""
core/music/embeds.py

職責：
- 所有音樂相關 Embed 工廠函式
- 顏色與標籤從 settings.json 讀取，支援熱更新
- 分頁邏輯（queue_embed）、歷史記錄（history_embed）

Modification():

- 移植自 music_bot/ui/embeds.py，調整路徑
- 顏色從 settings.json 讀取
- footer 文字從 settings.json 讀取

"""

from __future__ import annotations

import discord

from core.music.queue import MusicQueue, LoopMode
from core.music.song  import Song, format_duration
from core.system.settings import get

_PER_PAGE = 10

# ── 顏色解析 ──────────────────────

_COLOR_MAP: dict[str, discord.Color] = {
    "blurple": discord.Color.blurple(),
    "orange":  discord.Color.orange(),
    "green":   discord.Color.green(),
    "red":     discord.Color.red(),
    "blue":    discord.Color.blue(),
    "yellow":  discord.Color.yellow(),
    "purple":  discord.Color.purple(),
}


def _clr(key: str, fallback: str = "blurple") -> discord.Color:
    name = get(f"music.embed_colors.{key}", fallback)
    return _COLOR_MAP.get(name, discord.Color.blurple())


def _loop_label(mode: LoopMode) -> str:
    key = {LoopMode.OFF: "off", LoopMode.SINGLE: "single", LoopMode.QUEUE: "queue"}[mode]
    defaults = {"off": "循環：關閉", "single": "循環：單首", "queue": "循環：佇列"}
    return get(f"music.loop_labels.{key}", defaults[key])


def _footer() -> str:
    return get("embed_footer.music", "音樂系統")


def _base(
    *,
    title:       str | None = None,
    description: str | None = None,
    color:       discord.Color,
) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text=_footer())
    return e


# ── 正在播放 ──────────────────────

def now_playing_embed(song: Song, queue: MusicQueue) -> discord.Embed:
    embed = _base(
        title       = "正在播放",
        description = f"[{song.title}]({song.webpage_url})",
        color       = _clr("playing"),
    )
    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)

    embed.add_field(name="時長",     value=song.duration_str,              inline=True)
    embed.add_field(name="點歌者",   value=song.requester.mention,         inline=True)
    embed.add_field(name="循環模式", value=_loop_label(queue.loop_mode),   inline=True)
    embed.add_field(name="佇列待播", value=f"{queue.size} 首",             inline=True)

    if song.uploader:
        embed.set_footer(text=f"上傳頻道：{song.uploader}  |  {_footer()}")
    return embed


# ── 加入佇列 ──────────────────────

def added_song_embed(song: Song, position: int) -> discord.Embed:
    embed = _base(
        title       = "已加入播放佇列",
        description = f"[{song.title}]({song.webpage_url})",
        color       = _clr("queued"),
    )
    if song.thumbnail:
        embed.set_thumbnail(url=song.thumbnail)
    embed.add_field(name="時長",     value=song.duration_str,     inline=True)
    embed.add_field(name="點歌者",   value=song.requester.mention, inline=True)
    embed.add_field(name="佇列位置", value=f"第 {position} 首",   inline=True)
    return embed


# ── 加入播放清單 ──────────────────────

def playlist_added_embed(songs: list[Song]) -> discord.Embed:
    embed = _base(
        title       = "已加入播放清單",
        description = f"共 **{len(songs)}** 首歌曲已加入佇列",
        color       = _clr("queued"),
    )
    if songs and songs[0].thumbnail:
        embed.set_thumbnail(url=songs[0].thumbnail)

    preview = [
        f"`{i+1}.` [{s.title}]({s.webpage_url}) `{s.duration_str}`"
        for i, s in enumerate(songs[:5])
    ]
    if len(songs) > 5:
        preview.append(f"...還有 **{len(songs) - 5}** 首")
    embed.add_field(name="歌曲預覽", value="\n".join(preview), inline=False)
    return embed


# ── 佇列清單（分頁） ──────────────────────

def queue_embed(queue: MusicQueue, page: int = 1, per_page: int = _PER_PAGE) -> discord.Embed:
    songs       = queue.songs
    total       = len(songs)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * per_page

    embed = _base(
        title = f"播放佇列（第 {page}/{total_pages} 頁，共 {total} 首）",
        color = _clr("info"),
    )

    if not songs:
        embed.description = "佇列目前為空，使用 /play 加入歌曲"
    else:
        lines = [
            f"`{start + i + 1}.` [{s.title}]({s.webpage_url}) `{s.duration_str}`"
            for i, s in enumerate(songs[start: start + per_page])
        ]
        embed.description = "\n".join(lines)

    footer_parts = []
    if queue.current:
        footer_parts.append(f"正在播放：{queue.current.title}")
    if total:
        footer_parts.append(f"總時長：{format_duration(queue.total_duration)}")
    if footer_parts:
        embed.set_footer(text="  |  ".join(footer_parts + [_footer()]))
    return embed


# ── 歷史記錄 ──────────────────────

def history_embed(queue: MusicQueue) -> discord.Embed:
    history = queue.history[:10]
    embed   = _base(title="最近播放記錄", color=_clr("info"))

    if not history:
        embed.description = "尚無播放記錄"
    else:
        lines = [
            f"`{i+1}.` [{s.title}]({s.webpage_url}) `{s.duration_str}`"
            for i, s in enumerate(history)
        ]
        embed.description = "\n".join(lines)
    return embed


# ── 通用 ──────────────────────

def success_embed(message: str) -> discord.Embed:
    return _base(description=message, color=_clr("success"))


def error_embed(message: str) -> discord.Embed:
    return _base(description=f"錯誤：{message}", color=_clr("error"))


def info_embed(message: str) -> discord.Embed:
    return _base(description=message, color=_clr("info"))
