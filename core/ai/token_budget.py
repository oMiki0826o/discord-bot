"""Prompt token 預算的輕量級本地估算工具。

這裡不在每次對話前額外呼叫遠端 count_tokens API，避免拖慢回覆。
估算會對 CJK 文字採較保守的比例，並在 Prompt 組裝時留出安全邊界。
"""

from __future__ import annotations

import math
import re

_TOKEN_PARTS = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
    r"|[A-Za-z0-9_]+"
    r"|[^\s]"
)


def estimate_tokens(text: str) -> int:
    """保守估算文字 token 數，適用於中英混合與程式碼。"""
    total = 0
    for part in _TOKEN_PARTS.findall(text):
        if len(part) == 1 and ord(part) >= 0x3000:
            total += 1
        elif part.isascii() and (part.isalnum() or "_" in part):
            total += max(1, math.ceil(len(part) / 4))
        else:
            total += 1
    return total


def truncate_to_tokens(text: str, limit: int, *, keep_both_ends: bool = True) -> str:
    """將文字壓到 token 預算內，預設保留頭尾並標示截斷。"""
    limit = max(0, limit)
    if not text or limit == 0:
        return ""
    if estimate_tokens(text) <= limit:
        return text

    marker = "\n…（內容已截斷）…\n"
    marker_tokens = estimate_tokens(marker)
    if limit <= marker_tokens:
        return _fit_prefix(text, limit)

    content_budget = limit - marker_tokens
    if not keep_both_ends:
        return _fit_prefix(text, content_budget) + marker

    head_budget = (content_budget + 1) // 2
    tail_budget = content_budget - head_budget
    head = _fit_prefix(text, head_budget)
    tail = _fit_suffix(text[len(head):], tail_budget)
    return head + marker + tail


def _fit_prefix(text: str, limit: int) -> str:
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[:mid]) <= limit:
            low = mid
        else:
            high = mid - 1
    return text[:low]


def _fit_suffix(text: str, limit: int) -> str:
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[len(text) - mid:]) <= limit:
            low = mid
        else:
            high = mid - 1
    return text[len(text) - low:] if low else ""
