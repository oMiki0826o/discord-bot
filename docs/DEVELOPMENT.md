# 流螢醬 Discord Bot 開發與維運文件

本文件以目前程式碼為準，提供開發、部署、權限管理與故障排除所需的技術資訊。專案簡介與快速安裝請見 [README](../README.md)。

## 目錄

- [技術概覽](#技術概覽)
- [開發環境](#開發環境)
- [Discord 設定與 Bot 權限](#discord-設定與-bot-權限)
- [啟動流程](#啟動流程)
- [專案結構](#專案結構)
- [指令與權限](#指令與權限)
- [設定系統](#設定系統)
- [核心子系統](#核心子系統)
- [資料庫](#資料庫)
- [開發 Cog](#開發-cog)
- [測試與檢查](#測試與檢查)
- [維運與故障排除](#維運與故障排除)
- [安全原則](#安全原則)

## 技術概覽

| 項目 | 實作 |
|---|---|
| Discord 框架 | discord.py 2.7.1，Slash Command 與 Prefix Command 並存 |
| Python | 3.11 以上 |
| AI | `google-genai`，Gemini/Gemma 路由、記憶、內容審核與用量管理 |
| 音樂 | yt-dlp、FFmpeg、PyNaCl |
| HTTP | httpx 與 aiohttp |
| 文件解析 | MarkItDown 與專案自有 file parser registry |
| 資料庫 | SQLite，Repository Pattern |
| 設定 | `.env` 儲存機密；`settings.json` 儲存可熱更新行為參數 |
| 測試 | pytest |

主要分層：

```text
Discord event / command
        ↓
      cogs/
        ↓
      core/          業務邏輯
        ↓
database/repository/ 資料存取
        ↓
      SQLite
```

Cog 應處理 Discord 互動、參數驗證與回覆；可重用的邏輯應放在 `core/`；SQL 應放在 `database/repository/`。

## 開發環境

### 必要軟體

- Python 3.11+
- FFmpeg
- Git
- Discord Bot Token
- Google Gemini API Key（開發 AI 功能時）

`pymediainfo` 如要讀取完整媒體資訊，主機上還需 MediaInfo 共用程式庫。

### 建立虛擬環境

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pytest
cp .env.example .env
```

### `.env`

```env
DISCORD_TOKEN=Discord_Bot_Token
GEMINI_API=Google_Gemini_API_Key
OWNER_ID=Discord_User_ID
DB_PATH=database/ai/memory.db

# 進階選項
# EXTENSION_PACKAGES=cogs
# EXTENSION_BLACKLIST=
# EXCLUDED_DIRS=__pycache__,venv,.venv
```

`DISCORD_TOKEN` 是唯一會在啟動階段強制要求的變數。`OWNER_ID` 未設定時，Owner 指令與私訊轉發無法按預期使用。`GEMINI_API` 未設定時，非 AI 功能仍可運作。

## Discord 設定與 Bot 權限

### Gateway Intents

`FireflyBot` 會啟用：

- Guilds
- Guild Members
- Message Content
- Voice States

因此 Discord Developer Portal 的 Bot 頁面必須啟用 `Server Members Intent` 與 `Message Content Intent`。

### OAuth2 scopes

- `bot`
- `applications.commands`

### 建議 Bot 權限

| 功能 | Bot 所需權限 |
|---|---|
| 基本回覆 | View Channels、Send Messages、Embed Links、Attach Files、Read Message History |
| 連結預覽 | Send Messages、Embed Links；Manage Messages 可用於壓制原生預覽 |
| 音樂 | Connect、Speak |
| 管理 | Manage Messages、Moderate Members、Kick Members、Ban Members |
| 工單 | Manage Channels |
| 臨時語音 | Manage Channels、Move Members |
| 身份組面板 | Manage Roles，且 Bot 身份組必須高於可領取的身份組 |
| Webhook 代發 | Manage Webhooks |

不建議為了方便而直接授予 `Administrator`。

## 啟動流程

```bash
python bot.py
```

`FireflyBot.setup_hook()` 依序執行：

1. 在背景執行緒呼叫 `startup.initialize()`。
2. 執行核心預熱與資料庫初始化。
3. 掛載 Discord 錯誤通報 handler。
4. `ExtensionLoader` 掃描 `EXTENSION_PACKAGES` 中的 Python 檔案。
5. 載入所有未被排除的 Cog。
6. 將全域 Slash Commands 同步至 Discord。
7. 設定 `ready_event`，讓背景任務繼續。

`on_ready()` 會套用 `settings.json` 的狀態。`close()` 會嘗試傳送 Owner 關機報告，然後關閉 Discord 連線。

macOS 可使用 `./start.command`；該腳本會切換到專案目錄，若 `.venv` 存在則自動啟用。

## 專案結構

```text
.
├── bot.py                         Bot 入口、intents、全域錯誤處理
├── config.py                      .env 與路徑設定
├── settings.json                  可熱更新的行為設定
├── startup.py                     啟動預載
├── cogs/
│   ├── ai/                        AI 入口與 Owner 管理
│   ├── events/                    訊息、狀態與連結預覽 listener
│   ├── guild/                     伺服器設定
│   ├── minecraft/                 Minecraft 計算工具
│   ├── moderation/                管理指令
│   ├── music/                     音樂指令
│   ├── roles/                     身份組面板
│   ├── system/                    Extension、Owner 與 settings 指令
│   ├── talk/                      代發、Embed、Webhook、typing
│   ├── ticket/                    工單
│   ├── utility/                   一般工具、收藏、Markdown 轉換
│   └── voice/                     Join-to-Create 語音頻道
├── core/
│   ├── ai/                        AI 路由、記憶、guard、file parser
│   ├── link_preview/              URL 偵測、SSRF guard、平台 extractor
│   ├── logging/                   日誌與 Discord 通報
│   ├── minecraft/                 計算引擎
│   ├── music/                     player、queue、song、view
│   └── system/                    settings、event bus、extension loader
├── database/
│   ├── ai/sqlite.py                SQLite connection 與 schema 初始化
│   └── repository/                 各功能資料存取層
├── utils/                           權限、錯誤、格式化與共用工具
├── tests/                           pytest 測試
└── docs/DEVELOPMENT.md              本文件
```

## 指令與權限

### 權限實作原則

敏感 Slash Command 同時使用：

- `app_commands.default_permissions(...)`：Discord 側的預設可用性。
- `app_commands.checks.has_permissions(...)`：Bot 執行期強制授權。
- `app_commands.checks.bot_has_permissions(...)`：在動作前檢查 Bot 自身權限。

`default_permissions` 可被 Discord 伺服器的指令覆寫調整，不可單獨當作安全邊界。伺服器專用指令應使用 `guild_only`。Owner Prefix Command 使用 `commands.is_owner()`；Owner Slash Command 委派 `bot.is_owner()`，並支援 Team 擁有的 Discord Application。

### 公開 Slash Commands

| 指令 | 說明 | 使用環境／條件 |
|---|---|---|
| `/ping` | 顯示 WebSocket 延遲 | 伺服器與私訊 |
| `/botinfo` | 顯示 Bot 狀態、伺服器數與上線時間 | 伺服器與私訊 |
| `/help` | 顯示已載入 Slash Commands 的分類選單 | 伺服器與私訊 |
| `/hi` | 問候 | 伺服器與私訊 |
| `/hyw` | 回覆「何意味」 | 伺服器與私訊 |
| `/ai <prompt> [model] [file1-3]` | AI 對話；使用者可選 Gemini、Flash 或 Gemma 三類模型池 | 伺服器與私訊；有冷卻與同一使用者並發鎖 |
| `/markdown <file>` | 將文件轉換為 Markdown 檔回傳 | 伺服器與私訊；受附件大小上限限制 |
| `/mc pearl ...` | Minecraft 珍珠炮 TNT 配置計算 | 伺服器與私訊 |
| `/favorite add <url>` | 加入 YouTube 單曲收藏 | 伺服器與私訊；不接受播放清單 |
| `/favorite list` | 開啟個人收藏面板 | 伺服器與私訊；播放需先加入語音頻道 |

### 音樂 Slash Commands

| 指令 | 說明 | 條件 |
|---|---|---|
| `/play <mode> <url>` | 播放 YouTube 單曲或歌單 | 使用者必須在一般語音頻道；不可無權移動有聽眾的 Bot |
| `/queue` | 顯示播放佇列與分頁 | 僅伺服器 |
| `/clear` | 清空佇列 | 需通過播放器控制檢查 |
| `/history` | 顯示最近播放記錄 | 僅伺服器 |
| `/leave` | 讓 Bot 離開語音頻道 | 需通過播放器控制檢查 |

暂停、繼續、跳過、循環、音量、移除與移動歌曲放在 `MusicControls` 與佇列面板，不另設 Slash Command。

### 伺服器設定

| 指令 | 所需權限 | 說明 |
|---|---|---|
| `/server welcome <channel>` | Administrator | 設定歡迎頻道 |
| `/server leave <channel>` | Administrator | 設定離開訊息頻道 |
| `/server log <channel>` | Administrator | 設定管理日誌頻道 |
| `/server autorole [role]` | Administrator | 設定或停用新成員自動身份組 |
| `/server ticket_category <category>` | Administrator | 設定工單類別 |
| `/server ticket_support [role]` | Administrator | 設定或停用工單支援身份組 |
| `/server info` | Manage Server | 查看伺服器設定 |
| `/server reset` | Administrator | 重置資料庫中的伺服器設定 |

### 管理指令

| 指令 | 所需權限 | 說明 |
|---|---|---|
| `/ban <member> [reason] [delete_days]` | Ban Members | 封禁成員，可刪除 0–7 天訊息 |
| `/unban <user_id>` | Ban Members | 用 Discord User ID 解除封禁 |
| `/kick <member> [reason]` | Kick Members | 踢出成員 |
| `/mute <member> [minutes] [reason]` | Moderate Members | Discord Timeout，最長時間受設定限制 |
| `/unmute <member>` | Moderate Members | 解除 Timeout |
| `/warn <member> [reason]` | Moderate Members | 增加警告記錄，可依設定私訊目標 |
| `/warnings <member>` | Moderate Members | 查看目標的警告記錄 |
| `/clear_warns <member>` | Administrator | 清除目標的全部警告 |
| `/purge [amount]` | Manage Messages | 刪除 1–100 則頻道訊息 |
| `/modlog` | Moderate Members | 查看最近 20 筆管理動作 |

`ban`、`kick`、`mute`、`unmute` 與 `warn` 會檢查自我操作、Bot 目標、管理者與目標的身份組階層。

### 訊息工具

| 指令 | 所需權限 | 說明 |
|---|---|---|
| `/say <content> [...]` | Manage Messages | Bot 以 `「user ID」説：內容` 代發；支援 3 個附件、圖片 URL 與回覆訊息 |
| `/embed [...]` | Manage Messages | 建立自訂 Embed |
| `/webhook <content> [...]` | Manage Webhooks | 以自訂名稱與頭像發送 Webhook 訊息 |
| `/typing` | Manage Messages | 在目前頻道開始持續 typing 指示 |
| `/typing_stop` | Manage Messages | 停止目前頻道的 typing 指示 |

這些指令僅限伺服器。`/say` 會阻止無 Mention Everyone 權限的使用者藉 Bot 觸發 `@everyone` 或大量身份組提及。`/webhook` 具有模擬顯示名與頭像的能力，Manage Webhooks 只應授予可信任的管理者。

### 工單

| 指令 | 所需權限／條件 | 說明 |
|---|---|---|
| `/ticket open [topic]` | 公開；受冷卻與每人上限限制 | 建立私密工單頻道 |
| `/ticket close` | 工單建立者、支援身份組或 Manage Channels | 封存或刪除目前工單 |
| `/ticket add <member>` | Moderate Members | 將成員加入目前工單 |
| `/ticket remove <member>` | Moderate Members | 將成員移出目前工單 |
| `/ticket stats` | Moderate Members | 顯示伺服器工單統計 |
| `/ticket panel` | Administrator | 發送持久化的工單建立面板 |

### 身份組面板

| 指令 | 所需權限 | 說明 |
|---|---|---|
| `/roles panel [title] [description]` | Manage Roles | 建立面板 |
| `/roles add <message_id> <role> [...]` | Manage Roles | 新增身份組按鈕，單一面板最多 25 個 |
| `/roles remove <message_id> <role>` | Manage Roles | 移除身份組按鈕 |
| `/roles delete <message_id>` | Administrator | 刪除面板訊息與資料庫記錄 |
| `/roles list` | Manage Roles | 列出伺服器面板 |

面板不允許 `@everyone`、整合管理身份組、不低於 Bot 的身份組，或不低於操作者的身份組。面板資料與操作伺服器 ID 必須一致。

### 臨時語音頻道

| 指令 | 所需權限／條件 | 說明 |
|---|---|---|
| `/vc setup <channel> [category] [template] [limit]` | Administrator | 設定 Join-to-Create 觸發頻道 |
| `/vc name <name>` | 臨時頻道擁有者 | 改名 |
| `/vc limit <0-99>` | 臨時頻道擁有者 | 設定人數上限 |
| `/vc lock` / `/vc unlock` | 臨時頻道擁有者 | 鎖定或解鎖 |
| `/vc permit <member>` | 臨時頻道擁有者 | 允許成員加入 |
| `/vc reject <member>` | 臨時頻道擁有者 | 拒絕成員，若已在頻道內則移出 |
| `/vc kick <member>` | 臨時頻道擁有者 | 將成員移出 |
| `/vc transfer <member>` | 臨時頻道擁有者 | 轉移擁有權，目標必須在頻道內 |
| `/vc info` | 伺服器成員 | 顯示目前臨時頻道資訊 |
| `/vc forcedelete <channel>` | Administrator | 強制刪除臨時頻道 |

### Owner Slash Commands

| 指令 | 說明 |
|---|---|
| `/reply [content] [user_id]` | 回覆指定使用者，或最近私訊 Bot 的使用者 |
| `/talk <user> <content> [image]` | 主動私訊指定使用者 |

兩者均使用 `bot.is_owner()` 執行期驗證。

### Owner Prefix Commands

預設前綴為 `$`，可用 `bot.command_prefix` 調整。以下指令全部受 `commands.is_owner()` 保護：

| 指令 | 說明 |
|---|---|
| `$help` | 顯示已載入的 Prefix Commands |
| `$game [type] <text>` | 設定並持久化 Bot presence |
| `$slash` | 同步全域 Slash Commands |
| `$slash_guild` | 清除目前伺服器殘留的重複 Slash Commands |
| `$load <extension>` | 載入 Extension |
| `$unload <extension>` | 卸載 Extension |
| `$reload <extension>` | 重載 Extension |
| `$bot_reload` | 重載所有已載入 Extension |
| `$bot_stop` | 安全關閉 Bot |
| `$settings` | 顯示 settings 子指令 |
| `$settings show [section]` | 顯示全部設定或指定 section |
| `$settings reload` | 強制重載 `settings.json` 並更新 presence |
| `$status <presence> <type> <text>` | 設定並持久化 Bot 的在線與活動狀態 |
| `$status_show` | 顯示目前 Bot 狀態設定 |
| `$musicstatus` | 顯示所有伺服器的音樂播放狀態 |
| `$tier <member> <0-3>` | 設定 AI 社交等級 |
| `$ban <member> [reason]` | 禁止成員使用 AI；不是 Discord 封禁 |
| `$unban <member>` | 解除 AI 使用禁止 |
| `$unrestrict <member>` | 解除 abuse guard 的暫時限制 |
| `$記憶 <keyword> <importance> '<content>'` | 建立 1–5 重要度的全域 AI 記憶；別名 `$memory` |
| `$刪記憶 <keyword>` | 刪除全域記憶；別名 `$delmemory`、`$memorydel` |
| `$社交` | 顯示所有 AI 社交資料；別名 `$social` |
| `$info [member]` | 顯示 AI 用量統計 |
| `$info summary <member>` | 強制產生指定成員的對話摘要 |
| `$dashboard` / `$db` | AI 系統總覽 |
| `$dashboard user` | 30 天 Token 用量排行 |
| `$dashboard cache` | 查看並清理搜尋快取 |
| `$dashboard state` | 顯示非 normal 對話狀態 |
| `$dashboard clear <member>` | 清除成員對話狀態 |
| `$dashboard prompt` | 列出 prompt 模板 |
| `$dashboard set <name>` | 啟用 prompt 模板 |
| `$dashboard off` | 停用自訂模板 |
| `$dashboard del <name>` | 刪除 prompt 模板 |
| `$dashboard audit` | 顯示最近的 AI 管理審計記錄 |
| `$dashboard rules [reload]` | 顯示或重載內容審核規則 |

## 設定系統

### 分工

- `.env` / `config.py`：Token、API Key、Owner ID、DB 路徑與 Extension 掃描範圍。
- `settings.json` / `core/system/settings.py`：非機密、可調整的行為參數。
- 伺服器專屬設定：儲存於 SQLite，由 `/server ...` 管理。

`settings.py` 依 `settings.json` 的 mtime 自動重載，通常在下一次讀取時就會生效。如需立即驗證，可執行 `$settings reload`。無效 JSON 不會覆蓋最後一份成功載入的快取。

### `settings.json` 主要設定

| Section | 重要欄位 |
|---|---|
| `bot` | `command_prefix`、`status_type`、`status_text`、`presence`、`startup_timeout` |
| `ai` | 預設模型、冷卻、回覆長度、附件上限、人設、記憶/摘要門檻、搜尋快取、abuse guard 與告警門檻 |
| `music` | 佇列上限、閒置斷線、音量、FFmpeg 路徑、語音連線逾時、健康檢查、收藏分頁 |
| `ticket` | 頻道前綴、類別名稱、封存類別、冷卻與每人上限 |
| `voice_channel` | 預設頻道名稱、人數上限、JTC 頻道 ID 與類別 ID |
| `guild` | 歡迎與離開訊息樣板 |
| `moderation` | 預設/最長 Timeout 分鐘、警告與 Timeout 私訊通知 |
| `embed_footer` | 一般與音樂 Embed 頁腳 |
| `dm` | 私訊轉發快取上限與 Owner 回覆前綴 |
| `link_preview` | 啟用狀態、單則上限、快取、HTTP 逾時、摘要關鍵字/長度、SSRF 讀取上限、平台代理網域 |

歡迎/離開樣板支援 `{user}`、`{username}`、`{guild}` 與 `{count}`。Presence `status_type` 實際支援 `playing`、`listening`、`watching` 與 `competing`；其他值會回退到 `listening`。

`link_preview.bilibili_fetch_video` 目前是相容性欄位：現行 Bilibili 流程只解析 `b23.tv` redirect 與 BVID，產生 `vxbilibili.com` 修復連結，不呼叫 Bilibili API，也不下載影片。

## 核心子系統

### AI

`cogs/ai/ai_command.py` 與 `cogs/ai/chat.py` 是兩個入口，共用：

- `request_guard`：每使用者並發鎖與冷卻。
- `attachment_utils` 與 `file_parser/`：附件讀取、媒體分類與內容提取。
- `agent_router`：模型選擇與手動 override。
- `content_guard` / `abuse_guard`：內容審核、使用速率與暫時限制。
- `memory_manager`、`context_manager` 與 `user_context`：短期上下文、長期記憶、摘要與社交狀態。
- `budget`：記錄請求、Token 與錯誤。

AI 使用 Gemini 串流產生回覆，首段文字到達後立即發送，後續依
`ai.stream_update_interval_seconds` 節流更新同一則 Discord 訊息。回覆超過
`ai.max_reply_length` 時會以 `.txt` 附件回傳。單一模型的閒置逾時由
`ai.model_timeout_seconds` 控制，每次請求最多嘗試 `ai.max_model_attempts` 個模型。

上下文預設取回 100 筆歷史候選訊息與 10 筆最近訊息；對應設定為
`ai.message_candidate_limit` 與 `ai.recent_message_limit`。附件會以
`ai.attachment_concurrency` 為上限平行下載與解析。

### 音樂

`core/music/service.py` 管理每伺服器的 Player。`Player` 負責連線、播放、閒置計時與健康檢查；`Queue` 負責佇列、循環與歷史；`Song` 使用 yt-dlp 解析 YouTube；`views.py` 實作互動控制與權限驗證。

Player 會避免一般成員將仍有聽眾的 Bot 移到其他語音頻道。佇列上限、連線逾時與閒置斷線均可設定。

### 連結預覽與網頁摘要

`cogs/events/link_preview.py` 只處理伺服器中非 Bot 訊息：

1. `detector.py` 以 hostname 邊界偵測支援平台。
2. Bilibili 解析 BVID，必要時追蹤 `b23.tv` redirect，再回覆 `vxbilibili.com` 修復連結。
3. Instagram、Threads、Twitter/X 與 TikTok 依序嘗試設定中的代理網域；Pinterest 解析頁面 metadata。
4. 其他平台轉為 `LinkPreview`，必要時用 Gemma 摘要後組成 Embed。
5. `摘要 <url>` 會經 `url_guard.py` 做 SSRF 防護，拒絕內網、loopback 與不安全目標。

代理服務由第三方維運，單一網域失效是可預期情況。`fallback.py` 會記錄暫時失敗並調整嘗試順序。

### 持久化 Discord Views

工單關閉按鈕、工單建立面板與身份組面板使用 `timeout=None` 與穩定 `custom_id`。Bot 重啟後，Cog 會重新註冊 View；身份組面板會從 SQLite 重建按鈕。

## 資料庫

`DB_PATH` 預設為 `database/ai/memory.db`。各 Repository 負責：

| Repository | 資料 |
|---|---|
| `user_repository.py` | AI 使用者與狀態 |
| `memory_repository.py` | 記憶、摘要與向量相關資料 |
| `audit_repository.py` | AI 管理操作審計 |
| `favorites_repository.py` | 個人音樂收藏 |
| `guild_repository.py` | 伺服器頻道、自動身份組與工單設定 |
| `mod_repository.py` | 警告與管理動作 |
| `ticket_repository.py` | 工單狀態 |
| `vc_repository.py` | Join-to-Create 設定與臨時頻道擁有者 |

多數會在 async Discord handler 中頻繁呼叫的同步 SQLite 函式應使用 `utils.async_db.to_thread`，避免阻塞 event loop。

備份前建議停止 Bot，或使用 SQLite 的一致性備份機制；不要在 Bot 寫入時只複製主資料庫檔而忽略可能的 journal/WAL。

## 開發 Cog

### 基本範本

```python
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


class Example(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="example", description="範例指令")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(send_messages=True)
    async def example(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("完成", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Example(bot))
```

### 規約

- 每個可載入模組必須提供 `async def setup(bot)`。
- 檔名不可以 `_` 開頭；`__init__.py` 不會當作 Extension 載入。
- 開發期可用 `$reload cogs.example` 重載，或使用短名。
- 新增/刪除 Slash Command 後要執行 `$slash`；若同一指令出現兩次，在該伺服器執行 `$slash_guild` 清除舊的伺服器版。
- 互動可能超過 3 秒時，先 `interaction.response.defer()`，之後使用 `interaction.followup.send()`。
- 必須處理 Discord 的 2,000 字訊息、Embed field 1,024 字、25 fields 與 View 25 components 限制。
- 後台 task 必須在 `cog_unload()` 中 cancel 與清理。
- 持久化 View 必須使用 `timeout=None` 與固定 `custom_id`，並在重啟時重新註冊。
- 使用者網址的主機端請求必須先經 SSRF 驗證。
- 使用 `logging.getLogger("bot.<area>")`，不要用 `print()` 處理運行期錯誤。
- 新的非機密設定要同時加入 `settings.json` 與 `core/system/settings.py` 的 `_DEFAULTS`。
- 敏感指令的 Discord 預設權限與 Bot 執行期權限必須一致。

## 測試與檢查

### pytest

```bash
python -m pytest -q
```

測試不依賴 `pytest-asyncio`；async 案例使用 `asyncio.run()`。`tests/conftest.py` 負責測試時的環境與 import path。

權限回歸測試會掃描所有 Cog，確保每個使用 `default_permissions` 的指令同時具有執行期 `has_permissions`。

### 語法與靜態檢查

```bash
python -m compileall -q cogs core database utils tests
ruff check .
mypy .
```

Ruff 與 mypy 規則位於 `pyproject.toml`。若目前環境沒有這些工具，需另行安裝。

## 維運與故障排除

### Slash Command 未出現或仍顯示舊指令

1. 確認邀請 scopes 包含 `applications.commands`。
2. 查看啟動日誌的 Cog 載入與 Slash 同步結果。
3. 執行 `$slash` 同步全域指令；若 Discord 選單有重複項目，再於該伺服器執行 `$slash_guild`。
4. 全域同步不一定立即出現，可重開 Discord 客戶端再查看。

### Cog 載入失敗

- 執行 `python -m compileall -q cogs` 查找語法錯誤。
- 確認模組有 `async def setup(bot)`。
- 確認 import 使用專案根目錄為基準，且沒有缺少 dependency。
- 查看是否被 `EXTENSION_BLACKLIST` 或 `EXCLUDED_DIRS` 排除。

### 權限不足

- 使用者需同時符合 Slash Command 執行期權限與 Discord 頻道 overwrite。
- Bot 需擁有對應權限，且身份組階層必須高於管理目標。
- 指令在選單中被隱藏，可能是 Discord 伺服器的指令覆寫或 `default_permissions` 造成。
- 指令看得到但執行被拒絕，代表執行期 check 未通過。

### 音樂無法播放

- 確認 `ffmpeg -version` 可執行，或設定 `music.ffmpeg_path`。
- 確認 Bot 在語音頻道具有 Connect 與 Speak。
- 確認 PyNaCl 已安裝。
- yt-dlp 與 YouTube 行為會變動，解析失敗時先更新 yt-dlp 並查看 log。

### AI 無法回覆

- 確認 `GEMINI_API` 已設定且有效。
- 確認模型名稱、配額與 Google API 回應。
- 查看使用者是否被 `$ban` 永久封鎖或 abuse guard 暫時限制。
- 使用 `$dashboard`、`$dashboard audit` 與 log 定位問題。

### 連結預覽無反應

- 確認 `link_preview.enabled` 為 `true`。
- Bilibili 短連結需能夠追蹤 `b23.tv` redirect。
- Instagram、Threads、Twitter/X 與 TikTok 依賴第三方代理；若全部候選網域失效，功能會略過該連結。
- `摘要 <url>` 會拒絕私有 IP、localhost、不安全 redirect 或過大回應。

### 日誌

LogManager 會將日誌輸出至終端與輪替檔案。實際目錄、檔名、大小與保留數由 `core/logging/constants.py` 定義。ERROR 級別可透過 DiscordErrorHandler 通知 Owner，關機時會儘量傳送本次 session 摘要。

## 安全原則

- `.env`、SQLite 資料庫、log 與使用者上傳的暫存內容不應提交到 Git。
- Token 或 API Key 如曾出現在 log、commit、螢幕擷圖或聊天訊息中，應立即輪換。
- 新的管理指令不得只使用 `default_permissions`。
- 所有以伺服器、頻道或訊息 ID 查詢的資料必須再核對 guild ID，避免跨伺服器操作。
- 刪除、封鎖、轉移與代發要有可追溯的執行者資訊。
- Webhook 代發與 Owner 私訊能力屬於高信任功能，不應向一般身份組開放。
- 使用者提供的 URL、檔案與壓縮檔必須有 scheme、主機、容量、數量、解壓後大小與逾時上限。
- 開發者應使用最小權限測試帳號，不只用 Administrator 帳號驗證。

版本變更請以 Git commit 與 release notes 為準，不在本文件重複維護流水帳式 Changelog。
