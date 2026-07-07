"""
core/link_preview/registry.py

Modification():

- 新增本檔案：將 detector 判斷出的平台字串對應到對應的擷取器函式
- 新增 twitter、tiktok 兩個平台的註冊

職責：

- 提供 get_extractor()，Cog 層依此取得平台對應的擷取函式，
  新增平台時只需在 _REGISTRY 註冊一筆，不需修改 Cog 內的分支邏輯，
  避免硬編碼判斷式散落各處
"""

from __future__ import annotations

from typing import Awaitable, Callable

from core.link_preview import bilibili, instagram, pinterest, threads, tiktok, twitter
from core.link_preview.base import LinkPreview

Extractor = Callable[[str], Awaitable["LinkPreview | None"]]

_REGISTRY: dict[str, Extractor] = {
    "bilibili":  bilibili.extract,
    "instagram": instagram.extract,
    "threads":   threads.extract,
    "pinterest": pinterest.extract,
    "twitter":   twitter.extract,
    "tiktok":    tiktok.extract,
}


def get_extractor(platform: str) -> Extractor | None:
    """依平台字串取得對應擷取器；找不到時回傳 None。"""
    return _REGISTRY.get(platform)
