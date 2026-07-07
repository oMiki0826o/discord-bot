"""
core/link_preview/detector.py

Modification():

- 新增本檔案：從訊息文字中偵測 Discord 原生 Embed 支援不佳（或
  完全沒有）的連結，回傳 [(platform, url), ...] 供後續擷取器使用
- 新增 twitter、tiktok 平台規則
- 修正網域比對邏輯：原本用「子字串是否出現在整個網址中」判斷，
  對極短網域（例如 x.com）容易誤判，例如 xbox.com 這個網址本身
  就包含連續子字串 "x.com"（x-b-o-x-.-c-o-m 之中的 x.com），
  會被誤判為 Twitter/X 連結。改為先解析出網址真正的 hostname，
  再要求 hostname 完全等於候選網域、或以 "." + 候選網域 結尾
  （涵蓋 www.x.com 這類子網域），不再對整個網址字串做子字串搜尋

職責：

- 掃描訊息內容，比對已知平台網域，回傳命中的 (platform, url) 清單

設計原則：

- 平台與對應的網域比對規則集中於 PLATFORM_PATTERNS，新增平台只需
  在此新增一筆規則，不需修改偵測邏輯本身，避免判斷式散落各處
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_URL_RE = re.compile(r"https?://\S+")
_TRAILING_NOISE = ")>,、。」』"  # 常見的中文標點或括號結尾雜訊，需從網址尾端去除


# ── 平台規則 ──────────────────────

@dataclass(frozen=True, slots=True)
class PlatformRule:
    """單一平台的網域比對規則。"""

    platform:      str
    host_patterns: tuple[str, ...]  # 命中任一候選網域（依 hostname 邊界比對）即視為此平台


PLATFORM_PATTERNS: tuple[PlatformRule, ...] = (
    PlatformRule("bilibili",  ("b23.tv", "bilibili.com")),
    PlatformRule("instagram", ("instagram.com",)),
    PlatformRule("threads",   ("threads.com", "threads.net")),
    PlatformRule("pinterest", ("pinterest.com", "pin.it")),
    PlatformRule("twitter",   ("twitter.com", "x.com")),
    PlatformRule("tiktok",    ("tiktok.com",)),
)


# ── 對外介面 ──────────────────────

def detect_links(content: str) -> list[tuple[str, str]]:
    """
    掃描訊息內容，回傳 [(platform, url), ...]。

    同一則訊息可能包含多個連結；回傳順序與訊息中出現順序一致，
    呼叫端可自行決定要處理前幾筆（見 settings.json
    link_preview.max_embeds_per_message）。
    """
    matches: list[tuple[str, str]] = []
    for raw_url in _URL_RE.findall(content):
        url = raw_url.rstrip(_TRAILING_NOISE)
        platform = _match_platform(url)
        if platform is not None:
            matches.append((platform, url))
    return matches


def _match_platform(url: str) -> str | None:
    """
    回傳命中的平台字串；沒有任何規則命中時回傳 None。

    比對對象是解析後的 hostname，而非整個網址字串，避免極短網域
    （如 x.com）被其他網域的子字串意外命中（如 xbox.com）。
    """
    hostname = (urlsplit(url).hostname or "").lower()
    if not hostname:
        return None

    for rule in PLATFORM_PATTERNS:
        if any(_host_matches(hostname, pattern) for pattern in rule.host_patterns):
            return rule.platform
    return None


def _host_matches(hostname: str, pattern: str) -> bool:
    """hostname 完全等於 pattern，或以 "." + pattern 結尾（涵蓋子網域）。"""
    return hostname == pattern or hostname.endswith(f".{pattern}")
