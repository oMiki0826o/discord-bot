"""
core/music/song.py

Modification():

- 移植自 music_bot/core/song.py，調整 import 路徑
- max_queue_size 改由 core.system.settings 讀取，不再依賴獨立 config
- FFmpeg 路徑從 settings 讀取
- from_playlist() 加入 ignoreerrors=True，播放清單中版權封鎖或私人
  影片不再中斷整份清單，改為跳過並回傳跳過數量
- 新增 _strip_ansi()，清除 yt-dlp 錯誤訊息中的 ANSI 顏色代碼
- 新增 _normalize_query()，修正搜尋關鍵字含冒號時被 yt-dlp 誤判為
  URL scheme 導致 NoSupportingHandlers 例外的問題；詳見下方說明
- from_playlist() 改為要求輸入必須是合法網址，非網址時直接回傳
  明確錯誤，不再嘗試用 generic extractor 解析

職責：

- 定義 Song 資料模型（dataclass）
- 單曲／播放清單解析（在執行緒池中執行，不阻塞事件迴圈）
- create_source() 於播放前即時提取串流網址，避免短期網址逾期
- duration_str 統一格式化時長

錯誤重現與修正說明（NoSupportingHandlers: Unsupported url scheme）：

- 現象：使用者以 /play 輸入含冒號的純文字查詢（例如「OP: 只有一個
  人」這類標題），yt-dlp 拋出
  `Unable to handle request: Unsupported url scheme: "index"`。
- 根本原因：_YTDL_BASE 原本僅設定 default_search=ytsearch，
  依賴 yt-dlp 自行判斷輸入「是否已經是一個網址」。但 yt-dlp 的
  判斷邏輯對「冒號前的文字」較為敏感，含冒號的純文字查詢容易被
  誤認成某種未知 scheme 的網址（例如「index: ...」被誤判為
  scheme 是 "index" 的網址），因而略過 default_search 前綴，
  直接把整段文字丟給不支援該 scheme 的 generic extractor 處理，
  最終失敗。
- 修正方式：不再依賴 yt-dlp 的自動判斷，改由 _normalize_query()
  在呼叫 yt-dlp 之前，自行判斷輸入是否為合法的 http(s) 網址；
  非網址一律明確加上 ytsearch: 前綴後再送入 yt-dlp。
  _YTDL_BASE 仍保留 default_search 設定作為第二層防護，
  但實際判斷已不再依賴它。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import discord
import yt_dlp

from core.system.settings import get, get_int

log = logging.getLogger("bot.music.song")

# ── ANSI 代碼清除 ──────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """移除 yt-dlp 錯誤訊息中的 ANSI 顏色代碼，避免在 Discord 顯示亂碼。"""
    return _ANSI_RE.sub("", text)


# ── 查詢字串正規化 ──────────────────────

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def _search_prefix() -> str:
    """
    從 settings 讀取搜尋前綴，預設為 ytsearch。

    設為可調整值而非寫死字串，讓未來若想改為 ytsearch5（取前 5 筆
    結果供使用者選擇）等變體行為時，不需修改程式碼即可切換。
    """
    return get("music.search_prefix", "ytsearch")


def _normalize_query(query: str) -> str:
    """
    確保非網址的查詢字串一定會被當作搜尋關鍵字處理。

    不依賴 yt-dlp 對「輸入是否像網址」的內部判斷（該判斷對含冒號的
    純文字查詢不可靠，見檔案頂部說明），改為我們自行判斷：
    以 http:// 或 https:// 開頭才視為網址原樣傳入，其餘一律加上
    搜尋前綴，確保 yt-dlp 一定會走搜尋路徑而非誤判為未知 scheme。
    """
    cleaned = query.strip()
    if _URL_RE.match(cleaned):
        return cleaned
    return f"{_search_prefix()}:{cleaned}"


def _is_valid_url(text: str) -> bool:
    """判斷輸入是否為合法的 http(s) 網址。"""
    return bool(_URL_RE.match(text.strip()))


# ── YT-DLP 配置 ──────────────────────

_YTDL_BASE: dict[str, Any] = {
    "format":         "bestaudio/best",
    "quiet":          True,
    "no_warnings":    True,
    "default_search": "ytsearch",  # 第二層防護；實際判斷已由 _normalize_query() 處理
    "source_address": "0.0.0.0",
}

# 單曲：保留例外傳播，讓使用者知道搜尋為何失敗
_YTDL_SINGLE = yt_dlp.YoutubeDL({**_YTDL_BASE, "noplaylist": True})

# 播放清單：ignoreerrors=True 讓 yt-dlp 跳過無法提取的影片
# （版權封鎖、私人影片、地區限制），失敗項目回傳 None 而非拋出例外
_YTDL_PLAYLIST = yt_dlp.YoutubeDL(
    {**_YTDL_BASE, "noplaylist": False, "ignoreerrors": True}
)


# ── FFmpeg 配置 ──────────────────────

def _ffmpeg_opts() -> dict[str, str]:
    """從 settings 讀取 ffmpeg 路徑，組裝 FFmpeg 選項（含斷線自動重連）。"""
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
    串流網址不在此儲存，由 create_source() 播放前即時提取。
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
        """
        從搜尋關鍵字或網址建立 Song。

        query 在送入 yt-dlp 前會先經過 _normalize_query() 正規化，
        非網址一律轉換為明確的搜尋語法，避免含冒號的查詢字串被
        誤判為未知 scheme 的網址。
        """
        normalized = _normalize_query(query)
        loop = asyncio.get_event_loop()
        try:
            data: dict[str, Any] = await loop.run_in_executor(
                None, _extract, _YTDL_SINGLE, normalized,
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

        與 from_query 不同，這裡不會把非網址輸入轉換成搜尋語法：
        /playlist 指令語意上就是要求提供播放清單網址，若輸入不是
        合法網址，直接回傳明確錯誤，避免產生語意不清的搜尋結果，
        也避免同樣落入 generic extractor 誤判 scheme 的情況。

        ignoreerrors=True 使 yt-dlp 遇到版權封鎖、私人影片或地區限制的
        影片時不拋出例外，而是在 entries 中回傳 None。
        此處過濾 None 並計算跳過數量，讓呼叫端可告知使用者詳情。
        """
        if not _is_valid_url(url):
            raise ValueError("請提供有效的播放清單網址（需以 http:// 或 https:// 開頭）")

        limit = get_int("music.max_queue_size", 200)
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
        """
        播放前即時提取最新串流網址，建立 FFmpeg 音訊來源。

        self.webpage_url 來自 yt-dlp 回傳的 webpage_url 欄位，
        必為合法網址，不需經過 _normalize_query() 處理。
        """
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
