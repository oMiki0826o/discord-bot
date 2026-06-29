"""
core/logging/traceback_utils.py

修正：
- 補上完整型別註記與 docstring
- 維持原始切割邏輯，僅調整命名與註解格式
"""

from __future__ import annotations

from .constants import TRACEBACK_CHUNK_SIZE


# ── 切割長 traceback 文字為 Discord 訊息可接受的長度 ──────────────────────
def split_traceback(text: str) -> list[str]:
    """將過長的 traceback 文字切割為多段，並以 code block 包裝。"""
    chunks: list[str] = []

    while text:
        chunk, text = text[:TRACEBACK_CHUNK_SIZE], text[TRACEBACK_CHUNK_SIZE:]
        chunks.append(f"```\n{chunk}\n```")

    return chunks
