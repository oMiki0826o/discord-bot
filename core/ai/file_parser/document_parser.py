"""
core/ai/file_parser/document_parser.py

Modification():

- 全面改用 markitdown 套件（Microsoft 開源，pip install markitdown）
  取代原本 pypdf / python-docx / openpyxl / python-pptx 四套各自獨立
  的文字提取邏輯：

  1. 修正既有 bug：requirements.txt 原本寫的是 pypdf2（安裝後的模組
     名稱是 PyPDF2），但本檔案實際 import 的是 pypdf（完全不同的
     套件，需另外安裝）。實測建立乾淨的虛擬環境、依 requirements.txt
     安裝、執行 `import pypdf`，結果是 ModuleNotFoundError——代表 PDF
     解析這條路徑一直靜默失敗，永遠落入下方「缺少套件」的錯誤分支。
     改用 markitdown 後不再需要這個容易寫錯名稱的相依套件。
  2. 四種格式原本各自維護一套提取程式碼（合計約 150 行），現在統一
     呼叫 MarkItDown().convert()，程式碼量大幅減少。已實際安裝
     markitdown 0.1.6 並用 docx / xlsx / pptx / pdf 測試檔驗證：
     輸出的 result.text_content 為結構化 Markdown（標題轉成
     # heading、表格轉成正規 Markdown 表格），相較舊版 xlsx 解析器
     逐列輸出「儲存格 | 儲存格」的純文字堆疊，資訊密度更高、
     格式雜訊更少，有助於降低 AI 閱讀文件時的 token 消耗。
  3. 額外受益：markitdown 透過 xls extra 同時支援舊版 .xls，原本
     一律回絕「請轉換為 .xlsx」，現在可以直接解析；.doc / .ppt 仍
     超出 markitdown 的支援範圍（僅涵蓋 Office Open XML 格式），
     維持原本的引導訊息，請使用者自行轉換。
- 移除不再使用的 _MAX_XLSX_ROWS（原本以列數限制 Excel 讀取範圍；
  現在改由 markitdown 統一輸出後，交給既有的 truncate() 依字元數
  把關即可，避免兩套截斷邏輯同時存在造成混淆）。
- 仍維持同步呼叫介面：CPU 密集的轉換工作統一由 __init__.py 的
  parse() 透過 asyncio.to_thread 排程至執行緒池，本模組不自行
  處理執行緒，與其餘 parser 保持一致的呼叫慣例。

職責：
- 涵蓋 pdf / docx / xlsx / xls / pptx 文字提取，統一輸出 Markdown
  格式的純文字內容
- markitdown 為可選依賴，缺少對應 extra 時回傳明確 error，不中斷
  整體檔案解析流程
- PDF 僅提取文字層；掃描型 PDF（無文字層）或內容為空時回傳明確
  提示，不做 OCR（OCR 屬未來規劃）
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.ai.file_parser.models import ParsedFile
from core.ai.file_parser.summary_builder import truncate

logger = logging.getLogger("bot.file_parser.document")

# markitdown 實際涵蓋的格式（皆需安裝對應 extra，見 requirements.txt）。
_MARKITDOWN_EXTS = frozenset({".pdf", ".docx", ".xlsx", ".xls", ".pptx"})

# .doc / .ppt 為舊版二進位格式，超出 markitdown 的支援範圍（僅涵蓋
# Office Open XML），未來若要支援需額外整合 antiword / LibreOffice
# 之類的轉檔工具，目前仍請使用者自行轉換為新格式。
_UNSUPPORTED_LEGACY: dict[str, str] = {
    ".doc": "不支援舊版 .doc 格式，請轉換為 .docx",
    ".ppt": "不支援舊版 .ppt 格式，請轉換為 .pptx",
}


# ── 主要入口 ──────────────────────

def parse(path: Path, filename: str, size_bytes: int) -> ParsedFile:
    """解析文件類檔案，回傳 ParsedFile（同步函式，由上層統一排程至執行緒池）。"""
    ext = path.suffix.lower()
    try:
        return _dispatch(path, filename, ext, size_bytes)
    except Exception as e:
        logger.debug("[document_parser] error file=%s: %s", filename, e)
        return ParsedFile(
            filename=filename, extension=ext,
            category="document", size_bytes=size_bytes,
            error=str(e),
        )


# ── 格式分派 ──────────────────────

def _dispatch(path: Path, filename: str, ext: str, size_bytes: int) -> ParsedFile:
    if ext in _MARKITDOWN_EXTS:
        return _parse_with_markitdown(path, filename, ext, size_bytes)

    if ext in _UNSUPPORTED_LEGACY:
        return ParsedFile(
            filename=filename, extension=ext,
            category="document", size_bytes=size_bytes,
            error=_UNSUPPORTED_LEGACY[ext],
        )

    return ParsedFile(
        filename=filename, extension=ext,
        category="document", size_bytes=size_bytes,
        error=f"不支援的文件格式：{ext}",
    )


# ── markitdown 統一轉換 ──────────────────────

def _parse_with_markitdown(path: Path, filename: str, ext: str, size_bytes: int) -> ParsedFile:
    try:
        from markitdown import MarkItDown
    except ImportError:
        return ParsedFile(
            filename=filename, extension=ext,
            category="document", size_bytes=size_bytes,
            error="缺少 markitdown 套件，無法解析文件",
        )

    # 每次呼叫建立獨立實例（而非重用模組級單例）：markitdown 官方文件
    # 未明確保證 convert() 在多執行緒併發呼叫同一實例時的安全性，
    # 而本函式會被排程至執行緒池平行執行；建立實例的成本很低，
    # 不足以影響效能，換取明確不共享狀態較為穩妥。
    result    = MarkItDown().convert(str(path))
    full_text = (result.text_content or "").strip()

    if not full_text:
        return ParsedFile(
            filename=filename, extension=ext,
            category="document", size_bytes=size_bytes,
            content=f"[{ext} 檔案未提取到可用文字內容，若為掃描型 PDF 或純圖片投影片，屬已知限制（未內建 OCR）]",
            error=None,
        )

    content, truncated = truncate(full_text)
    return ParsedFile(
        filename=filename, extension=ext,
        category="document", size_bytes=size_bytes,
        content=content, truncated=truncated,
    )
