"""將 Owner 查詢到的資料庫資料轉為可讀 Markdown。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_TAIPEI = ZoneInfo("Asia/Taipei")
_MAX_CELL_LENGTH = 4_000


def _time(value: Any) -> str:
    if value in (None, ""):
        return "無"
    try:
        return datetime.fromtimestamp(float(value), tz=_TAIPEI).strftime("%Y-%m-%d %H:%M:%S %Z")
    except (TypeError, ValueError, OSError):
        return str(value)


def _heading(value: Any) -> str:
    return str(value or "未命名").replace("\n", " ").replace("\r", " ").strip()


def _quote(value: Any) -> str:
    text = str(value or "（空白）").replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > _MAX_CELL_LENGTH:
        text = text[:_MAX_CELL_LENGTH] + "\n…（內容過長，已截斷）"
    return "\n".join(f"> {line}" if line else ">" for line in text.split("\n"))


def _value(value: Any, column: str = "") -> str:
    if column == "embedding":
        try:
            decoded = json.loads(value)
            dimensions = len(decoded) if isinstance(decoded, list) else 0
        except (TypeError, json.JSONDecodeError):
            dimensions = 0
        return f"<向量資料，{dimensions} 維>"
    if column.endswith("_at") or column in {"expires_at", "created_at", "updated_at"}:
        return _time(value)
    if isinstance(value, bytes):
        return f"<二進位資料，{len(value)} bytes>"
    text = str(value if value is not None else "NULL")
    return text if len(text) <= _MAX_CELL_LENGTH else text[:_MAX_CELL_LENGTH] + "…（已截斷）"


def render_user_memory(snapshot: dict, display_name: str = "") -> str:
    """產生單一使用者的完整、可讀記憶報告。"""
    user_id = snapshot["user_id"]
    name = display_name or snapshot.get("username") or user_id
    messages = snapshot.get("messages", [])
    memories = snapshot.get("memories", [])
    vectors = snapshot.get("vector_memories", [])
    channel_summaries = snapshot.get("channel_summaries", [])

    lines = [
        f"# {_heading(name)} 的 AI 記憶",
        "",
        f"- 使用者 ID：`{user_id}`",
        f"- 資料庫名稱：{snapshot.get('username') or '無'}",
        f"- 匯出時間：{datetime.now(_TAIPEI).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- 對話：{len(messages)} 筆",
        f"- 一般記憶：{len(memories)} 筆",
        f"- 向量記憶：{len(vectors)} 筆",
        f"- 頻道摘要：{len(channel_summaries)} 筆",
        "",
        "## 使用者資料",
        "",
    ]

    profile = snapshot.get("profile") or {}
    if profile:
        lines.extend(["```json", json.dumps(profile, ensure_ascii=False, indent=2), "```"])
    else:
        lines.append("（無資料）")

    lines.extend(["", "## 使用者摘要", ""])
    summary = snapshot.get("summary")
    if summary:
        lines.extend([
            f"- 涵蓋訊息：{summary['msg_count']} 筆",
            f"- 更新時間：{_time(summary['updated_at'])}",
            "",
            _quote(summary["summary"]),
        ])
    else:
        lines.append("（無摘要）")

    lines.extend(["", "## 頻道摘要", ""])
    if channel_summaries:
        for item in channel_summaries:
            lines.extend([
                f"### 頻道 `{item['channel_id'] or '未記錄'}`",
                "",
                f"- 涵蓋訊息：{item['msg_count']} 筆",
                f"- 更新時間：{_time(item['updated_at'])}",
                "",
                _quote(item["summary"]),
                "",
            ])
    else:
        lines.append("（無頻道摘要）")

    lines.extend(["", "## 一般記憶", ""])
    if memories:
        for item in memories:
            lines.extend([
                f"### {_heading(item['keyword'])}",
                "",
                f"- 記憶 ID：`{item.get('id', '無')}`",
                f"- 記憶範圍：`{item.get('scope_type', 'channel')}`",
                (
                    "- 範圍對象：此使用者（可跨頻道）"
                    if item.get("scope_type") == "user"
                    else f"- 範圍對象：頻道 `{item.get('channel_id') or '舊版／未記錄'}`"
                ),
                f"- 類型：{item.get('category', 'general')}",
                f"- 主題：{item.get('subject') or '未標記'}",
                f"- 狀態：{item.get('status', 'active')}",
                f"- 可信度：{item.get('confidence', 0.7)}",
                f"- 重要度：{item['importance']}",
                f"- 來源訊息：{item.get('source_message_id') or '無'}",
                f"- 更新時間：{_time(item.get('updated_at') or item['created_at'])}",
                "",
                _quote(item["content"]),
                "",
            ])
    else:
        lines.append("（無一般記憶）")

    lines.extend(["", "## 向量記憶", ""])
    if vectors:
        lines.append("為了可讀性，本報告不輸出 embedding 浮點數組。")
        lines.append("")
        for item in vectors:
            lines.extend([
                f"### {_heading(item['keyword'])}",
                "",
                f"- 重要度：{item['importance']}",
                f"- 向量維度：{item['embedding_dimensions']}",
                f"- 更新時間：{_time(item['created_at'])}",
                "",
                _quote(item["content"]),
                "",
            ])
    else:
        lines.append("（無向量記憶）")

    lines.extend(["", "## 共通記憶", ""])
    global_memories = snapshot.get("global_memories", [])
    if global_memories:
        for item in global_memories:
            lines.extend([
                f"### {_heading(item['keyword'])}",
                "",
                f"- 重要度：{item['importance']}",
                f"- 更新時間：{_time(item['updated_at'])}",
                "",
                _quote(item["content"]),
                "",
            ])
    else:
        lines.append("（無共通記憶）")

    lines.extend(["", "## 對話紀錄", ""])
    if messages:
        for index, item in enumerate(messages, start=1):
            role = {"user": "使用者", "assistant": "AI"}.get(item["role"], item["role"])
            lines.extend([
                f"### {index}. {role}",
                "",
                f"- 頻道：`{item['channel_id'] or '未記錄'}`",
                f"- 時間：{_time(item['created_at'])}",
                "",
                _quote(item["content"]),
                "",
            ])
    else:
        lines.append("（無對話紀錄）")

    return "\n".join(lines).rstrip() + "\n"


def render_table_snapshot(snapshot: dict) -> str:
    """將資料表預覽轉成 Markdown 附件。"""
    lines = [
        f"# {snapshot['label']} (`{snapshot['table']}`)",
        "",
        f"- 總筆數：{snapshot['total']}",
        f"- 本次顯示：{len(snapshot['rows'])}",
        f"- 匯出時間：{datetime.now(_TAIPEI).strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "",
    ]
    if not snapshot["rows"]:
        lines.append("（資料表目前為空）")
        return "\n".join(lines) + "\n"

    for index, row in enumerate(snapshot["rows"], start=1):
        lines.extend([f"## 資料 {index}", ""])
        for column in snapshot["columns"]:
            value = _value(row.get(column), column)
            if "\n" in value or len(value) > 160:
                lines.extend([f"### `{column}`", "", _quote(value), ""])
            else:
                lines.append(f"- `{column}`: {value}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
