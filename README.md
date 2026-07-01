# 流螢醬 Discord Bot

基於 discord.py 2.x 的多功能 Discord Bot，整合 AI 對話（Google Gemini）、音樂播放、伺服器管理、工單系統、臨時語音頻道等功能。

---

## 目錄

- [環境需求](#環境需求)
- [快速啟動](#快速啟動)
- [設定檔說明](#設定檔說明)
- [專案結構](#專案結構)
- [Slash 指令一覽](#slash-指令一覽)
- [Prefix 指令一覽（$）](#prefix-指令一覽)
- [權限對照表](#權限對照表)
- [已知問題與排除](#已知問題與排除)
- [Changelog](#changelog)

---

## 環境需求

- Python 3.11 以上
- FFmpeg（音樂播放）
- SQLite（內建）

```
pip install -r requirements.txt
```

---

## 快速啟動

1. 複製 `.env.example` 為 `.env` 並填入 Token：

```env
TOKEN=你的_Discord_Bot_Token
GEMINI_API=你的_Google_Gemini_API_Key
OWNER_ID=你的_Discord_使用者_ID
DB_PATH=database/bot.db
```

2. 啟動：

```bash
python bot.py
```

---

## 設定檔說明

所有可調整的行為參數均存於 `settings.json`，重要欄位如下：

| 路徑 | 說明 | 預設值 |
|------|------|--------|
| `bot.command_prefix` | Prefix 指令前綴 | `$` |
| `bot.status_type` | 狀態類型（playing/listening/watching/competing） | `listening` |
| `bot.status_text` | 狀態顯示文字 | `/play \| @我` |
| `music.max_queue_size` | 播放佇列上限 | `200` |
| `music.idle_timeout_seconds` | 閒置自動斷線秒數 | `180` |
| `music.favorites_per_page` | 收藏清單每頁顯示數 | `10` |
| `music.favorites_load_all_limit` | 一次載入全部收藏的上限 | `50` |
| `moderation.dm_target_on_warn` | 警告時是否私訊被警告者 | `true` |
| `dm.forward_map_limit` | 私訊橋接記憶筆數上限 | `200` |
| `dm.recent_senders_limit` | /reply 最近私訊者追蹤筆數 | `200` |

執行 `$settings reload` 即可熱更新，無需重啟 Bot。

---

## 專案結構

```
bot/
├── bot.py                    # Bot 主體入口，含全域錯誤處理器
├── config.py                 # 環境變數讀取
├── settings.json             # 可熱更新的行為設定
├── startup.py                # 同步初始化（DB 建表）
│
├── cogs/                     # Discord Extension（Cog）
│   ├── ai/
│   │   ├── ai_owner_commands.py   # AI 系統管理（Owner）
│   │   ├── chat.py                # @mention AI 聊天入口
│   │   ├── dashboard.py           # AI 管理面板
│   │   └── info.py                # AI 使用說明
│   ├── events/
│   │   ├── message.py             # 私訊轉發 & Owner 回覆橋接
│   │   └── status.py              # Bot 狀態管理
│   ├── guild/
│   │   └── guild_settings.py      # 伺服器設定指令群組
│   ├── minecraft/
│   │   └── mc_commands.py         # Minecraft 工具（珍珠炮計算機）
│   ├── moderation/
│   │   └── mod.py                 # 伺服器管理指令
│   ├── music/
│   │   └── music.py               # 音樂播放指令
│   ├── roles/
│   │   └── role_management.py     # 身份組面板管理
│   ├── system/
│   │   ├── load.py                # Extension 載入管理
│   │   ├── monitor.py             # 系統監控背景任務
│   │   ├── owner.py               # Owner 系統指令
│   │   └── settings_cmd.py        # settings.json 管理
│   ├── talk/
│   │   ├── embed.py               # Embed 建構器
│   │   ├── say.py                 # Bot 代發訊息
│   │   ├── typing_indicator.py    # 輸入中指示器
│   │   └── webhook.py             # Webhook 發話
│   ├── ticket/
│   │   └── ticket.py              # 工單系統
│   ├── utility/
│   │   ├── favorites.py           # 音樂收藏清單
│   │   └── general.py             # 一般工具指令
│   └── voice/
│       └── voice_channel.py       # 臨時語音頻道（JTC）
│
├── core/                     # 業務邏輯核心（不含 Discord 直接依賴）
│   ├── ai/                        # AI 推論、上下文、記憶、限速
│   ├── logging/                   # 統一日誌設定
│   ├── minecraft/                 # 珍珠炮計算引擎
│   ├── music/                     # 音樂播放器引擎
│   └── system/                    # Settings、Extension Loader
│
├── database/                 # SQLite 資料存取層（Repository Pattern）
│   ├── ai/sqlite.py
│   └── repository/
│       ├── audit_repository.py
│       ├── favorites_repository.py
│       ├── guild_repository.py
│       ├── memory_repository.py
│       ├── mod_repository.py
│       ├── ticket_repository.py
│       ├── user_repository.py
│       └── vc_repository.py
│
└── utils/                    # 跨模組工具
    ├── checks.py                  # 權限 check 工廠
    ├── discord_errors.py          # Discord 錯誤代碼轉換
    ├── formatter.py               # 格式化工具（時長等）
    ├── helpers.py                 # 通用輔助函式
    └── owner_resolver.py          # Bot Owner ID 解析（含 Team 支援）
```

---

## Slash 指令一覽

### AI

| 指令 | 說明 | 所需權限 |
|------|------|----------|
| _(mention)_ | @提及 Bot 開始 AI 對話 | 無 |

AI Owner（Prefix 指令，見下方）

---

### 一般工具

| 指令 | 說明 | 所需權限 |
|------|------|----------|
| `/ping` | 顯示 Bot 的 WebSocket 延遲 | 無 |
| `/botinfo` | 顯示 Bot 版本、延遲、上線時間等資訊 | 無 |
| `/help` | 分頁瀏覽所有 Slash 指令清單 | 無 |
| `/hi` | 向 Bot 打招呼 | 無 |
| `/hyw` | 何意味 | 無 |

---

### 音樂播放

| 指令 | 說明 | 所需權限 |
|------|------|----------|
| `/play <query>` | 播放歌曲（YouTube URL 或關鍵字搜尋） | 無（需在語音頻道） |
| `/playlist <url>` | 播放整個 YouTube 播放清單 | 無（需在語音頻道） |
| `/pause` | 暫停播放 | 無 |
| `/resume` | 繼續播放 | 無 |
| `/skip` | 跳過目前歌曲 | 無 |
| `/stop` | 停止播放並清空佇列 | 無 |
| `/leave` | 讓 Bot 離開語音頻道 | 無 |
| `/nowplaying` | 顯示目前播放中的歌曲資訊 | 無 |
| `/queue` | 查看目前播放佇列 | 無 |
| `/shuffle` | 隨機打亂佇列 | 無 |
| `/loop [mode]` | 切換循環模式（off / single / queue） | 無 |
| `/volume <0-100>` | 調整音量百分比 | 無 |
| `/remove <index>` | 移除佇列中指定位置的歌曲 | 無 |
| `/move <from> <to>` | 移動佇列中歌曲的順序 | 無 |
| `/clear` | 清空播放佇列（不停止目前播放） | 無 |
| `/history` | 查看最近 10 首播放紀錄 | 無 |
| `/musicstatus` | 查看所有伺服器的音樂播放狀態 | 管理員 |

---

### 音樂收藏清單

| 指令 | 說明 | 所需權限 |
|------|------|----------|
| `/fav menu` | 開啟互動選單（取得清單、加入、載入、刪除） | 無 |
| `/fav add [query]` | 加入收藏：提供網址或關鍵字直接加入；省略則加入目前播放中的歌曲 | 無 |
| `/fav list` | 查看個人收藏清單（分頁瀏覽） | 無 |
| `/fav play <index>` | 從收藏清單播放指定編號的歌曲 | 無（需在語音頻道） |
| `/fav remove <index>` | 從收藏清單移除指定編號的歌曲 | 無 |
| `/fav clear` | 清空全部個人收藏 | 無 |

互動選單（`/fav menu`）提供以下操作：
1. 取得最愛歌曲清單
2. 加入指定歌曲（URL 或關鍵字）
3. 載入指定的最愛歌曲
4. 載入全部的最愛歌曲
5. 從最愛清單刪除指定歌曲

---

### 伺服器管理（Moderation）

| 指令 | 說明 | 所需權限 |
|------|------|----------|
| `/ban <member> [reason] [delete_days]` | 封禁成員 | 封禁成員 |
| `/unban <user_id>` | 解除封禁 | 封禁成員 |
| `/kick <member> [reason]` | 踢出成員 | 踢出成員 |
| `/mute <member> [minutes] [reason]` | 禁言成員（Discord Timeout） | 管理成員 |
| `/unmute <member>` | 解除成員禁言 | 管理成員 |
| `/warn <member> [reason]` | 對成員發出警告並記錄 | 管理成員 |
| `/warnings <member>` | 查看成員的警告紀錄 | 管理成員 |
| `/clear_warns <member>` | 清除成員所有警告紀錄 | 管理員 |
| `/purge [amount]` | 批量刪除頻道訊息（1-100 則） | 管理訊息 |
| `/modlog` | 查看最近 20 筆管理動作紀錄 | 管理成員 |

---

### 伺服器設定

| 指令 | 說明 | 所需權限 |
|------|------|----------|
| `/server log <channel>` | 設定管理動作日誌頻道 | 管理員 |
| `/server welcome <channel> [message]` | 設定成員加入歡迎訊息頻道 | 管理員 |
| `/server leave <channel> [message]` | 設定成員離開通知頻道 | 管理員 |
| `/server autorole <role>` | 設定新成員自動獲得的身份組 | 管理員 |
| `/server ticket_category <category>` | 設定工單頻道所在分類 | 管理員 |
| `/server ticket_support <role>` | 設定工單支援身份組 | 管理員 |
| `/server reset` | 重置所有伺服器設定為預設值 | 管理員 |

---

### 工單系統

| 指令 | 說明 | 所需權限 |
|------|------|----------|
| `/ticket open [topic]` | 開啟新工單（建立私人頻道） | 無 |
| `/ticket close` | 關閉目前工單（封存或刪除頻道） | 無（支援身份組或本人） |
| `/ticket add <member>` | 將成員加入工單頻道 | 管理成員 |
| `/ticket remove <member>` | 從工單頻道移除成員 | 管理成員 |
| `/ticket stats` | 查看伺服器工單統計 | 管理成員 |
| `/ticket panel` | 在目前頻道發送「建立工單」按鈕面板 | 管理員 |

---

### 身份組面板

| 指令 | 說明 | 所需權限 |
|------|------|----------|
| `/roles panel [title] [description]` | 在目前頻道建立身份組自助領取面板 | 管理身份組 |
| `/roles add <message_id> <role> [label] [emoji] [description] [style]` | 新增身份組按鈕至指定面板 | 管理身份組 |
| `/roles remove <message_id> <role>` | 從面板移除指定身份組按鈕 | 管理身份組 |
| `/roles delete <message_id>` | 刪除整個身份組面板 | 管理員 |
| `/roles forcedelete <message_id>` | 強制刪除面板（含 DB 紀錄） | 管理員 |

---

### 臨時語音頻道（JTC）

| 指令 | 說明 | 所需權限 |
|------|------|----------|
| `/vc setup <channel>` | 設定「加入即建立」觸發頻道 | 管理員 |
| `/vc name <name>` | 更改自己臨時頻道的名稱 | 無（需為頻道擁有者） |
| `/vc limit <0-99>` | 設定自己頻道的人數上限（0 為無限制） | 無（需為頻道擁有者） |
| `/vc lock` | 鎖定頻道，阻止其他成員加入 | 無（需為頻道擁有者） |
| `/vc unlock` | 解除頻道鎖定 | 無（需為頻道擁有者） |
| `/vc permit <member>` | 允許指定成員進入已鎖定的頻道 | 無（需為頻道擁有者） |
| `/vc reject <member>` | 禁止指定成員進入此頻道 | 無（需為頻道擁有者） |
| `/vc kick <member>` | 將成員踢出此語音頻道 | 無（需為頻道擁有者） |
| `/vc transfer <member>` | 將頻道所有權轉移給另一位成員 | 無（需為頻道擁有者） |
| `/vc info` | 查看目前頻道的設定與擁有者資訊 | 無 |

---

### 訊息工具

| 指令 | 說明 | 所需權限 |
|------|------|----------|
| `/say <content> [image_url] [message_id] [image1-3]` | 以 Bot 身份在目前頻道發送訊息 | 管理訊息 |
| `/embed [title] [description] [color] [author] [footer] [thumbnail] [image_url] [message_id]` | 發送自訂 Embed 訊息 | 管理訊息 |
| `/webhook <content> [username] [avatar_url] [image_url] [message_id] [image1-3]` | 以 Webhook 自訂名稱與頭像發話 | 管理 Webhook |
| `/typing` | 讓 Bot 在目前頻道持續顯示「正在輸入...」 | 管理訊息 |
| `/typing_stop` | 停止輸入指示器 | 管理訊息 |

---

### Minecraft 工具

| 指令 | 說明 | 所需權限 |
|------|------|----------|
| `/mc pearl <px> <py> <pz> <dest_x> <dest_z> [ground_height]` | 珍珠炮計算機：輸入 84gt 時的珍珠座標與目標，輸出前 10 個 TNT 配置方案 | 無 |

---

### Owner 專用 Slash 指令

以下指令僅限 Bot 擁有者（`OWNER_ID`）執行：

| 指令 | 說明 |
|------|------|
| `/reply [content] [user_id]` | 以 Bot 身份私訊最近一筆私訊者，或指定 user_id 的使用者 |
| `/talk <user> <content> [image]` | 讓 Bot 主動私訊指定使用者 |

---

## Prefix 指令一覽

預設前綴為 `$`，可在 `settings.json` 的 `bot.command_prefix` 調整。

### 系統管理（Owner 專用）

| 指令 | 說明 |
|------|------|
| `$game [type] <text>` | 設定 Bot 的活動狀態（遊玩 / 收聽 / 觀看 / 競賽），並持久化至 settings.json |
| `$slash` | 將 Slash Commands 同步至全域（最多 1 小時生效） |
| `$slash_guild` | 即時將 Slash Commands 同步至目前伺服器（測試用） |
| `$settings` | 顯示 settings.json 管理子指令清單 |
| `$settings show [section]` | 顯示 settings.json 的設定值（可指定區塊） |
| `$settings reload` | 強制重新載入 settings.json 並套用 Bot 狀態 |
| `$load <extension>` | 載入指定 Cog extension（支援逗號分隔多個） |
| `$unload <extension>` | 卸載指定 Cog extension |
| `$reload <extension>` | 重新載入指定 Cog extension |
| `$bot_reload` | 重新載入所有已載入的 Cog extension |
| `$bot_stop` | 安全關閉 Bot |

`$game` 用法範例：
```
$game 薩姆戰甲              → 使用目前設定的類型（預設 playing）
$game playing 薩姆戰甲      → 遊玩中：薩姆戰甲
$game listening /play | @我 → 收聽中：/play | @我
$game watching you          → 觀看：you
```

`$settings show` 可用 section：
`bot` / `ai` / `music` / `ticket` / `voice_channel` / `guild` / `moderation` / `embed_footer`

### AI 系統管理（Owner 專用）

| 指令 | 說明 |
|------|------|
| `$tier <user_id> [level]` | 查看或設定使用者的 AI 互動階層（0-5） |
| `$ban <user_id> [reason]` | 禁止使用者使用 AI 功能 |
| `$unban <user_id>` | 解除 AI 功能封禁 |
| `$unrestrict <user_id>` | 解除使用者的濫用限制 |
| `$記憶 <keyword> <content>` | 設定全域記憶（別名：`$memory`） |
| `$刪記憶 <keyword>` | 刪除全域記憶（別名：`$delmemory`、`$memorydel`） |
| `$社交` | 輸出所有使用者的社交檔案（別名：`$social`） |
| `$dashboard` | 顯示 AI 系統狀態（別名：`$db`） |
| `$info` | 顯示 AI 使用說明 |

AI 使用者階層說明：

| 等級 | 名稱 | 說明 |
|------|------|------|
| 0 | 陌生人 | 初次互動，基礎功能 |
| 1 | 朋友 | 互動達到一定次數後升級 |
| 2 | 好友 | 長期互動使用者 |
| 3 | 摯友 | 高度信任使用者 |
| 4 | 開拓者 | 進階功能開放 |
| 5 | 管理員 | 最高階層 |

---

## 權限對照表

| Discord 權限 | 對應指令 |
|-------------|----------|
| 無（任何人） | /ping /botinfo /help /hi /hyw /play /pause /resume /skip /stop /leave /nowplaying /queue /shuffle /loop /volume /remove /move /clear /history /fav* /ticket open /vc name /vc limit /vc lock /vc unlock /vc permit /vc reject /vc kick /vc transfer /vc info |
| 管理訊息 | /say /embed /typing /typing_stop |
| 管理 Webhook | /webhook |
| 管理成員 | /mute /unmute /warn /warnings /purge /modlog /ticket add /ticket remove /ticket stats |
| 管理員 | /ban /unban /kick /clear_warns /server* /ticket panel /roles delete /roles forcedelete /musicstatus /server reset /vc setup |
| 管理身份組 | /roles panel /roles add /roles remove |
| Bot 擁有者 | /reply /talk 及所有 $ 指令 |

---

## 已知問題與排除

### 私訊轉發不工作

檢查以下項目：

1. `.env` 中 `OWNER_ID` 是否正確填寫
2. Bot 與 Owner 是否有共同的伺服器（缺少共同伺服器時無法傳送 DM）
3. Owner 是否已開啟「接受伺服器成員的私訊」

---

### embedding 模型 404 錯誤

log 中可能出現：
```
embed error: 404 NOT_FOUND. models/text-embedding-004 is not found for API version v1beta
```

這是 Google Gemini SDK 預設使用 v1beta 端點，而 `text-embedding-004` 需要 v1 端點。此錯誤已被靜默處理，不影響 AI 對話功能，僅影響語意記憶搜尋的準確度。若需修復，需在 `core/ai/gemini_client.py` 為 embedding 建立使用 v1 端點的獨立 Client。

---

### Gemini 500 錯誤 / 超時

偶發的上游 API 錯誤，已實作自動重試（最多 3 次）與模型 fallback 機制：
- 主要模型失敗 → 自動降級至備用模型
- 連備用模型也失敗 → 回覆使用者錯誤訊息

---

## Changelog

### 修正項目

**bot.py**
- 新增 `CustomCommandTree`：slash 指令的全域錯誤處理器，正確回應 `MissingPermissions`、`CheckFailure`、`CommandOnCooldown` 等例外，取代原本使用者只看到「互動未能回應」的行為
- 新增 `on_command_error`：prefix 指令的全域錯誤處理器，`CommandNotFound` 靜默忽略，其餘例外回覆可讀訊息

**cogs/utility/general.py — /help**
- 修正：embed fields 超過 Discord 25 個上限（共 43 個指令）導致 400 error 50035
- 新增 `HelpView` 分頁瀏覽器，每頁最多 20 個指令

**cogs/minecraft/mc_commands.py — /mc pearl**
- 修正：10 筆計算結果（約 1224 字）超過 Discord embed field value 1024 字元上限
- 新增 `_split_results_to_fields()`：依實際字元數動態切分為多個 field

**cogs/moderation/mod.py — /warn**
- 修正：對 Bot 帳號執行 /warn 時觸發 `AttributeError: 'ClientUser' object has no attribute 'create_dm'`
- `_can_moderate()` 新增 `target.bot` 前置檢查
- DM 通知新增 `not member.bot` 守衛及 `AttributeError` 捕捉

**cogs/system/owner.py — /reply /talk**
- 修復：/reply 和 /talk 在重構過程中遺失，已重新整合
- /talk 錯誤訊息改用 `friendly_http_error()`，50007 等錯誤碼顯示可讀說明

**cogs/events/message.py**
- 修正：Owner 解析原本使用 `application_info().owner`，Team 擁有的應用程式會解析到錯誤對象，導致 DM 轉發靜默失敗
- 新增 `last_dm_user_id` property：改為獨立的 `_recent_senders` 追蹤（與轉發是否成功脫鉤），/reply 即使轉發失敗仍能找到目標

**cogs/talk/say.py / embed.py / typing_indicator.py / webhook.py**
- 修正：`@app_commands.checks.has_permissions` 改為 `@app_commands.default_permissions`
- 原本缺少 tree error handler 時，使用者無權限會看到「互動未能回應」而非說明訊息

**cogs/roles/role_management.py**
- 修正：`_build_panel_embed` 單一 field 在多個身份組時超過 1024 字元
- 新增 `_split_role_lines_to_fields()` 動態切分
- label 參數加上 80 字元上限（Discord 按鈕限制）
- 例外捕捉範圍擴展至 `discord.HTTPException`，涵蓋表情符號格式錯誤等情況

**cogs/system/load.py — $bot_reload**
- 修正：大量模組同時失敗時，組合錯誤訊息可能超過 Discord 2000 字元上限
- 新增 `_send_chunked()` 分段發送，單一例外字串截斷至 200 字元

**cogs/guild/guild_settings.py — /server reset**
- 修正：原本直接在 Cog 內執行原始 SQL，繞過 Repository 層
- 改呼叫 `guild_repo.reset_settings()`，SQL 集中於資料層維護

**utils/owner_resolver.py**（新增）
- 集中式 Bot Owner ID 解析，正確處理 Team 擁有的應用程式

**utils/discord_errors.py**（新增）
- Discord 錯誤代碼（50007 等）轉換為繁體中文說明

**utils/checks.py**
- `owner_only()` 與 `slash_owner_only()` 改為委派 `bot.is_owner()`，正確處理 Team 應用程式

**cogs/utility/favorites.py — /fav**
- 新增 `/fav add <query>`：直接提供網址或關鍵字加入收藏，不再要求「必須正在播放中」
- 新增 `/fav menu`：互動選單，整合取得清單、加入、載入單曲、載入全部、刪除
- 新增「載入全部收藏」功能，批次上限由 `settings.json` 控制

**database/repository/guild_repository.py**
- 新增 `reset_settings()` 函式，供 Cog 層呼叫取代直接 SQL 操作

**settings.json**
- 新增 `music.favorites_per_page`（預設 10）
- 新增 `music.favorites_load_all_limit`（預設 50）
- 新增 `dm.recent_senders_limit`（預設 200）
