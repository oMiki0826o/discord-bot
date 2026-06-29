"""
core/ai/file_parser/constants.py

Modification():
- 集中管理附件解析所需的副檔名分類與大小限制。
- 啟用 audio / video / binary 分類，避免已存在的 parser 無法被 registry 使用。
- 圖片仍走 Gemini 多模態 Part，不進文字型 file_parser。

職責：
- 作為 file_parser 支援格式與安全限制的唯一來源。
- 避免副檔名與大小限制散落在各 parser 模組中。
"""

from __future__ import annotations

import os

# ── 副檔名分類（frozenset 加速 in 判斷） ──────────────────────

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

# ── 壓縮檔副檔名（僅列出清單，不解壓內容） ──────────────────────
# 注意：.tar.gz / .tar.bz2 / .tar.xz 屬複合副檔名，Path.suffix 只會取得
# 最後一段（".gz" / ".bz2" / ".xz"），因此這些單一副檔名也需註冊；
# archive_parser 內部會用 tarfile.is_tarfile() 進一步判斷是否為 tar 封裝，
# 區分「.tar.gz」與「單一檔案的 .gz」（後者沒有清單可列）。

ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({
    ".zip", ".tar", ".tgz", ".tbz2", ".txz",
    ".gz", ".bz2", ".xz",
    ".rar", ".7z",
})

# ── 音訊 / 影片副檔名：目前提供 metadata，STT / 抽幀留給後續階段 ──────────────────────

AUDIO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac",
})

VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".flv",
})

# ── 二進位副檔名：只做基本識別與 SHA-256，不執行、不反組譯 ──────────────────────

BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".exe", ".dll", ".so", ".dylib",
    ".bin", ".dat", ".img", ".iso",
    ".class", ".jar", ".war", ".ear",
    ".pyc", ".pyd",
})

# ── 圖片副檔名：不進 file_parser，直接組成 Gemini multimodal Part ──────────────────────

IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
})

# ── 所有 file_parser 可處理的副檔名（圖片不在此） ──────────────────────

SUPPORTED_EXTENSIONS: frozenset[str] = (
    TEXT_EXTENSIONS
    | CODE_EXTENSIONS
    | DOCUMENT_EXTENSIONS
    | ARCHIVE_EXTENSIONS
    | AUDIO_EXTENSIONS
    | VIDEO_EXTENSIONS
    | BINARY_EXTENSIONS
)

# ── 大小限制 ──────────────────────

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

# 音訊 / 影片 metadata 解析的時間上限參考值，未來 STT / 抽幀會使用
MAX_AUDIO_DURATION: int = _int_env("MAX_AUDIO_DURATION", 300)       # 5 分鐘
MAX_VIDEO_DURATION: int = _int_env("MAX_VIDEO_DURATION", 120)       # 2 分鐘
