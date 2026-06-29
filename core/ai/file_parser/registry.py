"""
core/ai/file_parser/registry.py

修正（註冊表架構）：
- 用 dict 將副檔名對應到 parser function，取代 if-elif 鏈
- 新增格式只需：constants.py 加分類 → 寫 parser → 在此追加一行
- 風格對齊 core/system/startup_registry.py 的「追加一筆設定即可擴充」原則
- 所有 parser function 介面統一：(path, filename, size_bytes) -> ParsedFile
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from core.ai.file_parser.models import ParsedFile
from core.ai.file_parser import text_parser, code_parser, document_parser, archive_parser
from core.ai.file_parser.constants import (
    TEXT_EXTENSIONS, CODE_EXTENSIONS, DOCUMENT_EXTENSIONS, ARCHIVE_EXTENSIONS,
)

# ── Parser 函式型別 ──────────────────────────────────────────────────────

ParserFn = Callable[[Path, str, int], ParsedFile]


# ── 註冊表：副檔名 → parser function ────────────────────────────────────
# 新增格式只需在此追加一行，不修改任何分派邏輯。

REGISTRY: dict[str, ParserFn] = {
    **{ext: text_parser.parse     for ext in TEXT_EXTENSIONS},
    **{ext: code_parser.parse     for ext in CODE_EXTENSIONS},
    **{ext: document_parser.parse for ext in DOCUMENT_EXTENSIONS},
    **{ext: archive_parser.parse  for ext in ARCHIVE_EXTENSIONS},
}


def get_parser(extension: str) -> ParserFn | None:
    """依副檔名查詢對應 parser，找不到回傳 None。"""
    return REGISTRY.get(extension.lower())
