"""
utils/formatter.py

職責：
- 通用格式化工具函式（時間、文字截斷、位元組、代碼區塊）

Modification():

- 移植自 Bot-Firefly/utils/formatter.py
- format_duration 與 core/music/song.py 的 format_duration 定義一致
- 新增 human_number()：K/M/B 縮寫

"""

from __future__ import annotations


def format_duration(seconds: int | float | None) -> str:
    """將秒數格式化為 MM:SS 或 HH:MM:SS。"""
    if not seconds:
        return "00:00"
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def truncate_text(text: str, limit: int = 1000) -> str:
    """超過 limit 字元時截斷並附加 '...'。"""
    return text if len(text) <= limit else text[:limit] + "..."


def codeblock(text: str, language: str = "") -> str:
    """將文字包裝為 Discord Code Block。"""
    return f"```{language}\n{text}\n```"


def human_bytes(size: int | float) -> str:
    """將位元組轉為人類可讀格式（B / KB / MB / GB）。"""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def human_number(n: int | float) -> str:
    """將大數字縮寫（1500 → 1.5K）。"""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))
