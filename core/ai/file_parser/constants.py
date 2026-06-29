"""
core/ai/file_parser/constants.py

修正（集中管理，呼應 core.ai.models 的「單一處定義」原則）：
- 所有副檔名分類集中於此，禁止散落在各 parser 模組
- 所有大小限制與安全限制集中於此
- 大小限制可在此直接修改，或未來擴充為從 settings.json 讀取
- image_parser 不存在；圖片走 Gemini 多模態路線，此處僅定義允許集合，
  供 cogs/ai/chat.py 判斷「附件要走 file_parser 還是直接當圖片 Part」
"""

from __future__ import annotations

import os

# ── 副檔名分類（frozenset 加速 in 判斷）────────────────────────────────

TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".txt", ".md", ".log", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env",
})

CODE_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".kt", ".swift",
    ".c", ".cpp", ".cc", ".h", ".hpp",
    ".go", ".rs", ".rb", ".php",
    ".sh", ".bash", ".zsh", ".fish",
    ".sql", ".html", ".css", ".scss",
    ".r", ".m", ".lua", ".dart",
})

DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".docx", ".doc",
    ".xlsx", ".xls",
    ".pptx", ".ppt",
})

# ── 壓縮檔副檔名（僅列出清單，不解壓內容）──────────────────────────────
# 注意：.tar.gz / .tar.bz2 / .tar.xz 屬複合副檔名，Path.suffix 只會取得
# 最後一段（".gz" / ".bz2" / ".xz"），因此這些單一副檔名也需註冊；
# archive_parser 內部會用 tarfile.is_tarfile() 進一步判斷是否為 tar 封裝，
# 區分「.tar.gz」與「單一檔案的 .gz」（後者沒有清單可列）。

ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({
    ".zip", ".tar", ".tgz", ".tbz2", ".txz",
    ".gz", ".bz2", ".xz",
    ".rar", ".7z",
})

# ── 圖片副檔名：不進 file_parser，直接組成 Gemini multimodal Part ───────

IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
})

# ── 所有 file_parser 可處理的副檔名（圖片不在此）──────────────────────

SUPPORTED_EXTENSIONS: frozenset[str] = (
    TEXT_EXTENSIONS | CODE_EXTENSIONS | DOCUMENT_EXTENSIONS | ARCHIVE_EXTENSIONS
)

# ── 大小限制 ────────────────────────────────────────────────────────────

def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, ""))
    except (ValueError, TypeError):
        return default

# 單檔大小上限（bytes）
MAX_FILE_SIZE: int = _int_env("MAX_FILE_SIZE", 20 * 1024 * 1024)   # 20 MB

# 文字截斷字元上限
MAX_TEXT_CHARS: int = _int_env("MAX_TEXT_CHARS", 30_000)

# 圖片大小上限（bytes）
MAX_IMAGE_SIZE: int = _int_env("MAX_IMAGE_SIZE", 10 * 1024 * 1024) # 10 MB

# 壓縮檔 Manifest 最多列出的項目數（避免內含上萬檔案的壓縮檔塞爆 prompt）
MAX_ARCHIVE_ENTRIES: int = _int_env("MAX_ARCHIVE_ENTRIES", 300)
