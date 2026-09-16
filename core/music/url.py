"""YouTube URL validation shared by music commands and song extraction."""

from __future__ import annotations

import re
from urllib.parse import urlsplit


_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def is_youtube_url(text: str) -> bool:
    """Return whether *text* is a public YouTube or YouTube Music HTTP URL."""
    cleaned = text.strip()
    if not _HTTP_URL_RE.match(cleaned):
        return False

    try:
        hostname = (urlsplit(cleaned).hostname or "").lower().rstrip(".")
    except ValueError:
        return False

    return (
        hostname == "youtu.be"
        or hostname == "youtube.com"
        or hostname.endswith(".youtube.com")
    )
