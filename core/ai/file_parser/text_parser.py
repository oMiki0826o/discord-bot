"""
core/ai/file_parser/text_parser.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

修正（純文字解析）：
- 涵蓋 txt / md / log / csv / tsv / json / jsonl / yaml / toml / ini / env
- 編碼偵測：優先 UTF-8，失敗後嘗試 chardet，再退 latin-1 保底
- CSV / JSONL 大型檔案限制載入行數，避免 OOM
- 所有解析皆有 try/except，失敗回傳 error 欄位
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.ai.file_parser.models import ParsedFile
from core.ai.file_parser.summary_builder import truncate
from core.ai.file_parser.encoding import decode_bytes

logger = logging.getLogger("bot.file_parser.text")

# ── 大型 CSV / JSONL 最大列數 ──────────────────────
_MAX_CSV_ROWS  = 500
_MAX_JSONL_OBJ = 50


# ── 主要入口 ──────────────────────

def parse(path: Path, filename: str, size_bytes: int) -> ParsedFile:
    """解析純文字類檔案，回傳 ParsedFile。"""
    ext = path.suffix.lower()
    try:
        if ext in {".csv", ".tsv"}:
            return _parse_csv(path, filename, ext, size_bytes)
        if ext in {".json"}:
            return _parse_json(path, filename, size_bytes)
        if ext == ".jsonl":
            return _parse_jsonl(path, filename, size_bytes)
        return _parse_plain(path, filename, ext, size_bytes)
    except Exception as e:
        logger.debug("[text_parser] error file=%s: %s", filename, e)
        return ParsedFile(
            filename=filename, extension=ext,
            category="text", size_bytes=size_bytes,
            error=str(e),
        )


# ── 一般純文字 ──────────────────────

def _parse_plain(
    path: Path, filename: str, ext: str, size_bytes: int,
) -> ParsedFile:
    raw     = path.read_bytes()
    text    = decode_bytes(raw)
    content, truncated = truncate(text)
    return ParsedFile(
        filename=filename, extension=ext,
        category="text", size_bytes=size_bytes,
        content=content, truncated=truncated,
    )


# ── CSV / TSV ──────────────────────

def _parse_csv(
    path: Path, filename: str, ext: str, size_bytes: int,
) -> ParsedFile:
    import csv
    sep = "\t" if ext == ".tsv" else ","
    raw = path.read_bytes()
    text = decode_bytes(raw)

    lines   = text.splitlines()
    header  = lines[0] if lines else ""
    total   = len(lines) - 1   # 扣掉標題行

    # ── 限制列數，避免 OOM ──────────────────────
    sample_lines = lines[: _MAX_CSV_ROWS + 1]   # 含標題
    truncated    = total > _MAX_CSV_ROWS

    try:
        import io
        reader = csv.reader(io.StringIO("\n".join(sample_lines)), delimiter=sep)
        rows   = list(reader)
        content = f"欄位：{', '.join(rows[0]) if rows else '(空)'}\n"
        content += f"共 {total} 列（顯示前 {min(total, _MAX_CSV_ROWS)} 列）\n\n"
        content += "\n".join(sep.join(r) for r in rows[1:])
    except Exception:
        content   = "\n".join(sample_lines)
        truncated = total > _MAX_CSV_ROWS

    content, extra_cut = truncate(content)
    return ParsedFile(
        filename=filename, extension=ext,
        category="text", size_bytes=size_bytes,
        content=content, truncated=truncated or extra_cut,
    )


# ── JSON ──────────────────────

def _parse_json(
    path: Path, filename: str, size_bytes: int,
) -> ParsedFile:
    raw  = path.read_bytes()
    text = decode_bytes(raw)
    try:
        obj     = json.loads(text)
        # 提供結構概覽，而非完整 dump
        summary = _json_summary(obj)
        content, truncated = truncate(summary)
    except json.JSONDecodeError:
        # 不合法 JSON → 當純文字處理
        content, truncated = truncate(text)
    return ParsedFile(
        filename=filename, extension=".json",
        category="text", size_bytes=size_bytes,
        content=content, truncated=truncated,
    )


def _json_summary(obj: object, depth: int = 0, max_depth: int = 3) -> str:
    """遞迴產生 JSON 結構摘要，控制深度避免過長。"""
    indent = "  " * depth
    if depth >= max_depth:
        return f"{indent}..."
    if isinstance(obj, dict):
        lines = [f"{indent}{{"]
        for i, (k, v) in enumerate(obj.items()):
            if i >= 20:
                lines.append(f"{indent}  ... (共 {len(obj)} 個鍵)")
                break
            child = _json_summary(v, depth + 1, max_depth)
            lines.append(f"{indent}  {k!r}: {child.strip()}")
        lines.append(f"{indent}}}")
        return "\n".join(lines)
    if isinstance(obj, list):
        return (
            f"[陣列，共 {len(obj)} 項"
            + (f"，第一項：{_json_summary(obj[0], depth+1, max_depth).strip()}" if obj else "")
            + "]"
        )
    return repr(obj)


# ── JSONL ──────────────────────

def _parse_jsonl(
    path: Path, filename: str, size_bytes: int,
) -> ParsedFile:
    raw   = path.read_bytes()
    text  = decode_bytes(raw)
    lines = [l for l in text.splitlines() if l.strip()]
    total = len(lines)

    sample    = lines[:_MAX_JSONL_OBJ]
    truncated = total > _MAX_JSONL_OBJ

    parts: list[str] = [f"JSONL 共 {total} 筆（顯示前 {len(sample)} 筆）\n"]
    for i, line in enumerate(sample):
        try:
            obj = json.loads(line)
            parts.append(f"[{i}] {json.dumps(obj, ensure_ascii=False, separators=(',',':'))[:200]}")
        except json.JSONDecodeError:
            parts.append(f"[{i}] {line[:200]}")

    content, extra_cut = truncate("\n".join(parts))
    return ParsedFile(
        filename=filename, extension=".jsonl",
        category="text", size_bytes=size_bytes,
        content=content, truncated=truncated or extra_cut,
    )
