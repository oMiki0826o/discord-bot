"""
core/ai/file_parser/video_parser.py

修正（第三階段預留）：
- 分析影片 Metadata：時長、解析度、幀率、影片/音訊編碼、檔案大小
- 抽幀（ffmpeg / opencv）：第三階段，有需求再評估
- 音訊分離後 STT：整合 audio_parser + Whisper，第三階段
- 時長限制：MAX_VIDEO_DURATION，防止長影片阻塞

啟用方式（未來）：
1. constants.py 新增 VIDEO_EXTENSIONS 與 MAX_VIDEO_DURATION 常數
2. registry.py 的 REGISTRY 加入：
       **{ext: video_parser.parse for ext in VIDEO_EXTENSIONS},
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from core.ai.file_parser.models import ParsedFile

logger = logging.getLogger("bot.file_parser.video")

# ── 時長上限（秒）— 未來移至 constants.py ────────────────────────────────
_MAX_VIDEO_DURATION = 120   # 2 分鐘

_VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    ".mp4", ".mov", ".webm", ".mkv", ".avi", ".flv",
})


def parse(path: Path, filename: str, size_bytes: int) -> ParsedFile:
    """解析影片 Metadata，抽幀與 STT 於第三階段加入。"""
    ext = path.suffix.lower()
    try:
        return _parse_video(path, filename, ext, size_bytes)
    except Exception as e:
        logger.debug("[video_parser] error file=%s: %s", filename, e)
        return ParsedFile(
            filename=filename, extension=ext,
            category="video", size_bytes=size_bytes,
            error=str(e),
        )


def _parse_video(
    path: Path, filename: str, ext: str, size_bytes: int,
) -> ParsedFile:
    """
    使用 ffprobe（ffmpeg 內建工具）提取 Metadata。
    ffprobe 是 CLI 工具，通常隨 ffmpeg 一起安裝（apt install ffmpeg）。
    不需要 Python 套件，但需要系統路徑中存在 ffprobe。
    """
    # ── ffprobe 可用性確認 ────────────────────────────────
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams", "-show_format",
                str(path),
            ],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        return ParsedFile(
            filename=filename, extension=ext,
            category="video", size_bytes=size_bytes,
            error="系統未安裝 ffprobe（apt install ffmpeg），無法提取影片 Metadata",
        )
    except subprocess.TimeoutExpired:
        return ParsedFile(
            filename=filename, extension=ext,
            category="video", size_bytes=size_bytes,
            error="ffprobe 逾時，影片可能損壞",
        )

    if result.returncode != 0:
        return ParsedFile(
            filename=filename, extension=ext,
            category="video", size_bytes=size_bytes,
            error=f"ffprobe 失敗：{result.stderr[:200]}",
        )

    # ── 解析 ffprobe JSON 輸出 ─────────────────────────────
    try:
        info   = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return ParsedFile(
            filename=filename, extension=ext,
            category="video", size_bytes=size_bytes,
            error=f"無法解析 ffprobe 輸出：{e}",
        )

    fmt      = info.get("format", {})
    streams  = info.get("streams", [])
    duration = float(fmt.get("duration", 0))
    mins, secs = divmod(int(duration), 60)

    lines = [
        f"格式：{ext.lstrip('.').upper()}",
        f"時長：{mins}:{secs:02d}",
        f"檔案大小：{size_bytes // 1024} KB",
    ]

    # ── 分流各媒體串流資訊 ─────────────────────────────────
    for s in streams:
        codec_type = s.get("codec_type", "")
        codec_name = s.get("codec_name", "未知")
        if codec_type == "video":
            w, h = s.get("width", 0), s.get("height", 0)
            fps  = s.get("r_frame_rate", "")
            lines.append(f"影片串流：{codec_name}  {w}×{h}  {fps} fps")
        elif codec_type == "audio":
            sr  = s.get("sample_rate", "")
            ch  = s.get("channels", "")
            lines.append(f"音訊串流：{codec_name}  {sr} Hz  {ch}ch")

    if duration > _MAX_VIDEO_DURATION:
        logger.info(
            "[video_parser] 時長 %.0f 秒超過上限 %d 秒，抽幀時將截斷",
            duration, _MAX_VIDEO_DURATION,
        )
        lines.append(f"[時長超過 {_MAX_VIDEO_DURATION} 秒，第三階段 STT/抽幀時將截斷]")

    # ── TODO（第三階段）：抽幀 + STT ──────────────────────
    # if duration <= _MAX_VIDEO_DURATION:
    #     frames = _extract_frames(path, count=3)
    #     transcript = _extract_audio_and_stt(path)

    return ParsedFile(
        filename=filename, extension=ext,
        category="video", size_bytes=size_bytes,
        content="\n".join(lines),
    )
