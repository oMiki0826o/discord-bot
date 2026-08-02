"""
cogs/utility/markdown_convert.py

Modification():

- 新增本檔案：/markdown 指令，讓使用者上傳文件後直接轉換成 .md 檔案
  回傳。與 core/ai/file_parser/document_parser.py 用途不同：
  document_parser 是把文件內容塞進 AI 對話的 prompt，因此固定截斷到
  ai.max_reply_length 等長度上限以控制 token 消耗；這裡的目的是讓
  使用者拿到一份「完整」的轉換結果可以下載，因此直接呼叫
  markitdown，不經過會截斷內容的 file_parser 流程。
- 支援格式不寫死成一份手動維護的副檔名清單：直接把檔案交給
  MarkItDown().convert()，能不能轉換由 markitdown 本身依實際安裝的
  extra（見 requirements.txt 的 markitdown[pdf,docx,pptx,xlsx,xls]）
  決定。日後如果在 requirements.txt 加裝更多 markitdown extra
  （例如 audio-transcription），這個指令會自動獲得對應的轉換能力，
  不需要回來修改這裡的程式碼。
- 檔案大小上限重用 core.ai.file_parser.constants.MAX_FILE_SIZE
  （與 AI 附件解析共用同一個上限來源，不另外設一個獨立的硬編碼數字）。
- MarkItDown().convert() 是同步、CPU 密集的呼叫，透過 asyncio.to_thread
  排程，避免阻塞事件迴圈（與 file_parser 系列 parser 的慣例一致）。

職責：
- 接收使用者上傳的檔案，另存為暫存檔後交給 markitdown 轉換。
- 轉換成功則包成 .md 檔案回傳；內容為空或轉換失敗則回覆文字說明
  原因，不硬塞一份空檔案給使用者。
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from core.ai.file_parser.constants import MAX_FILE_SIZE

logger = logging.getLogger("bot.utility.markdown_convert")


def _convert_to_markdown(path: Path) -> str:
    """同步轉換（供 asyncio.to_thread 呼叫）：回傳完整 Markdown 文字。"""
    from markitdown import MarkItDown

    result = MarkItDown().convert(str(path))
    return (result.text_content or "").strip()


# ── Cog ──────────────────────

class MarkdownConvert(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="markdown",
        description="上傳文件（pdf / docx / xlsx / pptx 等），轉換成 Markdown (.md) 檔案回傳",
    )
    @app_commands.describe(file="要轉換的文件")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def markdown_command(
        self,
        interaction: discord.Interaction,
        file:        discord.Attachment,
    ) -> None:
        if file.size > MAX_FILE_SIZE:
            await interaction.response.send_message(
                f"檔案過大（{file.size / 1024 / 1024:.1f} MB），"
                f"上限為 {MAX_FILE_SIZE / 1024 / 1024:.0f} MB。",
                ephemeral=True,
            )
            return

        # ── 轉換可能需要幾秒鐘，先 defer 避免超過 3 秒互動時限 ──────────────────────
        await interaction.response.defer(thinking=True)

        suffix   = Path(file.filename).suffix
        tmp_fd, tmp_path_str = tempfile.mkstemp(suffix=suffix)
        tmp_path = Path(tmp_path_str)

        try:
            os.close(tmp_fd)
            await file.save(tmp_path)
            markdown_text = await asyncio.to_thread(_convert_to_markdown, tmp_path)

        except ImportError:
            logger.error("[markdown_command] 缺少 markitdown 套件")
            await interaction.followup.send("伺服器缺少 markitdown 套件，暫時無法轉換文件。")
            return

        except Exception as e:
            logger.warning(
                "[markdown_command] 轉換失敗 filename=%s error=%s", file.filename, e,
            )
            await interaction.followup.send(f"轉換失敗：{file.filename} 可能是不支援的格式或檔案已損毀。")
            return

        finally:
            tmp_path.unlink(missing_ok=True)

        if not markdown_text:
            await interaction.followup.send(
                f"{file.filename} 沒有可提取的文字內容（例如純掃描圖片、無文字層的 PDF）。"
            )
            return

        output_name = f"{Path(file.filename).stem}.md"
        buf = io.BytesIO(markdown_text.encode("utf-8"))
        await interaction.followup.send(
            content = f"已將 {file.filename} 轉換為 Markdown。",
            file    = discord.File(buf, filename=output_name),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MarkdownConvert(bot))
