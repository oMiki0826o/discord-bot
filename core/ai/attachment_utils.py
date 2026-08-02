"""
core/ai/attachment_utils.py

Modification():

- 新增本檔案：從 cogs/ai/chat.py 抽出附件處理邏輯（process_attachments /
  read_image_part / parse_attachment_file）。原本這三個函式是
  Chat Cog 的實例方法，但內容完全不使用 self（不依賴 Cog 的任何
  狀態），純粹是「輸入一批 discord.Attachment，輸出解析結果」的
  無狀態工具函式。新增 /ai 這個 slash 指令後，若繼續放在 Chat 內，
  新指令要嘛得重複實作一份幾乎一樣的程式碼，要嘛得跨 Cog 呼叫
  `bot.get_cog("Chat")` 再呼叫其方法（多一層不必要的耦合）。抽成
  獨立模組後，mention 對話（cogs/ai/chat.py）與 /ai（cogs/ai/
  ai_command.py）都直接 import 使用，沒有重複程式碼，日後若再新增
  第三種呼叫 AI 的入口，也不需要再煩惱這份邏輯要放哪裡。

職責：
- 將一批 Discord 附件分流為「file_parser 解析結果」與「Gemini 圖片
  多模態 Part」兩類，供 core.ai.core.generate() 使用。
- 單一附件處理失敗只記錄 log，不影響其他附件或整體對話流程。
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import discord
from google.genai import types

from core.ai.file_parser import parse as parse_file
from core.ai.file_parser.constants import IMAGE_EXTENSIONS, MAX_IMAGE_SIZE
from core.ai.file_parser.models import ParsedFile
from core.system.settings import get_int

logger = logging.getLogger("bot.ai.attachment_utils")

# ── 圖片 MIME 對照 ──────────────────────

_IMAGE_MIME: dict[str, str] = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif":  "image/gif",
}


# ── 附件分流 ──────────────────────

async def process_attachments(
    attachments: list[discord.Attachment],
) -> tuple[list[ParsedFile], list[types.Part]]:
    """
    將附件分流為「file_parser 解析結果」與「圖片多模態 Part」。
    單一附件失敗只記錄 log，不影響其他附件或整體對話流程。
    """
    files:       list[ParsedFile] = []
    image_parts: list[types.Part] = []
    max_attachments = max(0, get_int("ai.max_attachments", 5))

    for attachment in attachments[:max_attachments]:
        ext = Path(attachment.filename).suffix.lower()
        try:
            if ext in IMAGE_EXTENSIONS:
                part = await read_image_part(attachment, ext)
                if part is not None:
                    image_parts.append(part)
            else:
                parsed = await parse_attachment_file(attachment)
                if parsed is not None:
                    files.append(parsed)
        except Exception as e:
            logger.warning(
                "[process_attachments] 附件處理失敗 filename=%s: %s",
                attachment.filename, e,
            )

    if len(attachments) > max_attachments:
        logger.info(
            "[process_attachments] 附件數量 %d 超過上限 %d，僅處理前 %d 個",
            len(attachments), max_attachments, max_attachments,
        )

    return files, image_parts


async def read_image_part(
    attachment: discord.Attachment,
    ext:        str,
) -> types.Part | None:
    """讀取圖片 bytes 並組成 Gemini multimodal Part。"""
    if attachment.size > MAX_IMAGE_SIZE:
        logger.info(
            "[read_image_part] 圖片過大 filename=%s size=%d limit=%d",
            attachment.filename, attachment.size, MAX_IMAGE_SIZE,
        )
        return None

    data      = await attachment.read()
    mime_type = attachment.content_type or _IMAGE_MIME.get(ext, "image/png")
    return types.Part.from_bytes(data=data, mime_type=mime_type)


async def parse_attachment_file(
    attachment: discord.Attachment,
) -> ParsedFile | None:
    """寫入暫存檔後交由 file_parser 解析，確保暫存檔最終會被刪除。"""
    suffix  = Path(attachment.filename).suffix
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=suffix)
    tmp_path = Path(tmp_path_str)

    try:
        os.close(tmp_fd)   # 只需要路徑，立即關閉 fd 改用 attachment.save 寫入
        await attachment.save(tmp_path)
        return await parse_file(tmp_path, filename=attachment.filename)
    finally:
        tmp_path.unlink(missing_ok=True)
