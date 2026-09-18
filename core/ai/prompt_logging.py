"""將 AI 實際使用的 prompt 傳送到指定 Discord 紀錄頻道。"""

from __future__ import annotations

import io
import logging
import re

import discord

from core.system.settings import get_int

logger = logging.getLogger("bot.ai.prompt_logging")

DEFAULT_PROMPT_LOG_CHANNEL_ID = 1550078091949506622
_INLINE_LIMIT = 1_500
_bot: discord.Client | None = None

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*(?:bearer\s+)?)[^\s]+"),
    re.compile(r"(?i)((?:api[_ -]?key|bot[_ -]?token|session[_ -]?token|password)\s*[:=]\s*)[^\s]+"),
    re.compile(r"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----).*?(-----END [A-Z ]*PRIVATE KEY-----)", re.DOTALL),
)


def set_prompt_log_client(bot: discord.Client) -> None:
    """註冊 Discord client；重載 Cog 時可安全覆寫為同一個 bot。"""
    global _bot
    _bot = bot


def _render_prompt(system_prompt: str, final_prompt: str) -> str:
    return (
        "===== SYSTEM PROMPT =====\n"
        f"{system_prompt}\n\n"
        "===== FINAL PROMPT =====\n"
        f"{final_prompt}"
    )


def redact_prompt(text: str) -> str:
    """遮罩常見憑證，避免診斷開關把秘密複製進 log。"""
    redacted = text
    redacted = re.sub(
        r"<attachment_content>.*?</attachment_content>",
        "<attachment_content>[附件原文未記錄]</attachment_content>",
        redacted,
        flags=re.DOTALL,
    )
    redacted = re.sub(
        r"<channel_context>.*?</channel_context>",
        "<channel_context>[頻道訊息原文未記錄]</channel_context>",
        redacted,
        flags=re.DOTALL,
    )
    redacted = re.sub(
        r"(=== 使用者長期記憶參考 ===).*?(?=\n\n===|\Z)",
        r"\1\n[完整私人記憶未記錄]",
        redacted,
        flags=re.DOTALL,
    )
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r"\1[已遮罩]", redacted)
    return redacted


async def send_prompt_to_discord(
    *,
    user_id: str,
    username: str,
    source_channel_id: str,
    model: str,
    system_prompt: str,
    final_prompt: str,
) -> None:
    """傳送 prompt；短內容直接顯示，長內容改用附件避免 Discord 截斷。"""
    if _bot is None:
        raise RuntimeError("尚未註冊 Discord client")

    channel_id = get_int(
        "ai.prompt_log_channel_id",
        DEFAULT_PROMPT_LOG_CHANNEL_ID,
    )
    channel = _bot.get_channel(channel_id)
    if channel is None:
        channel = await _bot.fetch_channel(channel_id)

    prompt_text = redact_prompt(_render_prompt(system_prompt, final_prompt))
    header = (
        f"AI Prompt｜使用者：{username} (`{user_id}`)｜"
        f"來源頻道：`{source_channel_id or '未知'}`｜模型：`{model}`"
    )
    allowed_mentions = discord.AllowedMentions.none()

    if len(prompt_text) <= _INLINE_LIMIT:
        # 防止 prompt 內的 code fence 提前關閉區塊。
        safe_text = prompt_text.replace("```", "``\u200b`")
        await channel.send(
            f"{header}\n```text\n{safe_text}\n```",
            allowed_mentions=allowed_mentions,
        )
        return

    file = discord.File(
        io.BytesIO(prompt_text.encode("utf-8")),
        filename=f"prompt-{user_id}.txt",
    )
    await channel.send(
        f"{header}\nPrompt 較長，完整內容請見附件。",
        file=file,
        allowed_mentions=allowed_mentions,
    )


__all__ = [
    "DEFAULT_PROMPT_LOG_CHANNEL_ID",
    "redact_prompt",
    "send_prompt_to_discord",
    "set_prompt_log_client",
]
