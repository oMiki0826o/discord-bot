"""
core/link_preview/summary_trigger.py

Modification():
- 新增本檔案：偵測「關鍵字 + 網址」形式的摘要請求（例如：
  「摘要https://...」），與被動的平台預覽（Bilibili／Instagram／
  Threads／Pinterest）分離，刻意設計為需要明確關鍵字才會觸發，
  避免每則貼連結的訊息都被動消耗 Gemma API 額度。

職責：
- 從訊息文字判斷使用者是否要求對某個網址做摘要，回傳該網址。
"""

from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://\S+")
_TRAILING_NOISE = ")>,、。」』"


def find_summary_request(content: str, *, keyword: str) -> str | None:
    """
    尋找「keyword 之後緊接著網址」的形式，回傳網址；找不到則回傳 None。

    只要求 keyword 出現在網址之前的同一則訊息內，不要求緊鄰無空白，
    例如「摘要https://...」與「幫我摘要一下 https://...」皆會命中。
    """
    if not content or keyword not in content:
        return None

    keyword_index = content.index(keyword)
    remainder = content[keyword_index + len(keyword):]

    match = _URL_RE.search(remainder)
    if match is None:
        return None

    return match.group(0).rstrip(_TRAILING_NOISE)
