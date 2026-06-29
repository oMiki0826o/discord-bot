"""
core/ai/file_parser/archive_parser.py

修正（Manifest 模式，安全第一）：
- 預設只列出清單，絕對不自動解壓縮，防止 Zip Bomb 與路徑穿越攻擊
- 支援 zip / tar（含 .tar.gz / .tar.bz2 / .tar.xz）/ 7z（需 py7zr 套件）
- RAR 需 rarfile 套件（libunrar 系統依賴），缺少時回傳 error 說明而非崩潰
- .gz / .bz2 / .xz 若為 tarball 封裝則列清單；若為單一壓縮檔則只回傳解壓後
  的推測檔名（例：data.json.gz → data.json），不解壓也不讀取內容
- 項目數超過 MAX_ARCHIVE_ENTRIES 時截斷並標記 truncated=True
- 列出清單時同時統計：總項目數、總壓縮大小、各副檔名的分布
  這些統計資訊比純清單更有助於 AI 快速掌握壓縮包的內容組成
- 所有解壓縮操作均在嚴格 try/except 內，單一格式失敗不影響整體流程
"""

from __future__ import annotations

import io
import logging
import os
import tarfile
import zipfile
from collections import Counter
from pathlib import Path

from core.ai.file_parser.constants import MAX_ARCHIVE_ENTRIES
from core.ai.file_parser.models import ParsedFile
from core.ai.file_parser.summary_builder import truncate

logger = logging.getLogger("bot.file_parser.archive")


# ── 主要入口 ─────────────────────────────────────────────────────────────

def parse(path: Path, filename: str, size_bytes: int) -> ParsedFile:
    """
    解析壓縮檔，回傳 Manifest 清單（不解壓）。
    由 __init__.py 的 asyncio.to_thread 排程，本函式保持同步。
    """
    ext = path.suffix.lower()
    try:
        return _dispatch(path, filename, ext, size_bytes)
    except Exception as e:
        logger.debug("[archive_parser] error file=%s: %s", filename, e)
        return ParsedFile(
            filename=filename, extension=ext,
            category="archive", size_bytes=size_bytes,
            error=str(e),
        )


# ── 格式分派 ─────────────────────────────────────────────────────────────

def _dispatch(path: Path, filename: str, ext: str, size_bytes: int) -> ParsedFile:
    # ── ZIP ──────────────────────────────────────────────────
    if ext == ".zip" or zipfile.is_zipfile(path):
        return _manifest_zip(path, filename, ext, size_bytes)

    # ── TAR（含 .tar.gz / .tar.bz2 / .tar.xz / .tgz / .tbz2 / .txz）──
    if tarfile.is_tarfile(str(path)):
        return _manifest_tar(path, filename, ext, size_bytes)

    # ── 單一壓縮檔（.gz / .bz2 / .xz）─────────────────────
    if ext in {".gz", ".bz2", ".xz"}:
        return _single_compressed(path, filename, ext, size_bytes)

    # ── 7Z ───────────────────────────────────────────────────
    if ext == ".7z":
        return _manifest_7z(path, filename, size_bytes)

    # ── RAR ──────────────────────────────────────────────────
    if ext == ".rar":
        return _manifest_rar(path, filename, size_bytes)

    return ParsedFile(
        filename=filename, extension=ext,
        category="archive", size_bytes=size_bytes,
        error=f"無法識別的壓縮格式：{ext}",
    )


# ── 共用：將項目列表轉為 Manifest 文字 ───────────────────────────────────

