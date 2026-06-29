"""
core/ai/file_parser/summary_builder.py

修正（截斷策略）：
- 大型文字取頭尾，保留最有資訊量的部分
- 程式碼取前段，通常 import 與頂層定義在前
- 所有截斷皆設定 ParsedFile.truncated = True
- 單一函式，供所有 parser 在回傳前呼叫
"""

from __future__ import annotations

from core.ai.file_parser.constants import MAX_TEXT_CHARS


# ── 截斷策略 ─────────────────────────────────────────────────────────────

def truncate(content: str, max_chars: int = MAX_TEXT_CHARS) -> tuple[str, bool]:
    """
    若 content 超過 max_chars，取前 70% + 後 30% 並插入省略提示。
    回傳 (截斷後文字, 是否曾截斷)。

    取頭尾而非純頭部，目的是保留：
    - 前段：import、宣告、設定（程式碼）；標題、導言（文件）
    - 後段：結論、摘要、錯誤訊息（日誌）
    """
    if len(content) <= max_chars:
        return content, False

    head = int(max_chars * 0.70)
    tail = max_chars - head
    truncated = (
        content[:head]
        + "\n\n... [內容過長，中間部分已省略] ...\n\n"
        + content[-tail:]
    )
    return truncated, True


def truncate_lines(
    content: str,
    max_lines: int,
) -> tuple[str, bool]:
    """
    依行數截斷，適合 CSV / 日誌等以列為單位的格式。
    保留前 80% + 後 20% 行數。
    """
    lines = content.splitlines()
    if len(lines) <= max_lines:
        return content, False

    head = int(max_lines * 0.80)
    tail = max_lines - head
    result = (
        "\n".join(lines[:head])
        + f"\n\n... [共 {len(lines)} 行，中間已省略] ...\n\n"
        + "\n".join(lines[-tail:])
    )
    return result, True
