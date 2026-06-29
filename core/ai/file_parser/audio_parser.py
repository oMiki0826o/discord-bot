"""
core/ai/file_parser/audio_parser.py

修正（第三階段預留）：
- 分析音訊 Metadata：時長、取樣率、聲道數、位元率、格式標籤（標題/藝人/專輯）
- STT 語音轉文字（openai-whisper / faster-whisper）：第三階段，有需求再評估
- 時長限制：MAX_AUDIO_DURATION，防止長錄音阻塞 event loop
- CPU 密集（STT），需排程至執行緒池（__init__.py 已統一處理）

啟用方式（未來）：
1. constants.py 新增 AUDIO_EXTENSIONS 與 MAX_AUDIO_DURATION 常數
2. registry.py 的 REGISTRY 加入：
       **{ext: audio_parser.parse for ext in AUDIO_EXTENSIONS},
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.ai.file_parser.models import ParsedFile

logger = logging.getLogger("bot.file_parser.audio")

# ── 未來需要的依賴（目前未啟用）────────────────────────────────────────
# pip install mutagen
# pip install openai-whisper   # STT（第三階段）

# 音訊時長上限（秒）— 未來移至 constants.py
_MAX_AUDIO_DURATION = 300   # 5 分鐘

_AUDIO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac",
})


def parse(path: Path, filename: str, size_bytes: int) -> ParsedFile:
    """解析音訊 Metadata，STT 功能於第三階段加入。"""
    ext = path.suffix.lower()
    try:
        return _parse_audio(path, filename, ext, size_bytes)
    except Exception as e:
        logger.debug("[audio_parser] error file=%s: %s", filename, e)
        return ParsedFile(
            filename=filename, extension=ext,
            category="audio", size_bytes=size_bytes,
            error=str(e),
        )


def _parse_audio(
    path: Path, filename: str, ext: str, size_bytes: int,
) -> ParsedFile:
    """提取音訊基本 Metadata。需要 mutagen：pip install mutagen"""
    try:
        from mutagen import File as MutagenFile  # type: ignore
    except ImportError:
        return ParsedFile(
            filename=filename, extension=ext,
            category="audio", size_bytes=size_bytes,
            error="缺少 mutagen 套件，無法提取音訊 Metadata（pip install mutagen）",
        )

    audio = MutagenFile(str(path), easy=True)
    if audio is None:
        return ParsedFile(
            filename=filename, extension=ext,
            category="audio", size_bytes=size_bytes,
            error="無法辨識音訊格式",
        )

    duration   = getattr(audio.info, "length", None)
    samplerate = getattr(audio.info, "sample_rate", None)
    channels   = getattr(audio.info, "channels", None)
    bitrate    = getattr(audio.info, "bitrate", None)

    # ── 時長限制警告（STT 啟用後才有實際效果）──────────────
    duration_str = ""
    if duration is not None:
        mins, secs = divmod(int(duration), 60)
        duration_str = f"{mins}:{secs:02d}"
        if duration > _MAX_AUDIO_DURATION:
            logger.info(
                "[audio_parser] 時長 %.0f 秒超過上限 %d 秒，STT 時將截斷",
                duration, _MAX_AUDIO_DURATION,
            )

    # ── 標籤資訊（mutagen easy tag）─────────────────────────
    tag_lines: list[str] = []
    for key in ("title", "artist", "album", "date", "genre"):
        vals = audio.get(key)
        if vals:
            tag_lines.append(f"  {key.capitalize()}: {vals[0]}")

    lines = [f"格式：{ext.lstrip('.').upper()}"]
    if duration_str:
        lines.append(f"時長：{duration_str}")
    if samplerate:
        lines.append(f"取樣率：{samplerate} Hz")
    if channels:
        lines.append(f"聲道：{channels}")
    if bitrate:
        lines.append(f"位元率：{bitrate // 1000} kbps")
    lines.append(f"檔案大小：{size_bytes // 1024} KB")
    if tag_lines:
        lines.append("標籤資訊：")
        lines.extend(tag_lines)

    # ── TODO（第三階段）：STT ──────────────────────────────
    # if duration and duration <= _MAX_AUDIO_DURATION:
    #     import whisper
    #     model = whisper.load_model("small")
    #     result = model.transcribe(str(path), language="zh")
    #     lines.append(f"\n=== 語音轉文字 ===\n{result['text'][:3000]}")

    return ParsedFile(
        filename=filename, extension=ext,
        category="audio", size_bytes=size_bytes,
        content="\n".join(lines),
    )
