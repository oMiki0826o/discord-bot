"""
database/repository/memory_repository.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

職責：
- 訊息、長期記憶、向量記憶、摘要的純 SQL 查詢層
- 不含任何業務邏輯（排序、評分、背景任務等），只做存取

修正：
- 統一使用 get_connection()，row_factory 已在 sqlite.py 設定
- save_message 加入自動截斷（_MSG_MAX_LEN）與上限清理（_MSG_LIMIT）
- save_memory 使用 ON CONFLICT DO UPDATE，保證 UPSERT 語意

新增（channel_id，避免跨伺服器 / 跨頻道串台）：
- messages 表新增 channel_id 欄位，每筆訊息記錄來源頻道
- init_tables() 對既有資料庫做輕量遷移：若 messages 表已存在但
  缺少 channel_id 欄位，執行一次 ALTER TABLE ADD COLUMN
  （預設值 ''，相容舊資料）
- get_recent_messages() / get_messages_candidate() 改為
  「user_id + channel_id」雙重過濾：
    - 同一使用者在 A 伺服器與 B 伺服器的對話彼此不會出現在
      對方的「相關歷史訊息」與「最近對話」context 中
- 設計取捨：
    - count_messages() / get_messages_excluding_recent() 仍維持
      「僅以 user_id 過濾」，因為對話摘要（summaries 表）與
      _MSG_LIMIT 清理目前是以「使用者」為單位的長期記憶，
      代表的是使用者整體輪廓，刻意跨頻道彙整；
      只有「短期對話 context」需要依 channel_id 隔離，
      避免 AI 把 A 伺服器的閒聊內容當成 B 伺服器的對話脈絡

修正（load_background 從未真正生效的 bug）：
- 原 load_background() 假設 background.txt 是「key=value」逐行格式，
  以 "=" 分割每一行；但實際 background.txt（角色背景設定）是
  以【區塊標題】分段的自然語言段落，完全沒有 "=" 符號。
  結果是 load_background() 永遠回傳空 list，這份角色背景設定
  從未真正被注入到任何 prompt 中。
- 改為依【區塊標題】分段解析：每個區塊成為一筆
  (區塊標題, 區塊內容, importance=5) 的 tuple；
  若檔案完全沒有任何【】區塊（純自由格式文字），整份內容
  視為單一筆 ("background", 全文, 5)，確保任何格式都至少
  會被讀入，不會再悄悄回傳空結果
- 仍相容「key=value」格式：若偵測到該行含 "="，視為單獨一筆
  (key, value, 5)，新舊兩種寫法皆可運作
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from database.ai.sqlite import get_connection

# ── 常數 ──────────────────────

_MSG_LIMIT   = 200     # 每位使用者保留的訊息上限
_MSG_MAX_LEN = 2_000   # 單筆訊息最大字元數
_BG_FILE     = Path(__file__).resolve().parents[2] / "database" / "ai" / "background.txt"

# ── 初始化 ──────────────────────

def init_tables() -> None:
    """建立所有記憶相關資料表，並對既有資料庫做欄位遷移。"""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT    NOT NULL,
            role       TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            channel_id TEXT    NOT NULL DEFAULT '',
            created_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE TABLE IF NOT EXISTS memories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT    NOT NULL,
            keyword    TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            importance INTEGER NOT NULL DEFAULT 1,
            created_at REAL    NOT NULL DEFAULT (unixepoch('now')),
            UNIQUE(user_id, keyword)
        );
        CREATE TABLE IF NOT EXISTS vector_memories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT    NOT NULL,
            keyword    TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            embedding  TEXT    NOT NULL,
            importance INTEGER NOT NULL DEFAULT 1,
            created_at REAL    NOT NULL DEFAULT (unixepoch('now')),
            UNIQUE(user_id, keyword)
        );
        CREATE TABLE IF NOT EXISTS summaries (
            user_id    TEXT    PRIMARY KEY,
            summary    TEXT    NOT NULL DEFAULT '',
            msg_count  INTEGER NOT NULL DEFAULT 0,
            updated_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_msg_user
            ON messages(user_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_mem_user
            ON memories(user_id, importance DESC);
        CREATE INDEX IF NOT EXISTS idx_vec_user
            ON vector_memories(user_id);
    """)

    # ── 相容性遷移：舊資料庫的 messages 表可能沒有 channel_id ──────────────────────
    # CREATE TABLE IF NOT EXISTS 不會幫既有資料表補欄位，
    # 需手動檢查並以 ALTER TABLE 補上，預設值 '' 不影響舊資料查詢。
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(messages)")}
    if "channel_id" not in cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN channel_id TEXT NOT NULL DEFAULT ''"
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_user_channel "
        "ON messages(user_id, channel_id, id DESC)"
    )

    conn.commit()
    conn.close()