def _build_manifest(
    entries:    list[tuple[str, int]],   # [(name, compressed_size), ...]
    filename:   str,
    ext:        str,
    size_bytes: int,
    fmt:        str,
) -> ParsedFile:
    """
    entries：(項目名稱, 壓縮後大小) 的列表（-1 表示無法取得大小）。
    超過 MAX_ARCHIVE_ENTRIES 時截斷並標記 truncated。
    同時產生副檔名分布統計，讓 AI 能快速掌握壓縮包的組成。
    """
    total     = len(entries)
    truncated = total > MAX_ARCHIVE_ENTRIES
    shown     = entries[:MAX_ARCHIVE_ENTRIES]

    # ── 副檔名分布統計 ─────────────────────────────────────
    ext_counter: Counter[str] = Counter()
    total_size = 0
    for name, csz in entries:
        e = Path(name).suffix.lower() or "(無副檔名)"
        ext_counter[e] += 1
        if csz >= 0:
            total_size += csz

    top_exts = ", ".join(
        f"{e}×{c}" for e, c in ext_counter.most_common(10)
    )

    # ── 組裝文字 ───────────────────────────────────────────
    header = (
        f"[格式：{fmt}  共 {total} 個項目"
        + (f"  壓縮大小合計：{total_size // 1024}KB" if total_size else "")
        + f"]\n主要副檔名：{top_exts}\n"
        + (f"（僅顯示前 {MAX_ARCHIVE_ENTRIES} 項）\n" if truncated else "")
        + "\n"
    )

    lines = []
    for name, csz in shown:
        size_str = f"  ({csz // 1024}KB)" if csz >= 0 else ""
        lines.append(f"  {name}{size_str}")

    content, extra_cut = truncate(header + "\n".join(lines))
    return ParsedFile(
        filename=filename, extension=ext,
        category="archive", size_bytes=size_bytes,
        content=content, truncated=truncated or extra_cut,
    )


# ── ZIP ──────────────────────────────────────────────────────────────────

def _manifest_zip(
    path: Path, filename: str, ext: str, size_bytes: int,
) -> ParsedFile:
    with zipfile.ZipFile(path, "r") as zf:
        entries = [
            (info.filename, info.compress_size)
            for info in zf.infolist()
        ]
    return _build_manifest(entries, filename, ext, size_bytes, "ZIP")


# ── TAR（含 gz / bz2 / xz）──────────────────────────────────────────────

def _manifest_tar(
    path: Path, filename: str, ext: str, size_bytes: int,
) -> ParsedFile:
    # tarfile 自動偵測壓縮方式（r:* 模式）
    with tarfile.open(str(path), "r:*") as tf:
        entries = [
            (m.name, m.size)
            for m in tf.getmembers()
        ]
    fmt = "TAR.GZ" if ext in {".gz", ".tgz"} else \
          "TAR.BZ2" if ext in {".bz2", ".tbz2"} else \
          "TAR.XZ"  if ext in {".xz", ".txz"}  else "TAR"
    return _build_manifest(entries, filename, ext, size_bytes, fmt)


# ── 單一壓縮檔（不含 tar 封裝的 .gz / .bz2 / .xz）─────────────────────

def _single_compressed(
    path: Path, filename: str, ext: str, size_bytes: int,
) -> ParsedFile:
    """
    如 data.json.gz → 解壓後推測為 data.json。
    不解壓、不讀取內容，只提供可識別的檔名資訊。
    """
    inner_name = path.stem   # 去掉最後一個副檔名
    content = (
        f"[單一壓縮檔（{ext.upper().lstrip('.')}）]\n"
        f"解壓後推測檔名：{inner_name}\n"
        f"若需要讀取內容，請先解壓後重新上傳。"
    )
    return ParsedFile(
        filename=filename, extension=ext,
        category="archive", size_bytes=size_bytes,
        content=content,
    )


# ── 7Z ───────────────────────────────────────────────────────────────────

def _manifest_7z(path: Path, filename: str, size_bytes: int) -> ParsedFile:
    try:
        import py7zr  # type: ignore
    except ImportError:
        return ParsedFile(
            filename=filename, extension=".7z",
            category="archive", size_bytes=size_bytes,
            error="缺少 py7zr 套件，無法讀取 7Z 清單（pip install py7zr）",
        )
    with py7zr.SevenZipFile(path, mode="r") as zf:
        # getnames() 只讀取目錄項目，不解壓任何內容
        names   = zf.getnames()
        entries = [(n, -1) for n in names]   # py7zr 不直接提供壓縮後大小
    return _build_manifest(entries, filename, ".7z", size_bytes, "7Z")


# ── RAR ──────────────────────────────────────────────────────────────────

def _manifest_rar(path: Path, filename: str, size_bytes: int) -> ParsedFile:
    try:
        import rarfile  # type: ignore
    except ImportError:
        return ParsedFile(
            filename=filename, extension=".rar",
            category="archive", size_bytes=size_bytes,
            error="缺少 rarfile 套件，無法讀取 RAR 清單（pip install rarfile，並需系統安裝 libunrar）",
        )
    with rarfile.RarFile(path, "r") as rf:
        entries = [
            (info.filename, info.compress_size)
            for info in rf.infolist()
        ]
    return _build_manifest(entries, filename, ".rar", size_bytes, "RAR")
