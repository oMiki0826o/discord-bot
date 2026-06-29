"""
core/ai/file_parser/image_parser.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

修正（第二階段預留，目前圖片走多模態路線）：
- 分析圖片 Metadata：尺寸、格式、色彩模式、DPI、EXIF 資訊
- 目前圖片由 cogs/ai/chat.py 直接讀取 bytes 組成 Gemini types.Part，
  本模組暫不在 registry.py 中啟用，等第二階段需要 OCR 功能時再接入
- OCR 文字提取：需 tesseract + pytesseract（系統依賴較重），第二階段實作
- Vision 語意理解：走 Gemini multimodal Part，不在此模組職責範圍

啟用方式（未來）：
1. 在 constants.py 確認 IMAGE_EXTENSIONS 已定義（已完成）
2. 在 registry.py 的 REGISTRY 加入：
       **{ext: image_parser.parse for ext in IMAGE_EXTENSIONS},
3. 在 cogs/ai/chat.py 的分流邏輯調整判斷順序
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.ai.file_parser.models import ParsedFile

logger = logging.getLogger("bot.file_parser.image")

# ── 未來需要的依賴（目前未啟用） ──────────────────────
# pip install Pillow pytesseract
# apt-get install tesseract-ocr tesseract-ocr-chi-tra  # 繁中 OCR 語料包


def parse(path: Path, filename: str, size_bytes: int) -> ParsedFile:
    """
    解析圖片 Metadata，回傳 ParsedFile。
    OCR 功能在第二階段實作後於此加入，目前只提取 Metadata。
    """
    ext = path.suffix.lower()
    try:
        return _parse_image(path, filename, ext, size_bytes)
    except Exception as e:
        logger.debug("[image_parser] error file=%s: %s", filename, e)
        return ParsedFile(
            filename=filename, extension=ext,
            category="image", size_bytes=size_bytes,
            error=str(e),
        )


def _parse_image(
    path: Path, filename: str, ext: str, size_bytes: int,
) -> ParsedFile:
    """提取圖片基本 Metadata。需要 Pillow：pip install Pillow"""
    try:
        from PIL import Image, ExifTags
    except ImportError:
        return ParsedFile(
            filename=filename, extension=ext,
            category="image", size_bytes=size_bytes,
            error="缺少 Pillow 套件，無法提取圖片 Metadata（pip install Pillow）",
        )

    with Image.open(path) as img:
        width, height = img.size
        mode   = img.mode
        fmt    = img.format or ext.lstrip(".").upper()

        # ── EXIF 提取（JPEG / TIFF 常見，PNG 無） ──────────────────────
        exif_lines: list[str] = []
        try:
            raw_exif = img._getexif()  # type: ignore[attr-defined]
            if raw_exif:
                for tag_id, val in raw_exif.items():
                    tag = ExifTags.TAGS.get(tag_id, str(tag_id))
                    # 只保留可讀文字，跳過大型 bytes 資料
                    if isinstance(val, (str, int, float)):
                        exif_lines.append(f"  {tag}: {val}")
                    if len(exif_lines) >= 10:
                        break
        except (AttributeError, Exception):
            pass   # 沒有 EXIF 或格式不支援，靜默跳過

    lines = [
        f"格式：{fmt}",
        f"尺寸：{width} × {height} px",
        f"色彩模式：{mode}",
        f"檔案大小：{size_bytes // 1024}KB",
    ]
    if exif_lines:
        lines.append("EXIF 資訊：")
        lines.extend(exif_lines)

    # ── TODO（第二階段）：OCR ──────────────────────
    # try:
    #     import pytesseract
    #     ocr_text = pytesseract.image_to_string(img, lang="chi_tra+eng")
    #     if ocr_text.strip():
    #         lines.append(f"\n=== OCR 結果 ===\n{ocr_text[:2000]}")
    # except Exception:
    #     pass

    return ParsedFile(
        filename=filename, extension=ext,
        category="image", size_bytes=size_bytes,
        content="\n".join(lines),
    )
