"""
core/music/song.py

職責：
- 定義 Song 資料模型（dataclass）
- YDL 單曲 / 播放清單解析（在執行緒池中執行，不阻塞事件迴圈）
- create_source() 在播放前即時提取串流 URL，避免 YouTube 短期連結逾期
- duration_str 統一格式化時長

Modification():

- 移植自 music_bot/core/song.py，調整 import 路徑
- max_queue_size 改由 core.system.settings 讀取，不再依賴獨立 config
- FFmpeg 路徑從 settings 讀取

"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import discord
import yt_dlp

from core.system.settings import get

log = logging.getLogger("bot.music.song")

# ── YT-DLP 配置 ──────────────────────

_YTDL_BASE: dict[str, Any] = {
    "format":         "bestaudio/best",
    "quiet":          True,
    "no_warnings":    True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

_YTDL_SINGLE   = yt_dlp.YoutubeDL({**_YTDL_BASE, "noplaylist": True})
_YTDL_PLAYLIST = yt_dlp.YoutubeDL({**_YTDL_BASE, "noplaylist": False})

# ── FFmpeg 配置（斷線自動重連） ──────────────────────

def _ffmpeg_opts() -> dict[str, str]:
    """從 settings 讀取 ffmpeg 路徑，組裝 FFmpeg 選項。"""
    return {
        "executable":     get("music.ffmpeg_path", "ffmpeg"),
        "before_options": (
            "-reconnect 1 "
            "-reconnect_streamed 1 "
            "-reconnect_delay_max 5"
        ),
        "options": "-vn",
    }


# ── 工具函式 ──────────────────────

def _extract(ytdl: yt_dlp.YoutubeDL, query: str) -> dict[str, Any]:
    """同步提取，供 executor 使用。"""
    return ytdl.extract_info(query, download=False)


def _build_song(data: dict[str, Any], requester: discord.Member) -> "Song":
    """從 yt-dlp 資料字典建立 Song 物件。"""
    return Song(
        title       = data.get("title", "未知標題"),
        webpage_url = data.get("webpage_url") or data.get("url", ""),
        uploader    = data.get("uploader") or data.get("channel", ""),
        duration    = int(data.get("duration") or 0),
        thumbnail   = data.get("thumbnail"),
        requester   = requester,
    )


def format_duration(seconds: int) -> str:
    """將秒數格式化為 MM:SS 或 HH:MM:SS。"""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ── Song 資料模型 ──────────────────────

@dataclass
class Song:
    """
    單首歌曲的不可變資料容器。
    串流 URL 不在此儲存，由 create_source() 播放前即時提取。
    """

    title:       str
    webpage_url: str
    uploader:    str
    duration:    int
    thumbnail:   str | None
    requester:   discord.Member

    @property
    def duration_str(self) -> str:
        return format_duration(self.duration)

    # ── 工廠：單曲 ──────────────────────

    @classmethod
    async def from_query(cls, query: str, requester: discord.Member) -> "Song":
        """從搜尋關鍵字或 URL 建立 Song。"""
        loop = asyncio.get_event_loop()
        data: dict[str, Any] = await loop.run_in_executor(
            None, _extract, _YTDL_SINGLE, query,
        )
        if "entries" in data:
            data = data["entries"][0]
        return _build_song(data, requester)

    # ── 工廠：播放清單 ──────────────────────

    @classmethod
    async def from_playlist(cls, url: str, requester: discord.Member) -> "list[Song]":
        """解析 YouTube 播放清單，回傳歌曲清單（上限 max_queue_size）。"""
        limit = int(get("music.max_queue_size", 200))
        loop  = asyncio.get_event_loop()
        data: dict[str, Any] = await loop.run_in_executor(
            None, _extract, _YTDL_PLAYLIST, url,
        )
        entries = data.get("entries", [data])
        songs: list[Song] = []
        for entry in entries[:limit]:
            if not entry:
                continue
            try:
                songs.append(_build_song(entry, requester))
            except Exception as exc:
                log.warning("跳過無效播放清單項目：%s", exc)
        return songs

    # ── 音訊來源 ──────────────────────

    async def create_source(self) -> discord.FFmpegPCMAudio:
        """播放前即時提取最新串流 URL，建立 FFmpeg 音訊來源。"""
        loop = asyncio.get_event_loop()
        data: dict[str, Any] = await loop.run_in_executor(
            None, _extract, _YTDL_SINGLE, self.webpage_url,
        )
        if "entries" in data:
            data = data["entries"][0]
        opts = _ffmpeg_opts()
        exe  = opts.pop("executable", "ffmpeg")
        return discord.FFmpegPCMAudio(data["url"], executable=exe, **opts)
