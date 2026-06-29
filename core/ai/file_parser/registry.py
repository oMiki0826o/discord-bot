"""
core/ai/file_parser/registry.py

Modification():
- 使用 dict 將副檔名對應到 parser function，取代 if-elif 鏈。
- 啟用 audio / video / binary parser，讓已存在的解析器真正接進附件流程。
- 新增格式時只需在 constants.py 分類並在此註冊 parser。

職責：
- 提供副檔名到解析器的唯一查詢入口。
- 保持所有 parser function 介面一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from core.ai.file_parser.models import ParsedFile
from core.ai.file_parser import (
    archive_parser,
    audio_parser,
    binary_parser,
    code_parser,
    document_parser,
    text_parser,
    video_parser,
)
from core.ai.file_parser.constants import (
    ARCHIVE_EXTENSIONS,
    AUDIO_EXTENSIONS,
    BINARY_EXTENSIONS,
    CODE_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    TEXT_EXTENSIONS,
    VIDEO_EXTENSIONS,
)

# ── Parser 函式型別 ──────────────────────

ParserFn = Callable[[Path, str, int], ParsedFile]


# ── 註冊表：副檔名 → parser function ──────────────────────
# 新增格式只需在此追加一行，不修改任何分派邏輯。

REGISTRY: dict[str, ParserFn] = {
    **{ext: text_parser.parse     for ext in TEXT_EXTENSIONS},
    **{ext: code_parser.parse     for ext in CODE_EXTENSIONS},
    **{ext: document_parser.parse for ext in DOCUMENT_EXTENSIONS},
    **{ext: archive_parser.parse  for ext in ARCHIVE_EXTENSIONS},
    **{ext: audio_parser.parse    for ext in AUDIO_EXTENSIONS},
    **{ext: video_parser.parse    for ext in VIDEO_EXTENSIONS},
    **{ext: binary_parser.parse   for ext in BINARY_EXTENSIONS},
}


def get_parser(extension: str) -> ParserFn | None:
    """依副檔名查詢對應 parser，找不到回傳 None。"""
    return REGISTRY.get(extension.lower())
