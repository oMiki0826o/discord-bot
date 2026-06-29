"""
core/ai/file_parser/__init__.py

修正（統一入口）：
- 外部只呼叫 parse()，不直接操作內部模組（registry / 各 parser）
- 內部流程：大小檢查 → 副檔名判斷 → 查 registry → 呼叫對應 parser
  → 例外捕捉（單檔失敗不中斷整體）→ 回傳 ParsedFile
- 所有 parser 皆為同步函式，此處統一以 asyncio.to_thread 排程到執行緒池，
  避免 CPU 密集的解析（如 PDF 文字提取）阻塞 Discord Bot 主事件迴圈
- 找不到 parser 時，category 標記為 unknown，回傳明確 error，不拋出例外
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from core.ai.file_parser.constants import MAX_FILE_SIZE
from core.ai.file_parser.models import ParsedFile
from core.ai.file_parser.registry import get_parser

logger = logging.getLogger("bot.file_parser")

__all__ = ["parse", "ParsedFile"]


# ── 統一入口 ─────────────────────────────────────────────────────────────

async def parse(path: Path | str, filename: str | None = None) -> ParsedFile:
    """
    解析單一檔案，回傳 ParsedFile。

    參數：
    - path：檔案在磁碟上的實際路徑
    - filename：顯示用檔名（預設取 path 的檔名，Discord 附件可傳原始檔名）

    流程：大小檢查 → 副檔名判斷 → 查 registry → 執行緒池呼叫 parser → 例外捕捉。
    任何一步失敗都回傳帶 error 的 ParsedFile，不拋出例外，確保單檔失敗
    不會中斷整體對話流程。
    """
    path = Path(path)
    name = filename or path.name
    ext  = path.suffix.lower()

    # ── 1. 大小檢查 ──────────────────────────────────────
    try:
        size_bytes = path.stat().st_size
    except OSError as e:
        logger.warning("[file_parser] stat 失敗 file=%s: %s", name, e)
        return ParsedFile(
            filename=name, extension=ext, category="unknown",
            size_bytes=0, error=f"無法讀取檔案資訊：{e}",
        )

    if size_bytes > MAX_FILE_SIZE:
        logger.info(
            "[file_parser] 檔案過大拒絕解析 file=%s size=%d limit=%d",
            name, size_bytes, MAX_FILE_SIZE,
        )
        return ParsedFile(
            filename=name, extension=ext, category="unknown",
            size_bytes=size_bytes,
            error=f"檔案大小 {size_bytes // 1024}KB 超過上限 "
                  f"{MAX_FILE_SIZE // 1024}KB",
        )

    # ── 2. 查詢 registry ─────────────────────────────────
    parser_fn = get_parser(ext)
    if parser_fn is None:
        logger.info("[file_parser] 不支援的副檔名 file=%s ext=%s", name, ext)
        return ParsedFile(
            filename=name, extension=ext, category="unknown",
            size_bytes=size_bytes, error=f"不支援的檔案格式：{ext or '(無副檔名)'}",
        )

    # ── 3. 執行緒池呼叫對應 parser（CPU 密集不阻塞 event loop）────
    try:
        result = await asyncio.to_thread(parser_fn, path, name, size_bytes)
        logger.debug(
            "[file_parser] 解析完成 file=%s ext=%s ok=%s truncated=%s",
            name, ext, result.error is None, result.truncated,
        )
        return result
    except Exception as e:
        # 單一檔案解析失敗不可中斷整體流程
        logger.exception("[file_parser] 解析異常 file=%s ext=%s", name, ext)
        return ParsedFile(
            filename=name, extension=ext, category="unknown",
            size_bytes=size_bytes, error=f"解析時發生未預期錯誤：{e}",
        )
