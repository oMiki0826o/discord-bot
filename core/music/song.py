"""
core/music/song.py

職責：
- 定義 Song 資料模型（dataclass）
- YDL 單曲／播放清單解析（在執行緒池中執行，不阻塞事件迴圈）
- create_source() 在播放前即時提取串流 URL，避免 YouTube 短期連結逾期
- duration_str 統一格式化時長

Modification():

- 移植自 music_bot/core/song.py，調整 import 路徑
- max_queue_size 改由 core.system.settings 讀取，不再依賴獨立 config
- FFmpeg 路徑從 settings 讀取

- 修正 from_playlist() 播放清單中版權封鎖或私人影片導致整個清單加入失敗：
  _YTDL_PLAYLIST 加入 ignoreerrors=True，yt-dlp 遇到無法提取的
  影片時不拋出例外，改為回傳 None 項目，from_playlist() 過濾後
  計算跳過數量，回傳 tuple[list[Song], int]（歌曲清單, 跳過數）。
  單曲提取（_YTDL_SINGLE）維持不設定 ignoreerrors，確保搜尋失敗
  時能明確告知使用者原因。

- 新增 _strip_ansi()：yt-dlp 的錯誤訊息內含 ANSI 顏色代碼
  （例如 [0;31mERROR:[0m），直接顯示在 Discord embed 時會出現
  亂碼。統一在例外字串回傳前清除。

- music.voice_connect_timeout 設定鍵新增至 settings.json。

"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import discord
import yt_dlp

from core.system.settings import get

log = logging.getLogger("bot.music.song")

# ── ANSI 代碼清除 ──────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """移除 yt-dlp 錯誤訊息中的 ANSI 顏色代碼，避免在 Discord 顯示亂碼。"""
    return _ANSI_RE.sub("", text)


# ── YT-DLP 配置 ──────────────────────

_YTDL_BASE: dict[str, Any] = {
    "format":         "bestaudio/best",
    "quiet":          True,
    "no_warnings":    True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

# 單曲：保留例外傳播，讓使用者知道搜尋為何失敗
_YTDL_SINGLE = yt_dlp.YoutubeDL({**_YTDL_BASE, "noplaylist": True})

# 播放清單：ignoreerrors=True 讓 yt-dlp 跳過無法提取的影片
# （版權封鎖、私人影片、地區限制），失敗項目回傳 None 而非拋出例外
_YTDL_PLAYLIST = yt_dlp.YoutubeDL(
    {**_YTDL_BASE, "noplaylist": False, "ignoreerrors": True}
)

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
        try:
            data: dict[str, Any] = await loop.run_in_executor(
                None, _extract, _YTDL_SINGLE, query,
            )
        except Exception as exc:
            raise ValueError(_strip_ansi(str(exc))) from exc
        if "entries" in data:
            data = data["entries"][0]
        return _build_song(data, requester)

    # ── 工廠：播放清單 ──────────────────────

    @classmethod
    async def from_playlist(
        cls,
        url:       str,
        requester: discord.Member,
    ) -> tuple[list["Song"], int]:
        """
        解析 YouTube 播放清單，回傳 (成功歌曲清單, 跳過數量)。

        ignoreerrors=True 使 yt-dlp 遇到版權封鎖、私人影片或地區限制的
        影片時不拋出例外，而是在 entries 中回傳 None。
        此處過濾 None 並計算跳過數量，讓呼叫端可告知使用者詳情。
        """
        limit = int(get("music.max_queue_size", 200))
        loop  = asyncio.get_event_loop()
        try:
            data: dict[str, Any] = await loop.run_in_executor(
                None, _extract, _YTDL_PLAYLIST, url,
            )
        except Exception as exc:
            raise ValueError(_strip_ansi(str(exc))) from exc

        raw_entries = data.get("entries", [data])[:limit]
        songs:   list[Song] = []
        skipped: int        = 0

        for entry in raw_entries:
            if not entry:
                skipped += 1
                continue
            try:
                songs.append(_build_song(entry, requester))
            except Exception as exc:
                log.warning("跳過無效播放清單項目：%s", exc)
                skipped += 1

        return songs, skipped

    # ── 音訊來源 ──────────────────────

    async def create_source(self) -> discord.FFmpegPCMAudio:
        """播放前即時提取最新串流 URL，建立 FFmpeg 音訊來源。"""
        loop = asyncio.get_event_loop()
        try:
            data: dict[str, Any] = await loop.run_in_executor(
                None, _extract, _YTDL_SINGLE, self.webpage_url,
            )
        except Exception as exc:
            raise ValueError(_strip_ansi(str(exc))) from exc
        if "entries" in data:
            data = data["entries"][0]
        opts = _ffmpeg_opts()
        exe  = opts.pop("executable", "ffmpeg")
        return discord.FFmpegPCMAudio(data["url"], executable=exe, **opts)
