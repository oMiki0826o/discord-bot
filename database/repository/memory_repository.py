"""
database/repository/memory_repository.py

Modification():
- 全部 14 個非 init_tables 函式套用 utils.async_db.to_thread 裝飾器
  （包含 load_background()：雖然讀的是檔案而非資料庫，但檔案 I/O
  同樣是同步、會阻塞事件迴圈的操作，套用同一套機制一併處理）。
  這是全專案 Repository 中呼叫頻率最高的一個，每一次 AI 對話都會
  觸發訊息儲存與記憶查詢。原本全是同步函式卻直接被 async 函式
  呼叫，改為透過 await 呼叫，實際執行委派給背景執行緒池。
  呼叫端主要是 core/ai/memory_manager.py，該檔案原本用
  loop.run_in_executor() 把整個同步的 search() 函式丟到執行緒池
  執行，等於是「用一個大執行緒池呼叫包住很多次資料庫存取」；
  現在改為 search() 本身就是 async def，直接 await 每一次資料庫
  存取，不再需要外層的 run_in_executor 包裝，呼叫端
  （core/ai/context_manager.py）也一併簡化。
  init_tables() 不套用：只在模組載入時執行一次，且已經整個被
  bot.py 的 `await asyncio.to_thread(initialize)` 包住執行。

職責：
- 訊息、長期記憶、向量記憶、摘要的純 SQL 查詢層
- 不含任何業務邏輯（排序、評分、背景任務等），只做存取

修正：
- 統一使用 get_connection()，row_factory 已在 sqlite.py 設定
- save_message 加入自動截斷（_MSG_MAX_LEN）與上限清理（_MSG_LIMIT）
- 記憶改為版本化狀態（active / provisional / superseded / retracted /
  deleted），並以 content_hash 去重；單值欄位更新不會破壞歷史版本

新增（channel_id，避免跨伺服器 / 跨頻道串台）：
- messages 表新增 channel_id 欄位，每筆訊息記錄來源頻道
- init_tables() 對既有資料庫做輕量遷移：若 messages 表已存在但
  缺少 channel_id 欄位，執行一次 ALTER TABLE ADD COLUMN
  （預設值 ''，相容舊資料）
- get_recent_messages() / get_messages_candidate() 改為
  「user_id + channel_id」雙重過濾：
    - 同一使用者在 A 伺服器與 B 伺服器的對話彼此不會出現在
      對方的「相關歷史訊息」與「最近對話」context 中
- 對話摘要也改用 user_id + channel_id 隔離，避免將 A 頻道的
  對話脈絡帶到 B 頻道。舊 summaries 表保留給管理指令與
  沒有 channel_id 的相容呼叫。

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
import hashlib
import re
from difflib import SequenceMatcher
from pathlib import Path

from database.ai.sqlite import get_connection
from utils.async_db import to_thread

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
            turn_id    TEXT    NOT NULL DEFAULT '',
            created_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE TABLE IF NOT EXISTS memories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT    NOT NULL,
            scope_type TEXT    NOT NULL DEFAULT 'channel',
            channel_id TEXT    NOT NULL DEFAULT '',
            keyword    TEXT    NOT NULL,
            category   TEXT    NOT NULL DEFAULT 'general',
            subject    TEXT    NOT NULL DEFAULT '',
            content    TEXT    NOT NULL,
            importance INTEGER NOT NULL DEFAULT 1,
            confidence REAL    NOT NULL DEFAULT 0.7,
            status     TEXT    NOT NULL DEFAULT 'active',
            source_message_id INTEGER,
            source_excerpt TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            supersedes_id INTEGER,
            expires_at REAL,
            created_at REAL    NOT NULL DEFAULT (unixepoch('now')),
            updated_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE TABLE IF NOT EXISTS vector_memories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id  INTEGER,
            user_id    TEXT    NOT NULL,
            channel_id TEXT    NOT NULL DEFAULT '',
            keyword    TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            content_hash TEXT  NOT NULL DEFAULT '',
            embedding_model TEXT NOT NULL DEFAULT '',
            embedding  TEXT    NOT NULL,
            importance INTEGER NOT NULL DEFAULT 1,
            created_at REAL    NOT NULL DEFAULT (unixepoch('now')),
            updated_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE TABLE IF NOT EXISTS summaries (
            user_id    TEXT    PRIMARY KEY,
            summary    TEXT    NOT NULL DEFAULT '',
            msg_count  INTEGER NOT NULL DEFAULT 0,
            updated_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE TABLE IF NOT EXISTS channel_summaries (
            user_id    TEXT    NOT NULL,
            channel_id TEXT    NOT NULL,
            summary    TEXT    NOT NULL DEFAULT '',
            msg_count  INTEGER NOT NULL DEFAULT 0,
            last_message_id INTEGER NOT NULL DEFAULT 0,
            updated_at REAL    NOT NULL DEFAULT (unixepoch('now')),
            PRIMARY KEY (user_id, channel_id)
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
    if "turn_id" not in cols:
        conn.execute(
            "ALTER TABLE messages ADD COLUMN turn_id TEXT NOT NULL DEFAULT ''"
        )

    # memories / vector_memories 舊版帶有無法直接移除的 UNIQUE(user_id,
    # keyword)，會讓不同頻道的同名記憶互相覆蓋。只在偵測到舊結構時
    # 以交易重建，保留舊資料並補上安全預設值。
    memory_cols = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
    required_memory_cols = {
        "channel_id", "category", "subject", "confidence", "status",
        "source_message_id", "source_excerpt", "content_hash", "supersedes_id",
        "expires_at", "updated_at",
    }
    if not required_memory_cols.issubset(memory_cols):
        conn.executescript("""
            ALTER TABLE memories RENAME TO memories_legacy;
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                scope_type TEXT NOT NULL DEFAULT 'channel',
                channel_id TEXT NOT NULL DEFAULT '',
                keyword TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'general',
                subject TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                importance INTEGER NOT NULL DEFAULT 1,
                confidence REAL NOT NULL DEFAULT 0.7,
                status TEXT NOT NULL DEFAULT 'active',
                source_message_id INTEGER,
                source_excerpt TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                supersedes_id INTEGER,
                expires_at REAL,
                created_at REAL NOT NULL DEFAULT (unixepoch('now')),
                updated_at REAL NOT NULL DEFAULT (unixepoch('now'))
            );
            INSERT INTO memories (
                id, user_id, keyword, subject, content, importance,
                confidence, status, created_at, updated_at
            )
            SELECT id, user_id, keyword, keyword, content, importance,
                   0.7, 'active', created_at, created_at
            FROM memories_legacy;
            DROP TABLE memories_legacy;
        """)

    memory_cols = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
    if "scope_type" not in memory_cols:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN "
            "scope_type TEXT NOT NULL DEFAULT 'channel'"
        )

    vector_cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(vector_memories)")
    }
    required_vector_cols = {
        "memory_id", "channel_id", "content_hash", "embedding_model", "updated_at",
    }
    if not required_vector_cols.issubset(vector_cols):
        conn.executescript("""
            ALTER TABLE vector_memories RENAME TO vector_memories_legacy;
            CREATE TABLE vector_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER,
                user_id TEXT NOT NULL,
                channel_id TEXT NOT NULL DEFAULT '',
                keyword TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL DEFAULT '',
                embedding_model TEXT NOT NULL DEFAULT '',
                embedding TEXT NOT NULL,
                importance INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL DEFAULT (unixepoch('now')),
                updated_at REAL NOT NULL DEFAULT (unixepoch('now'))
            );
            INSERT INTO vector_memories (
                id, user_id, keyword, content, embedding, importance,
                created_at, updated_at
            )
            SELECT id, user_id, keyword, content, embedding, importance,
                   created_at, created_at
            FROM vector_memories_legacy;
            DROP TABLE vector_memories_legacy;
        """)

    summary_cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(channel_summaries)")
    }
    if "last_message_id" not in summary_cols:
        conn.execute(
            "ALTER TABLE channel_summaries ADD COLUMN "
            "last_message_id INTEGER NOT NULL DEFAULT 0"
        )

    # 舊資料沒有 hash；於啟動遷移時一次回填，之後即可精確去重並避免
    # 對未變更內容重複產生 Embedding。
    for row in conn.execute(
        "SELECT id, content FROM memories WHERE content_hash = ''"
    ).fetchall():
        normalized = " ".join(str(row["content"]).casefold().split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        conn.execute(
            "UPDATE memories SET content_hash = ? WHERE id = ?",
            (digest, int(row["id"])),
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_msg_user_channel "
        "ON messages(user_id, channel_id, id DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_scope_v2 "
        "ON memories(scope_type, user_id, channel_id, status, "
        "importance DESC, updated_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mem_source "
        "ON memories(user_id, channel_id, source_message_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vec_scope "
        "ON vector_memories(user_id, channel_id)"
    )

    conn.commit()
    conn.close()

# ── Messages ──────────────────────

def _trim_channel_messages(conn, user_id: str, channel_id: str) -> None:
    count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE user_id = ? AND channel_id = ?",
        (user_id, channel_id),
    ).fetchone()[0]
    if count > _MSG_LIMIT:
        summary_row = conn.execute(
            "SELECT last_message_id FROM channel_summaries "
            "WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        ).fetchone()
        covered_id = int(summary_row["last_message_id"]) if summary_row else 0
        if covered_id <= 0:
            # 摘要尚未成功時寧可暫時超過軟上限，也不能先刪除尚未被摘要
            # 涵蓋的對話，否則 Provider 暫時失敗就會造成永久失憶。
            return
        conn.execute(
            """
            DELETE FROM messages
            WHERE id IN (
                SELECT id FROM messages
                WHERE user_id = ? AND channel_id = ? AND id <= ?
                ORDER BY id ASC LIMIT ?
            )
            """,
            (user_id, channel_id, covered_id, count - _MSG_LIMIT),
        )


@to_thread
def insert_message(user_id: str, role: str, content: str, channel_id: str = "") -> int | None:
    """
    插入訊息，超過 _MSG_LIMIT 自動刪除最舊紀錄。
    空白訊息跳過。

    channel_id：訊息來源頻道，供 get_recent_messages /
    get_messages_candidate 依頻道過濾，避免跨伺服器串台。
    _MSG_LIMIT 清理以 user_id + channel_id 為單位，避免活躍頻道把其他
    頻道的近期上下文擠出保留範圍。
    """
    if not content.strip():
        return None

    content = content[:_MSG_MAX_LEN]
    conn    = get_connection()
    c       = conn.cursor()

    c.execute(
        "INSERT INTO messages (user_id, role, content, channel_id) VALUES (?, ?, ?, ?)",
        (user_id, role, content, channel_id),
    )
    message_id = int(c.lastrowid)
    _trim_channel_messages(conn, user_id, channel_id)

    conn.commit()
    conn.close()
    return message_id


@to_thread
def insert_conversation_turn(
    user_id: str,
    user_content: str,
    assistant_content: str,
    channel_id: str = "",
    turn_id: str = "",
) -> tuple[int, int]:
    """以單一交易依序保存 user → assistant，避免順序競態。"""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        user_cursor = conn.execute(
            "INSERT INTO messages (user_id, role, content, channel_id, turn_id) "
            "VALUES (?, 'user', ?, ?, ?)",
            (user_id, user_content[:_MSG_MAX_LEN], channel_id, turn_id),
        )
        assistant_cursor = conn.execute(
            "INSERT INTO messages (user_id, role, content, channel_id, turn_id) "
            "VALUES (?, 'assistant', ?, ?, ?)",
            (user_id, assistant_content[:_MSG_MAX_LEN], channel_id, turn_id),
        )
        _trim_channel_messages(conn, user_id, channel_id)
        conn.commit()
        return int(user_cursor.lastrowid), int(assistant_cursor.lastrowid)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@to_thread
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


@to_thread
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


@to_thread
def count_messages(user_id: str, channel_id: str | None = None) -> int:
    """取得訊息數；指定 channel_id 時僅統計該對話範圍。"""
    conn = get_connection()
    c    = conn.cursor()
    if channel_id is None:
        c.execute("SELECT COUNT(*) FROM messages WHERE user_id = ?", (user_id,))
    else:
        c.execute(
            "SELECT COUNT(*) FROM messages WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )
    n = c.fetchone()[0]
    conn.close()
    return n


@to_thread
def get_messages_excluding_recent(
    user_id: str, keep_recent: int, channel_id: str | None = None,
) -> list[tuple[str, str]]:
    """
    取得排除最近 keep_recent 筆後的訊息。指定 channel_id 時
    僅在目前頻道內運作；None 保留舊的使用者全域行為。
    """
    conn = get_connection()
    c    = conn.cursor()
    if channel_id is None:
        c.execute(
            """
            SELECT role, content FROM messages
            WHERE user_id = ? AND id NOT IN (
                SELECT id FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
            """,
            (user_id, user_id, keep_recent),
        )
    else:
        c.execute(
            """
            SELECT role, content FROM messages
            WHERE user_id = ? AND channel_id = ? AND id NOT IN (
                SELECT id FROM messages WHERE user_id = ? AND channel_id = ?
                ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
            """,
            (user_id, channel_id, user_id, channel_id, keep_recent),
        )
    rows = c.fetchall()
    conn.close()
    return [(r["role"], r["content"]) for r in rows]


@to_thread
def get_messages_after(
    user_id: str,
    channel_id: str,
    after_id: int,
    *,
    exclude_recent: int = 0,
) -> list[dict]:
    """取得摘要游標後的新訊息，可保留最新數筆不納入摘要。"""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, role, content FROM messages
        WHERE user_id = ? AND channel_id = ? AND id > ?
          AND id NOT IN (
              SELECT id FROM messages
              WHERE user_id = ? AND channel_id = ?
              ORDER BY id DESC LIMIT ?
          )
        ORDER BY id ASC
        """,
        (user_id, channel_id, after_id, user_id, channel_id, exclude_recent),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

# ── Memories ──────────────────────

@to_thread
def upsert_memory(
    user_id:    str,
    keyword:    str,
    content:    str,
    importance: int,
    channel_id: str = "",
    *,
    scope_type: str = "user",
    category: str = "general",
    subject: str = "",
    confidence: float = 0.7,
    source_message_id: int | None = None,
    source_excerpt: str = "",
    content_hash: str = "",
    single_value: bool = False,
) -> int:
    """新增或確認記憶；單值欄位的新值會取代同 scope 的舊值。"""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        scope_type = "user" if scope_type == "user" else "channel"
        scoped_channel_id = "" if scope_type == "user" else channel_id
        subject = subject.strip() or keyword.strip()
        existing = None
        if content_hash:
            existing = conn.execute(
                """
                SELECT id, confidence FROM memories
                WHERE user_id = ? AND scope_type = ? AND channel_id = ?
                  AND content_hash = ?
                  AND status IN ('active', 'provisional')
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, scope_type, scoped_channel_id, content_hash),
            ).fetchone()
        if existing is None and not single_value:
            similar_rows = conn.execute(
                """
                SELECT id, content, confidence FROM memories
                WHERE user_id = ? AND scope_type = ? AND channel_id = ?
                  AND category = ? AND subject = ?
                  AND status IN ('active', 'provisional')
                ORDER BY updated_at DESC LIMIT 20
                """,
                (user_id, scope_type, scoped_channel_id, category, subject),
            ).fetchall()
            normalized_new = " ".join(content.casefold().split())
            existing = next(
                (
                    row for row in similar_rows
                    if SequenceMatcher(
                        None,
                        normalized_new,
                        " ".join(str(row["content"]).casefold().split()),
                    ).ratio() >= 0.9
                ),
                None,
            )
        if existing:
            memory_id = int(existing["id"])
            confirmed_confidence = min(
                1.0,
                max(float(existing["confidence"]), confidence) + 0.15,
            )
            conn.execute(
                """
                UPDATE memories
                SET confidence = ?, importance = MAX(importance, ?),
                    status = CASE WHEN ? >= 0.85 THEN 'active' ELSE status END,
                    source_message_id = COALESCE(?, source_message_id),
                    source_excerpt = CASE WHEN ? != '' THEN ? ELSE source_excerpt END,
                    updated_at = unixepoch('now')
                WHERE id = ?
                """,
                (
                    confirmed_confidence, importance, confirmed_confidence,
                    source_message_id,
                    source_excerpt, source_excerpt, memory_id,
                ),
            )
            conn.commit()
            return memory_id

        supersedes_id: int | None = None
        if single_value:
            previous = conn.execute(
                """
                SELECT id FROM memories
                WHERE user_id = ? AND scope_type = ? AND channel_id = ?
                  AND category = ? AND subject = ?
                  AND status IN ('active', 'provisional')
                ORDER BY updated_at DESC, id DESC LIMIT 1
                """,
                (user_id, scope_type, scoped_channel_id, category, subject),
            ).fetchone()
            if previous:
                supersedes_id = int(previous["id"])
                conn.execute(
                    "UPDATE memories SET status = 'superseded', "
                    "updated_at = unixepoch('now') WHERE id = ?",
                    (supersedes_id,),
                )
                conn.execute(
                    "DELETE FROM vector_memories WHERE memory_id = ?",
                    (supersedes_id,),
                )

        status = "active" if confidence >= 0.85 else "provisional"
        cursor = conn.execute(
            """
            INSERT INTO memories (
                user_id, scope_type, channel_id, keyword, category, subject, content,
                importance, confidence, status, source_message_id,
                source_excerpt, content_hash, supersedes_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, scope_type, scoped_channel_id,
                keyword, category, subject, content,
                importance, confidence, status, source_message_id,
                source_excerpt, content_hash, supersedes_id,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@to_thread
def retract_memories_from_previous_user_message(
    user_id: str,
    channel_id: str,
    current_message_id: int,
) -> list[int]:
    """將目前訊息之前最近一則 user 訊息所建立的記憶標記作廢。"""
    conn = get_connection()
    try:
        previous = conn.execute(
            """
            SELECT id FROM messages
            WHERE user_id = ? AND channel_id = ? AND role = 'user' AND id < ?
            ORDER BY id DESC LIMIT 1
            """,
            (user_id, channel_id, current_message_id),
        ).fetchone()
        if not previous:
            return []
        rows = conn.execute(
            """
            SELECT id FROM memories
            WHERE user_id = ? AND source_message_id = ?
              AND status IN ('active', 'provisional')
            """,
            (user_id, int(previous["id"])),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE memories SET status = 'retracted', "
                f"updated_at = unixepoch('now') WHERE id IN ({placeholders})",
                ids,
            )
            conn.execute(
                f"DELETE FROM vector_memories WHERE memory_id IN ({placeholders})",
                ids,
            )
            conn.commit()
        return ids
    finally:
        conn.close()


@to_thread
def forget_memory_slot(
    user_id: str,
    channel_id: str,
    category: str,
    subject: str,
    scope_type: str = "user",
) -> list[int]:
    """忘記同 scope 的指定欄位，保留狀態歷程但不再允許檢索。"""
    conn = get_connection()
    try:
        scope_type = "user" if scope_type == "user" else "channel"
        scoped_channel_id = "" if scope_type == "user" else channel_id
        rows = conn.execute(
            """
            SELECT id FROM memories
            WHERE user_id = ? AND scope_type = ? AND channel_id = ?
              AND category = ? AND subject = ?
              AND status IN ('active', 'provisional')
            """,
            (user_id, scope_type, scoped_channel_id, category, subject),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE memories SET status = 'deleted', "
                f"updated_at = unixepoch('now') WHERE id IN ({placeholders})",
                ids,
            )
            conn.execute(
                f"DELETE FROM vector_memories WHERE memory_id IN ({placeholders})",
                ids,
            )
            conn.commit()
        return ids
    finally:
        conn.close()


@to_thread
def get_memories_candidate(
    user_id: str, limit: int = 30, channel_id: str = "",
) -> list[tuple[str, str, int]]:
    """取出候選記憶集，由 memory_manager 負責相關性排序。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        SELECT keyword, content, importance FROM memories
        WHERE ((scope_type = 'user' AND user_id = ?)
           OR (scope_type = 'channel' AND channel_id = ?))
          AND status = 'active'
          AND (expires_at IS NULL OR expires_at > unixepoch('now'))
        ORDER BY confidence DESC, importance DESC, updated_at DESC
        LIMIT  ?
        """,
        (user_id, channel_id, limit),
    )
    rows = c.fetchall()
    conn.close()
    return [(r["keyword"], r["content"], r["importance"]) for r in rows]


@to_thread
def get_memory_records(
    user_id: str, channel_id: str, limit: int = 200,
) -> list[dict]:
    """取得目前使用者的個人記憶，加上目前頻道的共享記憶。"""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT id, user_id AS source_user_id, scope_type, channel_id,
               keyword, category, subject, content, importance, confidence,
               status, source_message_id, source_excerpt, content_hash,
               created_at, updated_at
        FROM memories
        WHERE ((scope_type = 'user' AND user_id = ?)
           OR (scope_type = 'channel' AND channel_id = ?))
          AND status = 'active'
          AND (expires_at IS NULL OR expires_at > unixepoch('now'))
        ORDER BY confidence DESC, importance DESC, updated_at DESC
        LIMIT ?
        """,
        (user_id, channel_id, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


@to_thread
def get_memories_needing_vectors(
    user_id: str,
    channel_id: str,
    limit: int = 10,
    embedding_model: str = "",
) -> list[dict]:
    """只回傳尚無相同 content_hash 向量的有效記憶。"""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT m.id, m.user_id, m.scope_type, m.channel_id, m.keyword,
               m.content, m.importance, m.content_hash
        FROM memories AS m
        LEFT JOIN vector_memories AS v
          ON v.memory_id = m.id AND v.content_hash = m.content_hash
         AND (? = '' OR v.embedding_model = ?)
        WHERE ((m.scope_type = 'user' AND m.user_id = ?)
           OR (m.scope_type = 'channel' AND m.channel_id = ?))
          AND m.status = 'active'
          AND m.content_hash != '' AND v.id IS NULL
        ORDER BY m.updated_at DESC LIMIT ?
        """,
        (embedding_model, embedding_model, user_id, channel_id, limit),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

# ── Vector Memories ──────────────────────

@to_thread
def upsert_vector(
    user_id:    str,
    keyword:    str,
    content:    str,
    embedding:  list[float],
    importance: int,
    channel_id: str = "",
    *,
    memory_id: int | None = None,
    content_hash: str = "",
    embedding_model: str = "",
) -> None:
    conn = get_connection()
    existing = None
    if content_hash:
        existing = conn.execute(
            "SELECT id FROM vector_memories WHERE user_id = ? AND channel_id = ? "
            "AND content_hash = ? ORDER BY id DESC LIMIT 1",
            (user_id, channel_id, content_hash),
        ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE vector_memories
            SET memory_id = ?, keyword = ?, content = ?, embedding = ?,
                embedding_model = ?, importance = ?, updated_at = unixepoch('now')
            WHERE id = ?
            """,
            (
                memory_id, keyword, content, json.dumps(embedding),
                embedding_model, importance, int(existing["id"]),
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO vector_memories (
                memory_id, user_id, channel_id, keyword, content, content_hash,
                embedding_model, embedding, importance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id, user_id, channel_id, keyword, content, content_hash,
                embedding_model, json.dumps(embedding), importance,
            ),
        )
    conn.commit()
    conn.close()


@to_thread
def get_all_vectors(user_id: str, channel_id: str = "") -> list[dict]:
    """取出目前使用者與目前頻道可見的有效向量記憶。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        SELECT v.memory_id, v.keyword, v.content, v.content_hash,
               v.importance, v.embedding
        FROM vector_memories AS v
        JOIN memories AS m ON m.id = v.memory_id
        WHERE ((m.scope_type = 'user' AND m.user_id = ?)
           OR (m.scope_type = 'channel' AND m.channel_id = ?))
          AND m.status = 'active'
          AND (m.expires_at IS NULL OR m.expires_at > unixepoch('now'))
        """,
        (user_id, channel_id),
    )
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        try:
            result.append({
                "keyword":    r["keyword"],
                "memory_id":  r["memory_id"],
                "content":    r["content"],
                "content_hash": r["content_hash"],
                "importance": r["importance"],
                "embedding":  json.loads(r["embedding"]),
            })
        except Exception:
            continue
    return result


@to_thread
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

@to_thread
def get_summary(user_id: str, channel_id: str = "") -> str:
    conn = get_connection()
    c    = conn.cursor()
    if channel_id:
        c.execute(
            "SELECT summary FROM channel_summaries WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )
    else:
        c.execute("SELECT summary FROM summaries WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row["summary"] if row else ""


@to_thread
def get_summary_state(user_id: str, channel_id: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        """
        SELECT summary, msg_count, last_message_id, updated_at
        FROM channel_summaries WHERE user_id = ? AND channel_id = ?
        """,
        (user_id, channel_id),
    ).fetchone()
    conn.close()
    if not row:
        return {"summary": "", "msg_count": 0, "last_message_id": 0, "updated_at": 0}
    return dict(row)


@to_thread
def upsert_summary(
    user_id: str,
    summary: str,
    msg_count: int,
    channel_id: str = "",
    last_message_id: int = 0,
) -> None:
    conn = get_connection()
    if channel_id:
        conn.execute(
            """
            INSERT INTO channel_summaries (
                user_id, channel_id, summary, msg_count, last_message_id
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, channel_id) DO UPDATE SET
                summary = excluded.summary,
                msg_count = excluded.msg_count,
                last_message_id = excluded.last_message_id,
                updated_at = unixepoch('now')
            """,
            (user_id, channel_id, summary, msg_count, last_message_id),
        )
    else:
        conn.execute(
            """
            INSERT INTO summaries (user_id, summary, msg_count)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                summary = excluded.summary,
                msg_count = excluded.msg_count,
                updated_at = unixepoch('now')
            """,
            (user_id, summary, msg_count),
        )
    conn.commit()
    conn.close()


@to_thread
def count_summaries() -> int:
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT "
        "(SELECT COUNT(*) FROM summaries WHERE summary != '') + "
        "(SELECT COUNT(*) FROM channel_summaries WHERE summary != '')"
    )
    n = c.fetchone()[0]
    conn.close()
    return n

# ── Background Memories ──────────────────────

# ── 區塊標題格式：【標題】開頭一行 ──────────────────────
_SECTION_RE = re.compile(r"^【(.+?)】\s*$")


@to_thread
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
