# 流螢醬 Discord Bot

基於 discord.py 2.x 的多功能 Discord Bot，整合 AI 對話（Google Gemini）、音樂播放、伺服器管理、工單系統、臨時語音頻道、連結預覽等功能。

---

## 目錄

- [環境需求](#環境需求)
- [快速啟動](#快速啟動)
- [設定檔說明](#設定檔說明)
- [專案結構](#專案結構)
- [連結預覽（Bilibili／Instagram／Threads／Pinterest／關鍵字摘要）](#連結預覽biliblili instagram threads pinterest 關鍵字摘要)
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

> 連結預覽功能新增以下依賴，若尚未加入 `requirements.txt` 請一併補上：
> `httpx`（非同步 HTTP 請求）、`google-genai`（Gemma 摘要生成，與既有 AI 對話功能共用同一套 SDK，不需額外申請金鑰）。純文字擷取（Pinterest／關鍵字摘要）採輕量正規表示式解析，不需要額外安裝 BeautifulSoup 或 lxml。

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
| `link_preview.enabled` | 是否啟用連結預覽功能（含被動預覽與關鍵字摘要） | `true` |
| `link_preview.max_embeds_per_message` | 單一訊息最多處理幾個被動預覽連結 | `3` |
| `link_preview.cache_size` | 連結預覽結果快取筆數上限 | `200` |
| `link_preview.request_timeout_seconds` | 對外請求逾時秒數 | `10` |
| `link_preview.embed_description_max_chars` | Embed 內文最大字數（超過會截斷並加上刪節號） | `800` |
| `link_preview.summary_trigger_min_chars` | 被動預覽的簡介文字達到幾字才觸發 Gemma 自動摘要 | `60` |
| `link_preview.summary_max_chars` | 摘要輸出字數上限 | `200` |
| `link_preview.summary_input_max_chars` | 送入 Gemma 摘要前，原文截斷長度上限 | `4000` |
| `link_preview.attach_video` | 是否嘗試下載影片並以附件方式重新上傳 | `true` |
| `link_preview.video_max_upload_mb` | 影片附件下載／上傳大小上限（MB） | `8` |
| `link_preview.bilibili_fetch_video` | 是否額外呼叫 Bilibili 播放網址 API 取得影片 | `false` |
| `link_preview.summary_keyword` | 觸發通用網頁摘要的關鍵字 | `摘要` |
| `link_preview.summary_fetch_max_chars` | 關鍵字摘要功能抓取網頁純文字的長度上限 | `6000` |
| `link_preview.summary_fail_message` | 網頁爬取失敗時的回覆訊息 | `無法擷取這個網址的內容，可能是網站封鎖爬取或內容非純文字頁面。` |

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
│   │   ├── link_preview.py        # 連結預覽（Bilibili／Instagram／Threads）
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
│   │   └── models.py               # Gemini / Gemma 模型名稱常數（唯一來源）
│   ├── link_preview/               # 連結預覽核心邏輯（不含 Discord 直接依賴）
│   │   ├── base.py                    # LinkPreview / LinkStat 統一資料結構
│   │   ├── detector.py                # 從訊息文字偵測支援的平台連結
│   │   ├── flags.py                   # 布林設定讀取輔助
│   │   ├── http.py                    # 共用 httpx.AsyncClient 設定
│   │   ├── og_meta.py                 # 通用 Open Graph meta 標籤解析
│   │   ├── article.py                 # 通用網頁純文字擷取（關鍵字摘要用）
│   │   ├── summary_trigger.py         # 「關鍵字 + 網址」摘要請求偵測
│   │   ├── bilibili.py                # Bilibili 擷取器（含防 412 標頭）
│   │   ├── instagram.py               # Instagram 擷取器
│   │   ├── threads.py                 # Threads 擷取器
│   │   ├── pinterest.py               # Pinterest 擷取器
│   │   ├── registry.py                # 平台字串 → 擷取器 對應表
│   │   ├── summarizer.py              # 使用 Gemma 生成內容摘要
│   │   └── video.py                   # 影片下載與 Bilibili 播放網址解析
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

## 連結預覽（Bilibili／Instagram／Threads／Pinterest／關鍵字摘要）

`cogs/events/link_preview.py` 提供兩個彼此獨立、可能同時觸發的功能：

### 1. 被動預覽（不需關鍵字，貼連結即觸發）

Discord 對 Bilibili 短連結（`b23.tv`）、Instagram、Threads、Pinterest 的原生 Embed 支援不佳，常見完全沒有預覽或只顯示極少資訊。偵測到這四類連結時會自動重新產生一份完整的預覽，作法比照「縮圖修復」類第三方 Bot：

1. **偵測**：`core/link_preview/detector.py` 掃描訊息文字中的網址，比對是否屬於 `b23.tv`／`bilibili.com`／`instagram.com`／`threads.com`／`threads.net`／`pinterest.com`／`pin.it`。
2. **擷取**：依平台呼叫對應擷取器。
   - **Bilibili**：先解析 `b23.tv` 短連結重定向，再呼叫 Bilibili 公開 API 取得標題、簡介、封面、時長、UP 主與觀看／按讚／投幣／收藏／分享數；請求固定帶上 `Referer` / `Origin` 標頭，避免 Bilibili API 因缺少這兩個標頭回傳 `412`。
   - **Instagram／Threads／Pinterest**：解析頁面的 `og:*` meta 標籤取得標題、說明文字、縮圖／影片網址（Instagram／Threads 在未登入狀態下頁面資訊有限，屬於平台本身限制，詳見〈已知問題與排除〉）。
3. **簡介摘要**：若簡介文字長度超過 `link_preview.summary_trigger_min_chars`，改用 Gemma（`core/ai/models.py` 的 `MODELS["gemma"]`）生成繁體中文摘要取代原文，控制 token 用量。
4. **影片**：若擷取到可下載的影片網址，且大小在 `link_preview.video_max_upload_mb` 範圍內，會下載並以附件形式重新上傳；超過大小上限則退回「僅顯示縮圖 + 原始連結」。
5. **組裝與回覆**：組成 Embed（作者列／來源列／統計數據列／標題／縮圖或影片）並以回覆方式送出；內文超過 `link_preview.embed_description_max_chars` 會自動截斷，避免超過 Discord Embed 長度上限。若 Bot 具備「管理訊息」權限，會嘗試抑制原訊息的低品質原生 Embed。

新增其他平台時，只需在 `core/link_preview/` 新增一個擷取器並於 `registry.py` 註冊，`detector.py` 加入網域規則即可，不需修改 Cog 內的事件處理邏輯。

### 2. 關鍵字摘要（需明確關鍵字，不限定平台）

被動預覽只處理上述四個平台；若想針對「任何網址」（新聞、部落格、論壇文章等）取得摘要，需在訊息中包含關鍵字（預設「摘要」，可由 `link_preview.summary_keyword` 調整）並緊接著網址，例如：

```
摘要https://example.com/news/123
幫我摘要一下 https://example.com/article
```

流程：`core/link_preview/summary_trigger.py` 偵測到「關鍵字 + 網址」後，由 `core/link_preview/article.py` 抓取該網址並清理成純文字（移除 script/style 與 HTML 標籤），交給 `summarizer.py` 用 Gemma 生成摘要並回覆；若頁面請求失敗、內容非文字類型、或清理後為空，會直接回覆 `link_preview.summary_fail_message` 設定的訊息（預設「無法擷取這個網址的內容，可能是網站封鎖爬取或內容非純文字頁面。」）。

此功能刻意需要關鍵字才觸發，是因為它會對「任意」網址發送請求並呼叫 Gemma，若像被動預覽一樣自動觸發，會讓每則貼連結的訊息都消耗 API 額度；而 Bilibili／Instagram／Threads／Pinterest 等平台本身多為 JavaScript 單頁應用，直接抓取網頁純文字通常效果不佳，因此這個功能較適合文字內容較完整的一般網頁。

> **設計備註**：曾有另一版 `cogs/events/bilibili.py`（獨立 Cog）與 `core/utils/bilibili.py`（同步版工具函式）的實作方案，內含「Bilibili API 需要 Referer / Origin 標頭避免 412」這個有價值的修正，已併入 `core/link_preview/bilibili.py`；但這兩個檔案本身不會建立在專案中——獨立 Cog 會與本檔案同時處理 Bilibili 連結、造成同一則連結被回覆兩次，`core/utils/bilibili.py` 則會與既有的 `core/link_preview/bilibili.py` 形成兩份平行邏輯、增加日後維護時漏改其中一邊的風險。

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
`bot` / `ai` / `music` / `ticket` / `voice_channel` / `guild` / `moderation` / `embed_footer` / `link_preview`

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

### 連結預覽沒有反應 / 資訊不完整

檢查以下項目：

1. `settings.json` 的 `link_preview.enabled` 是否為 `true`
2. Bot 的 Intents 是否已開啟 `message_content`（讀不到訊息文字就偵測不到連結或關鍵字）
3. **Instagram／Threads／Pinterest 屬於平台本身限制**：三者未登入狀態下頁面能取得的 `og:*` 標籤本來就有限，有時只能取得標題與極少描述，屬於預期行為而非程式錯誤；如需更完整資料，需要額外串接登入態 Cookie（尚未內建，可依 `core/link_preview/instagram.py`／`threads.py`／`pinterest.py` 內的註解自行擴充）。
4. 影片沒有被上傳為附件：多半是超過 `link_preview.video_max_upload_mb` 大小上限而自動退回「僅縮圖 + 連結」，屬正常降級行為。
5. Bilibili API 回傳 `412`：已固定在請求加上 `Referer` / `Origin` 標頭修正，若仍發生，可能是 Bilibili 端另外調整了防爬機制，需重新確認所需標頭。

### 「摘要」關鍵字沒有反應 / 一直回覆無法擷取

檢查以下項目：

1. 關鍵字是否與 `settings.json` 的 `link_preview.summary_keyword` 一致（預設為「摘要」，需完整符合，不支援同義詞）
2. 網址是否緊接在關鍵字之後的同一則訊息內
3. 目標網站是否為需要登入或大量依賴 JavaScript 渲染的頁面（例如單頁應用），此類網站伺服器端回傳的 HTML 本身文字量就很少，屬於純文字擷取方式的固有限制，不是程式錯誤
4. `.env` 的 `GEMINI_API` 是否正確設定，缺少此金鑰時摘要會直接失敗

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

### 新增項目

**core/link_preview/**（新增，含 base.py / detector.py / flags.py / http.py / og_meta.py / article.py / summary_trigger.py / bilibili.py / instagram.py / threads.py / pinterest.py / registry.py / summarizer.py / video.py）
- 新增連結預覽核心邏輯：偵測 Bilibili 短連結／Instagram／Threads／Pinterest 連結，擷取標題、簡介、縮圖、影片網址與統計數據，並透過 Gemma 生成長文摘要（相較 Gemini 系列成本較低）
- 新增 Pinterest 擷取器（含 `pin.it` 短連結），沿用既有的 `og:*` 標籤解析邏輯
- 新增關鍵字觸發的通用網頁摘要（`article.py` + `summary_trigger.py`）：訊息中出現「摘要」關鍵字並緊接網址時，抓取任意網址的網頁純文字並用 Gemma 摘要，不限定於前述四個平台；爬取失敗時明確回覆無法擷取，與被動預覽的自動觸發邏輯完全獨立
- Bilibili 擷取器併入防 `412` 的 `Referer` / `Origin` 標頭修正（源自使用者提供的參考實作），並新增時長欄位解析
- 平台判斷、擷取邏輯與摘要邏輯皆與 Discord 物件解耦，新增平台只需新增一個擷取器並在 `registry.py` 註冊

**cogs/events/link_preview.py**（新增）
- 監聽伺服器訊息，涵蓋被動預覽（Bilibili／Instagram／Threads／Pinterest，貼連結即觸發）與關鍵字摘要（「摘要」+ 任意網址，需明確關鍵字）兩套獨立流程，可能同時觸發互不影響
- 被動預覽回覆風格統一的 Embed（比照「縮圖修復」類第三方 Bot 的呈現方式：作者列／來源列／統計數據列／標題／縮圖或影片），內文超長時自動截斷，避免超過 Discord Embed 長度上限
- 影片會嘗試下載並以附件重新上傳，超過大小上限則自動退回純縮圖呈現
- 具備「管理訊息」權限時，會嘗試抑制原訊息的低品質原生 Embed
- 內建行程內快取，避免同一連結短時間內重複發送外部請求

**core/ai/models.py**（新增）
- 集中定義 Gemini / Gemma 模型名稱常數，作為模型名稱與用途的唯一來源
- 新增 `MULTIMODAL_MODEL`，圖片附件不再依賴散落的硬編碼模型名稱
- 連結預覽的兩套摘要功能（被動預覽簡介摘要、關鍵字網頁摘要）皆直接複用 `MODELS["gemma"]`

**settings.json**
- 正式合併 `link_preview.*` 系列設定至實際專案設定檔（enabled / max_embeds_per_message / cache_size / request_timeout_seconds / embed_description_max_chars / attach_video / video_max_upload_mb / bilibili_fetch_video / summary_trigger_min_chars / summary_max_chars / summary_input_max_chars / summary_keyword / summary_fetch_max_chars / summary_fail_message）

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
- `on_message` 加上外層例外保護，避免未攔截例外被 discord.py 預設 `on_error` 印到 stderr、繞過專案自己的 logging 系統，導致「功能靜默失效、log 也看不到」

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