# ── Messages ──────────────────────

def insert_message(user_id: str, role: str, content: str, channel_id: str = "") -> None:
    """
    插入訊息，超過 _MSG_LIMIT 自動刪除最舊紀錄。
    空白訊息跳過。

    channel_id：訊息來源頻道，供 get_recent_messages /
    get_messages_candidate 依頻道過濾，避免跨伺服器串台。
    _MSG_LIMIT 清理仍以 user_id（不分頻道）為單位，
    詳見本檔標頭「設計取捨」說明。
    """
    if not content.strip():
        return

    content = content[:_MSG_MAX_LEN]
    conn    = get_connection()
    c       = conn.cursor()

    c.execute(
        "INSERT INTO messages (user_id, role, content, channel_id) VALUES (?, ?, ?, ?)",
        (user_id, role, content, channel_id),
    )
    c.execute(
        "SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,),
    )
    count = c.fetchone()[0]
    if count > _MSG_LIMIT:
        c.execute(
            """
            DELETE FROM messages
            WHERE user_id = ? AND id IN (
                SELECT id FROM messages WHERE user_id = ?
                ORDER BY id ASC LIMIT ?
            )
            """,
            (user_id, user_id, count - _MSG_LIMIT),
        )

    conn.commit()
    conn.close()


def get_recent_messages(
    user_id: str, channel_id: str, limit: int = 12,
) -> list[tuple[str, str]]:
    """
    取得「同一頻道」最近 N 筆訊息，依時間正序（舊→新）。

    依 user_id + channel_id 過濾，避免將使用者在其他伺服器 /
    頻道的對話內容混入目前頻道的「最近對話」context。
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT role, content FROM messages "
        "WHERE user_id = ? AND channel_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, channel_id, limit),
    )
    rows = c.fetchall()
    conn.close()
    return [(r["role"], r["content"]) for r in reversed(rows)]


def get_messages_candidate(
    user_id: str, channel_id: str, limit: int = 200,
) -> list[tuple[str, str]]:
    """
    取出「同一頻道」候選訊息集，由 memory_manager 負責相關性排序。

    依 user_id + channel_id 過濾，理由同 get_recent_messages()。
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT role, content FROM messages "
        "WHERE user_id = ? AND channel_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, channel_id, limit),
    )
    rows = c.fetchall()
    conn.close()
    return [(r["role"], r["content"]) for r in rows]


def count_messages(user_id: str) -> int:
    """使用者全部頻道的訊息總數（供 _MSG_LIMIT 清理與摘要觸發判斷）。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,))
    n = c.fetchone()[0]
    conn.close()
    return n


def get_messages_excluding_recent(
    user_id: str, keep_recent: int,
) -> list[tuple[str, str]]:
    """
    取出使用者全部頻道中、除最新 keep_recent 筆外的所有訊息，用於摘要。

    刻意不依 channel_id 過濾：摘要代表使用者整體輪廓，
    跨頻道彙整為單一 summaries 紀錄（詳見本檔標頭說明）。
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        SELECT role, content FROM messages
        WHERE  user_id = ?
          AND  id NOT IN (
              SELECT id FROM messages WHERE user_id = ?
              ORDER BY id DESC LIMIT ?
          )
        ORDER BY id ASC
        """,
        (user_id, user_id, keep_recent),
    )
    rows = c.fetchall()
    conn.close()
    return [(r["role"], r["content"]) for r in rows]

# ── Memories ──────────────────────

def upsert_memory(
    user_id:    str,
    keyword:    str,
    content:    str,
    importance: int,
) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO memories (user_id, keyword, content, importance)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, keyword) DO UPDATE SET
            content    = excluded.content,
            importance = excluded.importance,
            created_at = unixepoch('now')
        """,
        (user_id, keyword, content, importance),
    )
    conn.commit()
    conn.close()


def get_memories_candidate(
    user_id: str, limit: int = 30,
) -> list[tuple[str, str, int]]:
    """取出候選記憶集，由 memory_manager 負責相關性排序。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        SELECT keyword, content, importance FROM memories
        WHERE  user_id = ?
        ORDER  BY importance DESC, created_at DESC
        LIMIT  ?
        """,
        (user_id, limit),
    )
    rows = c.fetchall()
    conn.close()
    return [(r["keyword"], r["content"], r["importance"]) for r in rows]

