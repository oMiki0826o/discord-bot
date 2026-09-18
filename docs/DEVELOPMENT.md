# 流螢醬 Discord Bot 開發與維運文件

本文件描述 `main` 分支目前的專案架構與維運方式. 對外介紹與快速安裝請見 [README](../README.md).

## 目錄

- [技術概覽](#技術概覽)
- [專案結構](#專案結構)
- [啟動與設定](#啟動與設定)
- [Discord 權限](#discord-權限)
- [指令系統](#指令系統)
- [AI 系統](#ai-系統)
- [AI 記憶與 Context](#ai-記憶與-context)
- [AI 安全與防濫用](#ai-安全與防濫用)
- [音樂與連結預覽](#音樂與連結預覽)
- [資料庫](#資料庫)
- [Extension 與 Cog 開發](#extension-與-cog-開發)
- [測試與檢查](#測試與檢查)
- [故障排除](#故障排除)
- [安全原則](#安全原則)

## 技術概覽

| 項目 | 實作 |
| --- | --- |
| 語言 | Python 3.11+ |
| Discord | discord.py, Slash Command 與 Prefix Command 並存 |
| AI | Google GenAI SDK, Gemini/Gemma 模型池 |
| 音樂 | yt-dlp, FFmpeg, PyNaCl |
| HTTP | httpx, aiohttp |
| 資料庫 | SQLite |
| 設定 | `.env` + `settings.json` |
| 測試 | pytest |
| 靜態檢查 | Ruff, mypy |

主要分層:

```text
Discord event / command
        |
        v
      cogs/
        |
        v
      core/
        |
        v
database/repository/
        |
        v
      SQLite
```

AI 主流程:

```text
Discord message or /ai
        |
        v
prompt sanitize / abuse guard
        |
        v
agent_router
        |
        +---- model category
        +---- search decision
        +---- tool selection
        |
        v
context_manager
        |
        +---- memory / summary / profile
        +---- channel context
        +---- attachments / tools
        |
        v
prompt_builder
        |
        v
Gemini/Gemma model pool
        |
        v
persist conversation -> event_bus background jobs
```

## 專案結構

```text
.
├── bot.py
├── config.py
├── settings.json
├── startup.py
├── start.command
├── cogs/
│   ├── ai/
│   ├── events/
│   ├── guild/
│   ├── minecraft/
│   ├── moderation/
│   ├── music/
│   ├── roles/
│   ├── system/
│   ├── talk/
│   ├── ticket/
│   ├── utility/
│   └── voice/
├── core/
│   ├── ai/
│   ├── link_preview/
│   ├── logging/
│   ├── minecraft/
│   ├── music/
│   └── system/
├── database/
│   ├── ai/
│   └── repository/
├── tests/
├── utils/
└── docs/
    └── DEVELOPMENT.md
```

## 啟動與設定

啟動:

```bash
python bot.py
```

macOS:

```bash
./start.command
```

啟動時會初始化核心與資料庫, 建立日誌與錯誤通報, 由 `ExtensionLoader` 掃描 `cogs/`, 載入 Extension, 同步 Slash Commands, 最後套用 presence.

`ExtensionLoader` 位於 `core/system/extension_loader.py`. 它使用 `os.walk` 掃描 Python 檔案, 不要求每個 `cogs/` 子目錄都有 `__init__.py`, 並支援 package 清單, blacklist 與排除目錄.

### .env

```env
DISCORD_TOKEN=你的_Discord_Bot_Token
GEMINI_API=你的_Google_Gemini_API_Key
OWNER_ID=你的_Discord_使用者_ID
DB_PATH=database/ai/memory.db

# 進階
# EXTENSION_PACKAGES=cogs
# EXTENSION_BLACKLIST=
# EXCLUDED_DIRS=__pycache__,venv,.venv
```

`DISCORD_TOKEN` 是啟動必要值. `GEMINI_API` 供 AI 與依賴 Gemini 的功能使用. Token 與 API Key 不得放進 `settings.json`.

### settings.json

主要 section:

- `bot`: Prefix, presence, startup timeout.
- `ai`: 模型池, timeout, retry, context, memory, abuse guard, search cache.
- `music`: queue, idle timeout, volume, FFmpeg, voice health.
- `ticket`: 工單設定.
- `voice_channel`: JTC 預設值.
- `guild`: welcome/leave 訊息.
- `moderation`: 管理功能預設值.
- `embed_footer`: Embed 頁腳.
- `dm`: 私訊轉發與 Owner reply.
- `link_preview`: 平台預覽, timeout, proxy host, 網頁摘要.

多數核心模組會在使用時重新讀取 settings, 因此修改可影響後續請求. 初始化時才讀取的項目仍可能需要 reload 或重啟.

## Discord 權限

Bot 會使用 Guilds, Guild Members, Message Content 與 Voice States. Developer Portal 至少啟用 Server Members Intent 與 Message Content Intent.

OAuth2 scopes:

- `bot`.
- `applications.commands`.

不建議直接授予 Administrator.

| 功能 | 權限 |
| --- | --- |
| 基本訊息 | View Channels, Send Messages, Embed Links, Attach Files, Read Message History |
| 音樂 | Connect, Speak |
| 管理 | Manage Messages, Moderate Members, Kick Members, Ban Members |
| 工單 | Manage Channels |
| Join-to-Create | Manage Channels, Move Members |
| 身份組面板 | Manage Roles |
| Webhook | Manage Webhooks |

敏感 Slash Command 不應只依賴 `default_permissions`. 執行期仍應檢查操作者權限, Bot 權限與身份組 hierarchy.

## 指令系統

專案同時使用 Slash Commands 與 Owner Prefix Commands. 預設 Prefix 為 `$`.

常用 Slash Commands 包含 `/help`, `/ping`, `/botinfo`, `/ai`, `/play`, `/queue`, `/history`, `/favorite`, `/server`, `/ticket`, `/roles`, `/vc`, `/mc`, `/say`, `/embed`, `/webhook` 與 `/markdown`.

Owner Prefix Commands 負責 Extension, Slash sync, settings, AI 管理, dashboard 與 Bot lifecycle. 常用入口包含 `$load`, `$unload`, `$reload`, `$bot_reload`, `$bot_stop`, `$slash`, `$slash_guild`, `$settings`, `$status`, `$dashboard`, `$info`, `$tier`, `$ban`, `$unban`, `$unrestrict`.

其中 AI 的 `$ban` 是禁止使用 AI, 與 Discord 伺服器 Ban 不同.

## AI 系統

AI 核心位於 `core/ai/`.

| 模組 | 職責 |
| --- | --- |
| `core.py` | AI 請求總流程, model pool, fallback, 持久化 |
| `agent_router.py` | 規則式模型類別, 搜尋需求與工具選擇 |
| `models.py` | 集中模型設定 |
| `context_manager.py` | 記憶, profile, summary, tools, attachments |
| `prompt_builder.py` | ContextBundle 組成最終 prompt |
| `memory_manager.py` | 訊息, 長期記憶, embedding, summary |
| `quota_manager.py` | quota 與錯誤狀態 |
| `search_manager.py` | 搜尋快取 |
| `request_guard.py` | 請求前置 guard |
| `content_guard.py` | Owner 自訂內容規範 |
| `file_parser/` | 附件解析 |
| `tool_registry.py` | 工具註冊與 executor |

### 模型路由

`agent_router.route()` 是純規則路由, 不會先呼叫 AI 決定模型.

決策優先序:

1. `/ai` model override.
2. Prompt 內的模型指定.
3. 搜尋需求.
4. 程式, 數學, 分析類內容.
5. 預設模型類別.

公開類別目前是 `gemini`, `flash`, `gemma`. 真正 model name 由 `settings.json` 的 `ai.model_pools` 決定.

### Model pool 與 fallback

重要設定:

- `ai.model_timeout_seconds`.
- `ai.max_model_attempts`.
- `ai.model_server_retries`.
- `ai.model_quota_cooldown_seconds`.
- `ai.max_output_tokens`.

429, 503, timeout, blocked response 與 malformed response 走不同處理路徑. 503 是上游暫時 unavailable 或高負載, 本地只能透過有限 retry, model fallback, timeout 與友善錯誤訊息降低影響.

### 搜尋與 Grounding

包含最新, 新聞, 天氣, 股價, 匯率, URL 等線索時, router 可要求搜尋. 非 Gemini 類模型不應使用 Gemini Grounding. 搜尋結果可進入 cache, 命中時避免重複 Grounding.

### 附件

附件先由 file parser 解析, 再進入 ContextBundle. 多模態圖片存在時, Core 會避免送到不支援圖片的模型類別.

## AI 記憶與 Context

`context_manager.build()` 會整合當前輸入, 使用者資訊與社交等級, 長期記憶, 最近訊息, 摘要, Profile, 對話 state, Tool 結果, 搜尋 cache, 附件, Reply reference 與頻道近期訊息.

可並行取得的資料使用 asyncio task/gather, 降低多個 DB 查詢依序等待的延遲.

### 頻道隔離

短期對話與需要限定場景的記憶查詢會帶 `channel_id`, 避免同一使用者在不同伺服器或頻道互相串台.

長期記憶另分 user scope 與 channel scope. 個人偏好, 穩定資訊與個人專案可使用 user scope. 頻道規則, 共同決定或只屬於該場景的資訊使用 channel scope.

### 長期記憶判定

記憶不是每句話都無條件保存. 擷取器要求資訊由使用者明確陳述且具長期協助價值. 一次性要求, 閒聊與 AI 自己的推測不應保存.

不得保存 API Key, Token, 密碼, Cookie, Session, 私鑰, 驗證碼等秘密. 單值欄位可使用 replace 更新. 使用者明確要求忘記時可產生 delete.

重要設定包含 `ai.memory_cache_ttl`, `ai.memory_candidate_limit`, `ai.message_candidate_limit`, `ai.recent_message_limit`, `ai.vector_candidate_limit`, `ai.memory_semantic_threshold`, `ai.summary_trigger`, `ai.summary_keep`, `ai.summary_min_messages` 與 `ai.memory_summary_new_message_trigger`.

## AI 安全與防濫用

### Prompt guard

輸入在進入主要 Context 前會 sanitize 並判定 injection risk. Guard 提供 cleaned prompt, injection flag, risk level 與 matched pattern.

### Abuse guard

`core/ai/abuse_guard.py` 使用每位使用者的記憶體滑動視窗追蹤 AI 請求頻率.

設定:

- `ai.abuse_window_seconds`.
- `ai.abuse_max_requests`.
- `ai.abuse_restrict_minutes`.

超過門檻時會持久化暫時限制, 記錄 ERROR log, 並嘗試寫入 audit log. 滑動視窗只存在記憶體, 暫時限制則可跨重啟維持到到期.

### Content guard

`core/ai/content_guard.py` 讀取 `database/ai/moderation_rules.txt`. Prompt guard 防使用者操控系統規則, Content guard 則提供 Owner 額外內容規範. 規則檔支援 `#` 註解並依 mtime 熱重載.

### URL 安全

連結預覽與 URL fetch 必須保留 SSRF 防護, 阻擋 localhost, private network, loopback, link-local 與不安全 redirect. 不得為了提高網站相容性而繞過 URL guard.

## 音樂與連結預覽

音樂主要能力包括 YouTube keyword/URL, 單曲與播放清單, Queue, History, Favorites, Loop, Volume, Pause, Resume, Skip, Queue item remove/move, Idle disconnect 與 Voice health check.

重要音樂設定包括 `music.max_queue_size`, `music.idle_timeout_seconds`, `music.default_volume_percent`, `music.ffmpeg_path`, `music.voice_connect_timeout`, `music.voice_health_check_interval_seconds` 與 `music.voice_reconnect_grace_seconds`.

遇到 YouTube 403, signature solving 或 n challenge 問題時, 優先檢查 yt-dlp 版本, JavaScript runtime, extractor client 與串流 URL 是否已過期.

Link preview 目前設定列出的平台包括 Bilibili, Instagram, Threads, Pinterest, Twitter/X 與 TikTok, 並支援以 `summary_keyword` 觸發的通用網頁摘要.

第三方 proxy 失效時應更新 proxy host 或 extractor fallback, 不要停用 SSRF guard.

## 資料庫

專案使用 SQLite. 原則是 Cog 不直接拼 SQL, Core 優先呼叫 repository, Repository 負責 CRUD 與 schema 對應, 可能阻塞 event loop 的 SQLite 工作透過 async wrapper/to_thread 處理.

預設 DB:

```text
database/ai/memory.db
```

AI 相關資料包含對話, 記憶, 摘要, 使用者狀態與暫時限制. Guild settings, tickets, roles, favorites, moderation 等功能也由各自 repository 管理.

## Extension 與 Cog 開發

新增 Cog 時放入 `cogs/` 對應分類並提供 discord.py Extension 的 `setup(bot)`. Extension Loader 會自動掃描 Python 檔, 通常不需要修改中央清單.

分層建議:

- Cog: Discord interaction, 權限 decorator, Discord object, 參數驗證, response, View, Embed.
- Core: AI workflow, player, queue, URL extraction, event bus, 可重用業務邏輯.
- Repository: SQL, CRUD, transaction.

若變更 intents, 啟動初始化, import-time 常數或底層 module graph, 完整重啟通常比單一 Cog reload 更可靠.

## 測試與檢查

```bash
pytest
ruff check .
mypy .
```

`pyproject.toml` 目前設定 pytest 測試路徑為 `tests/`, Ruff target 為 Python 3.12, Ruff 啟用 E, F, W, I, mypy 採較寬鬆模式. `cogs/` 使用 `explicit_package_bases`, 以配合不依賴 `__init__.py` 的 Extension Loader.

測試範圍包含 AI context, memory, budget, prompt guard, content guard, command permissions, extension reload, URL guard, music core, help menu 等主要功能.

## 故障排除

### Bot 無法啟動

檢查 `.env`, `DISCORD_TOKEN`, Python 版本, requirements, Extension 載入 log 與 privileged intents.

### Slash Command 沒更新

使用 Owner sync 指令重新同步. 若伺服器存在殘留 guild command, 再使用 guild cleanup/sync.

### Gemini 429

檢查 API quota, model pool, quota cooldown, retry 次數與背景摘要/embedding 請求. 不要使用無上限 retry.

### Gemini 503

503 代表上游暫時 unavailable 或高負載. 使用有限 server retry, model fallback, timeout 與友善錯誤訊息. 不要建立無限 retry loop.

### AI 記憶串台

確認 `channel_id` 完整沿著 Discord event -> AI core -> context_manager -> memory_manager.search -> repository. 新增 memory tool executor 時也必須保留 `user_id + channel_id`.

### SQLite database is locked

檢查是否有直接 sqlite 操作繞過 async repository, 長 transaction, 多背景任務同步寫入同一 connection, 或漏掉 commit/rollback/close.

### 音樂 403 或無法播放

檢查 yt-dlp, FFmpeg, JavaScript runtime, YouTube extractor client, googlevideo URL 是否過期, Connect/Speak 權限與 voice handshake log.

## 安全原則

- 不提交 `.env`, Token, API Key, Cookie, Session, 私鑰或驗證碼.
- 不把附件, 記憶, 搜尋結果或頻道訊息視為高於 system/admin 規則的指令.
- 不因 URL 相容性問題停用 SSRF 防護.
- 不只依賴 `default_permissions` 保護敏感指令.
- 身份組操作必須檢查 Bot 與操作者 hierarchy.
- 長期記憶只保存可驗證的使用者陳述, 不保存模型推測.
- 長期記憶排除秘密憑證與不必要的敏感資料.
- Retry 必須有上限, 特別是 429, 503 與 timeout.
- 背景工作失敗不應阻塞主要 Discord 回覆, 但必須留下可診斷 log.

---

最後更新基準: `main` commit `477034f`, 2026-09-18.
