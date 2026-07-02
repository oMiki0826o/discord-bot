"""
core/link_preview/detector.py

Modification():

- 新增本檔案：從訊息文字中偵測 Discord 原生 Embed 支援不佳（或
  完全沒有）的連結，回傳 [(platform, url), ...] 供後續擷取器使用

職責：

- 掃描訊息內容，比對已知平台網域，回傳命中的 (platform, url) 清單

設計原則：

- 平台與對應的網域比對規則集中於 PLATFORM_PATTERNS，新增平台只需
  在此新增一筆規則，不需修改偵測邏輯本身，避免判斷式散落各處
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_URL_RE = re.compile(r"https?://\S+")
_TRAILING_NOISE = ")>,、。」』"  # 常見的中文標點或括號結尾雜訊，需從網址尾端去除


# ── 平台規則 ──────────────────────

@dataclass(frozen=True, slots=True)
class PlatformRule:
    """單一平台的網域比對規則。"""

    platform: str
    host_patterns: tuple[str, ...]  # 命中任一 host 關鍵字即視為此平台


PLATFORM_PATTERNS: tuple[PlatformRule, ...] = (
    PlatformRule("bilibili", ("b23.tv", "bilibili.com")),
    PlatformRule("instagram", ("instagram.com",)),
    PlatformRule("threads", ("threads.com", "threads.net")),
    PlatformRule("pinterest", ("pinterest.com", "pin.it")),
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
    """回傳命中的平台字串；沒有任何規則命中時回傳 None。"""
    lowered = url.lower()
    for rule in PLATFORM_PATTERNS:
        if any(host in lowered for host in rule.host_patterns):
            return rule.platform
    return None
