"""依目前訊息篩選摘要與 Profile 中的非常駐資料。"""

from __future__ import annotations

import re

from core.ai.ranker import is_relevant

_HEADING_RE = re.compile(r"(?m)(?=^(?:\*\*[^*\n]+：?\*\*|#{1,6}\s+[^\n]+)\s*$)")


def select_summary(summary: str, query: str) -> str:
    """只保留與當前問題有關的摘要區塊。"""
    if not summary.strip() or not query.strip():
        return ""

    chunks = [chunk.strip() for chunk in _HEADING_RE.split(summary) if chunk.strip()]
    selected = [chunk for chunk in chunks if is_relevant(query, chunk)]
    return "\n\n".join(selected)


def select_profile(profile_section: str, query: str) -> str:
    """風格／語言常駐；話題與備註需與當前訊息相關。"""
    if not profile_section.strip():
        return ""

    kept: list[str] = []
    for line in profile_section.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("==="):
            continue
        if stripped.startswith(("- 溝通風格：", "- 語言偏好：")):
            kept.append(stripped)
        elif stripped.startswith("- 常見話題："):
            raw_topics = stripped.split("：", 1)[1]
            topics = [topic.strip() for topic in re.split(r"[,，]", raw_topics)]
            related = [topic for topic in topics if is_relevant(query, topic)]
            if related:
                kept.append(f"- 已確認相關話題：{', '.join(related)}")
        elif stripped.startswith("- 備註：") and is_relevant(query, stripped):
            kept.append(stripped)

    return "=== 使用者偏好參考 ===\n" + "\n".join(kept) if kept else ""