# ── Vector Memories ──────────────────────

def upsert_vector(
    user_id:    str,
    keyword:    str,
    content:    str,
    embedding:  list[float],
    importance: int,
) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO vector_memories (user_id, keyword, content, embedding, importance)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, keyword) DO UPDATE SET
            content    = excluded.content,
            embedding  = excluded.embedding,
            importance = excluded.importance,
            created_at = unixepoch('now')
        """,
        (user_id, keyword, content, json.dumps(embedding), importance),
    )
    conn.commit()
    conn.close()


def get_all_vectors(user_id: str) -> list[dict]:
    """取出所有向量記憶（keyword, content, importance, embedding）。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT keyword, content, importance, embedding FROM vector_memories WHERE user_id = ?",
        (user_id,),
    )
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        try:
            result.append({
                "keyword":    r["keyword"],
                "content":    r["content"],
                "importance": r["importance"],
                "embedding":  json.loads(r["embedding"]),
            })
        except Exception:
            continue
    return result


def count_vectors(user_id: str = "") -> int:
    conn = get_connection()
    c    = conn.cursor()
    if user_id:
        c.execute(
            "SELECT COUNT(*) FROM vector_memories WHERE user_id = ?", (user_id,),
        )
    else:
        c.execute("SELECT COUNT(*) FROM vector_memories")
    n = c.fetchone()[0]
    conn.close()
    return n

# ── Summaries ──────────────────────

def get_summary(user_id: str) -> str:
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT summary FROM summaries WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row["summary"] if row else ""


def upsert_summary(user_id: str, summary: str, msg_count: int) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO summaries (user_id, summary, msg_count)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            summary    = excluded.summary,
            msg_count  = excluded.msg_count,
            updated_at = unixepoch('now')
        """,
        (user_id, summary, msg_count),
    )
    conn.commit()
    conn.close()


def count_summaries() -> int:
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT COUNT(*) FROM summaries WHERE summary != ''")
    n = c.fetchone()[0]
    conn.close()
    return n

# ── Background Memories ──────────────────────

# ── 區塊標題格式：【標題】開頭一行 ──────────────────────
_SECTION_RE = re.compile(r"^【(.+?)】\s*$")


def load_background() -> list[tuple[str, str, int]]:
    """
    讀取 database/ai/background.txt。

    支援兩種格式（可同時混用，逐行判斷）：
    1. 【區塊標題】開頭的段落 → (區塊標題, 區塊內容, 5)
    2. key=value 單行 → (key, value, 5)

    第一個【區塊標題】出現前的純文字（例如開頭一行簡介）會收集為
    ("intro", 文字, 5)，避免被靜默捨棄。

    若整份檔案完全偵測不到上述任何格式（純自由格式文字，無區塊
    標題、無 "=" 符號），整份內容視為單一筆
    ("background", 全文, 5)，確保不會因格式不符而靜默回傳空結果。
    """
    if not _BG_FILE.exists():
        return []

    try:
        raw = _BG_FILE.read_text(encoding="utf-8")
    except Exception:
        return []

    if not raw.strip():
        return []

    result:        list[tuple[str, str, int]] = []
    intro_lines:   list[str] = []
    current_title: str | None = None
    current_lines: list[str] = []
    found_section: bool = False

    def _flush_section() -> None:
        if current_title is not None:
            content = "\n".join(current_lines).strip()
            if content:
                result.append((current_title, content, 5))

    for line in raw.splitlines():
        stripped = line.strip()
        section_match = _SECTION_RE.match(stripped)

        if section_match:
            _flush_section()
            current_title = section_match.group(1)
            current_lines = []
            found_section = True
            continue

        if current_title is not None:
            current_lines.append(line)
        elif "=" in stripped and stripped:
            k, v = stripped.split("=", 1)
            k, v = k.strip(), v.strip()
            if k and v:
                result.append((k, v, 5))
        elif stripped:
            intro_lines.append(stripped)

    _flush_section()

    intro_text = "\n".join(intro_lines).strip()
    if intro_text:
        if found_section or result:
            # 有區塊標題（intro 是「第一個標題前」的文字）或已有
            # key=value 項目（intro 是穿插其中的雜散文字）
            result.insert(0, ("intro", intro_text, 5))
        else:
            # 完全沒有偵測到任何格式（無標題、無 key=value），
            # 整份內容就是這段 intro_text，標記為 background 更貼切
            result.append(("background", intro_text, 5))

    return result


# ── 啟動時建立資料表 ──────────────────────
init_tables()
