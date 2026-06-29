"""
core/ai/file_parser/binary_parser.py

修正（二進位識別，不解析內容）：
- 不解析任何內容，只提供：MIME 類型、魔術字節識別、SHA-256 雜湊、檔案大小
- 魔術字節識別使用內建查表（不需外部套件），涵蓋最常見的執行檔格式
- 可選依賴 python-magic 做更精確的 MIME 偵測（pip install python-magic，需 libmagic）
- 雜湊值讓 AI 可回報給使用者做完整性驗證，本模組自身不做安全掃描

啟用方式（未來）：
1. constants.py 新增 BINARY_EXTENSIONS 集合
2. registry.py 的 REGISTRY 加入：
       **{ext: binary_parser.parse for ext in BINARY_EXTENSIONS},
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from core.ai.file_parser.models import ParsedFile

logger = logging.getLogger("bot.file_parser.binary")

_BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".exe", ".dll", ".so", ".dylib",
    ".bin", ".dat", ".img", ".iso",
    ".class", ".jar", ".war", ".ear",
    ".pyc", ".pyd",
})

# ── 常見魔術字節查表（無需 libmagic）────────────────────────────────────

_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b"MZ",                       "Windows PE 執行檔 / DLL"),
    (b"\x7fELF",                  "Linux ELF 執行檔"),
    (b"\xca\xfe\xba\xbe",         "Java Class 或 macOS Fat Binary"),
    (b"PK\x03\x04",               "ZIP 封裝（JAR / APK / DOCX 等）"),
    (b"\x89PNG\r\n\x1a\n",        "PNG 圖片"),
    (b"\xff\xd8\xff",             "JPEG 圖片"),
    (b"GIF87a",                   "GIF87 圖片"),
    (b"GIF89a",                   "GIF89 圖片"),
    (b"%PDF-",                    "PDF 文件"),
    (b"\x1f\x8b",                 "GZIP 壓縮"),
    (b"BZh",                      "BZIP2 壓縮"),
    (b"\xfd7zXZ",                 "XZ 壓縮"),
    (b"Rar!\x1a\x07",             "RAR 壓縮檔"),
    (b"7z\xbc\xaf'\x1c",          "7-Zip 壓縮檔"),
    (b"\x50\x4b\x05\x06",         "ZIP（空白）"),
    (b"SQLite format 3",          "SQLite 資料庫"),
    (b"\x00\x61\x73\x6d",         "WebAssembly 二進位"),
    (b"CAFEBABE",                 "Java Class（文字格式）"),
]


def parse(path: Path, filename: str, size_bytes: int) -> ParsedFile:
    """識別二進位檔類型並提取基本指紋，不讀取或解析任何內容。"""
    ext = path.suffix.lower()
    try:
        return _parse_binary(path, filename, ext, size_bytes)
    except Exception as e:
        logger.debug("[binary_parser] error file=%s: %s", filename, e)
        return ParsedFile(
            filename=filename, extension=ext,
            category="binary", size_bytes=size_bytes,
            error=str(e),
        )


def _parse_binary(
    path: Path, filename: str, ext: str, size_bytes: int,
) -> ParsedFile:
    # ── 讀取前 512 bytes 做魔術字節識別（不讀全部，省記憶體）──
    with path.open("rb") as f:
        header = f.read(512)

    # ── 魔術字節識別 ──────────────────────────────────────
    identified = "(未能識別)"
    for signature, description in _MAGIC_SIGNATURES:
        if header.startswith(signature):
            identified = description
            break

    # ── 可選：python-magic 做更精確識別 ──────────────────
    mime_type = "(需 python-magic)"
    try:
        import magic  # type: ignore
        mime_type = magic.from_file(str(path), mime=True)
    except ImportError:
        pass   # 未安裝，略過；魔術字節查表已足夠基本識別
    except Exception as e:
        logger.debug("[binary_parser] magic failed: %s", e)

    # ── SHA-256 雜湊（分塊讀取，不一次載入全部）──────────
    sha256 = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)

    lines = [
        f"副檔名：{ext}",
        f"識別結果：{identified}",
        f"MIME 類型：{mime_type}",
        f"檔案大小：{size_bytes // 1024} KB",
        f"SHA-256：{sha256.hexdigest()}",
    ]

    return ParsedFile(
        filename=filename, extension=ext,
        category="binary", size_bytes=size_bytes,
        content="\n".join(lines),
    )
