# firefly-bot — 最終整合版

109 個 Python 檔案、23 個 Cog、8 個 Repository。
整合來源：`firefly-bot-final` / `firefly-bot-merged` / `music_bot` / `ai-bot` / `Bot-Firefly`。

---

## 快速啟動

```bash
# 1. 安裝依賴（含 FFmpeg，見下方說明）
pip install -r requirements.txt

# 2. 複製並填寫機密設定
cp .env.example .env

# 3. 編輯非機密客製化設定
nano settings.json

# 4. 啟動
python bot.py

# 5. 首次啟動後同步 Slash Commands（Owner）
# 在 Discord 輸入：
$slash_guild   # 即時生效（當前伺服器，測試用）
$slash         # 全域同步（最多 1 小時生效）
```

### FFmpeg 安裝

```bash
# Ubuntu / Debian
sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Windows：下載 https://ffmpeg.org/download.html
# 設定 PATH 或在 settings.json 指定路徑：
# "music": { "ffmpeg_path": "C:/ffmpeg/bin/ffmpeg.exe" }
```

---

## 設定架構

| 檔案 | 用途 | 是否熱載入 |
|------|------|----------|
| `.env` | 機密值（Token / API Key） | 否（重啟生效） |
| `settings.json` | 所有可調整的非機密設定 | 是（每次 get() 自動檢查 mtime） |
| `database/ai/keywords.json` | AI 記憶觸發關鍵字（較長） | 是 |
| `database/ai/blocked_words.json` | 內容過濾詞清單（較長） | 是 |
| `database/ai/background.txt` | AI 角色背景描述 | 是 |
| `database/ai/moderation_rules.txt` | 內容審核規則 | 是 |

### settings.json 可調整項目（摘錄）

```
bot.command_prefix          前綴（預設 $）
bot.status_type             playing/listening/watching/competing/custom
bot.status_text             狀態文字
bot.presence                online/idle/dnd/invisible

ai.persona_name             AI 角色名稱
ai.cooldown_seconds         對話冷卻（秒）
ai.max_reply_length         超過此字數轉為附件
ai.social_tier_names        社交等級名稱（0-3）
ai.conversation_state_labels 對話模式標籤

music.max_queue_size        佇列上限
music.idle_timeout_seconds  閒置斷線秒數
music.default_volume_percent 預設音量
music.ffmpeg_path           FFmpeg 執行檔路徑
music.embed_colors.*        各 Embed 顏色
music.loop_labels.*         循環模式按鈕文字

ticket.channel_prefix       工單頻道前綴
ticket.cooldown_seconds     開票冷卻秒數
ticket.max_per_user         每人最大同時開票數

voice_channel.default_name_template  JTC 頻道名稱（{username}）
voice_channel.default_limit          預設人數上限

guild.welcome_template      歡迎訊息（{user}/{username}/{guild}/{count}）
guild.leave_template        離開訊息

moderation.default_mute_minutes  預設禁言時長
moderation.dm_target_on_warn     警告時是否私訊
moderation.dm_target_on_mute     禁言時是否私訊

embed_footer.default        全局 Embed 頁腳
embed_footer.music          音樂 Embed 頁腳
```

---

## 完整指令一覽

### AI 對話（cogs/ai/）

| 觸發 | 說明 |
|------|------|
| `@Bot 訊息` | Gemma 模型一般對話 |
| `@Bot 用flash 訊息` | Gemini 2.5 Flash |
| `@Bot 用gemini 訊息` | Gemini Lite |
| `@Bot 用gemma 訊息` | 強制 Gemma |
| 附圖片 | Gemini 多模態視覺（直接識圖）|
| 附其他檔案 | 自動解析：PDF/Word/程式碼/ZIP 等 |
| `$ban @用戶` | AI 封鎖（Owner）|
| `$unban @用戶` | AI 解封（Owner）|
| `$dashboard` | 系統總覽（Owner）|
| `$info @用戶` | 使用者 AI 統計 |

### 系統管理（cogs/system/）

| 指令 | 說明 | 權限 |
|------|------|------|
| `$game [type] <文字>` | 快速設定狀態（寫入 settings.json）| Owner |
| `$slash` | 全域同步 Slash Commands | Owner |
| `$slash_guild` | 即時同步至當前伺服器 | Owner |
| `$load <模組>` | 載入 Cog | Owner |
| `$unload <模組>` | 卸載 Cog | Owner |
| `$reload <模組>` | 重載 Cog | Owner |
| `$monitor` | 系統資源監控 | Owner |
| `$settings show [section]` | 顯示 settings.json | Owner |
| `$settings reload` | 熱重載 settings.json + 套用狀態 | Owner |

### 狀態事件（cogs/events/）

| 指令 / 事件 | 說明 | 權限 |
|-------------|------|------|
| `$status <online\|idle\|dnd\|invisible> <type> <文字>` | 完整狀態指令（含 custom 類型）| Owner |
| `$status_show` | 顯示目前狀態 | Owner |
| `on_ready` | 啟動時自動套用 settings.json 狀態 | — |
| DM 收到訊息 | 自動轉發給 Bot Owner（含轉寄 reply 橋接）| — |

### 訊息工具（cogs/talk/）

| 指令 | 說明 | 權限 |
|------|------|------|
| `/say <內容>` | Bot 代發訊息（支援附件/圖片 URL/回覆）| Manage Messages |
| `/embed` | 全功能 Embed 建構器（標題/描述/顏色/作者/頁腳/縮圖/圖片）| Manage Messages |
| `/typing` | 持續顯示正在輸入 | Manage Messages |
| `/typing_stop` | 停止輸入指示器 | Manage Messages |
| `/webhook <內容>` | Webhook 偽裝發話（自訂名稱/頭像/附件）| Manage Webhooks |

### 通用工具（cogs/utility/）

| 指令 | 說明 |
|------|------|
| `/ping` | 顯示 Bot 延遲（綠/黃/紅色碼）|
| `/botinfo` | Bot 基本資訊（延遲/伺服器數/上線時間/角色名）|
| `/help` | 列出所有 Slash Commands（Embed 格式）|
| `/hi` | 向 Bot 打招呼 |
| `/hyw` | 何意味 |
| `/fav add` | 收藏當前播放歌曲 |
| `/fav list` | 查看個人收藏清單（翻頁）|
| `/fav play <編號>` | 播放收藏清單中的歌曲 |
| `/fav remove <編號>` | 移除收藏 |
| `/fav clear` | 清空收藏 |

### 音樂播放（cogs/music/）

| 指令 | 說明 |
|------|------|
| `/play <關鍵字或 URL>` | 播放單曲（YouTube 搜尋或直連）|
| `/playlist <URL>` | 加入整個播放清單 |
| `/skip` | 跳過當前歌曲 |
| `/pause` / `/resume` | 暫停 / 繼續 |
| `/stop` | 停止並清空佇列 |
| `/nowplaying` | 查看正在播放（含控制面板）|
| `/queue` | 查看佇列（可翻頁）|
| `/shuffle` | 隨機排列佇列 |
| `/loop off/single/queue` | 循環模式 |
| `/volume 0-200` | 調整音量 |
| `/remove <編號>` | 移除佇列歌曲 |
| `/move <from> <to>` | 移動歌曲位置 |
| `/clear` | 清空佇列 |
| `/history` | 最近播放紀錄 |
| `/leave` | 離開語音頻道 |
| `/musicstatus` | 所有伺服器播放狀態（Admin）|

控制面板按鈕（在播放訊息上）：暫停/繼續 · 跳過 · 停止 · 循環切換 · 離開

### 伺服器管理（cogs/moderation/ + cogs/guild/）

| 指令 | 說明 | 權限 |
|------|------|------|
| `/ban @成員 [原因]` | 封禁 | Ban Members |
| `/unban <ID>` | 解封 | Ban Members |
| `/kick @成員 [原因]` | 踢出 | Kick Members |
| `/mute @成員 [分鐘] [原因]` | 禁言（Discord timeout）| Moderate Members |
| `/unmute @成員` | 解除禁言 | Moderate Members |
| `/warn @成員 [原因]` | 警告（可 DM 通知）| Moderate Members |
| `/warnings @成員` | 查看警告紀錄 | Moderate Members |
| `/clear_warns @成員` | 清除警告 | Administrator |
| `/purge [數量]` | 批量刪除訊息（最多 100）| Manage Messages |
| `/modlog` | 最近 20 筆管理動作 | Moderate Members |
| `/server welcome/leave/log/autorole` | 事件頻道設定 | Administrator |
| `/server ticket_category/ticket_support` | 工單設定 | Administrator |
| `/server info` | 查看目前設定 | Manage Guild |
| `/server reset` | 重置伺服器設定 | Administrator |

### 工單系統（cogs/ticket/）

| 指令 | 說明 |
|------|------|
| `/ticket panel` | 發送工單建立面板（Admin）|
| `/ticket open [主題]` | 建立工單（私人頻道 + 關閉按鈕）|
| `/ticket close` | 關閉工單 |
| `/ticket add/remove @成員` | 管理工單成員 |
| `/ticket stats` | 工單統計 |

### 身份組（cogs/roles/）

| 指令 | 說明 |
|------|------|
| `/roles panel` | 建立身份組自助面板 |
| `/roles add <訊息ID> @身份組` | 新增按鈕 |
| `/roles remove <訊息ID> @身份組` | 移除按鈕 |
| `/roles delete <訊息ID>` | 刪除面板 |
| `/roles list` | 列出所有面板 |

### 臨時語音頻道 JTC（cogs/voice/）

| 指令 | 說明 |
|------|------|
| `/vc setup #頻道` | 設定 JTC 觸發頻道（Admin）|
| `/vc name/limit/lock/unlock` | 頻道設定（擁有者）|
| `/vc permit/reject @成員` | 進出控制（擁有者）|
| `/vc kick @成員` | 踢出頻道（擁有者）|
| `/vc transfer @成員` | 轉移所有權（擁有者）|
| `/vc info` | 查看頻道資訊 |
| `/vc forcedelete #頻道` | 強制刪除（Admin）|

### Minecraft（cogs/minecraft/）

| 指令 | 說明 |
|------|------|
| `/mc pearl <px> <py> <pz> <dest_x> <dest_z> [ground_height]` | 珍珠炮計算（支援 DM）|

---

## 專案結構

```
bot/
├── bot.py                    # FireflyBot 主類別，setup_hook 載入所有 Cog
├── config.py                 # 僅機密 env：TOKEN / API Key / 路徑
├── startup.py                # 同步預載（非同步前執行）
├── settings.json             # 所有可客製化設定（熱載入）
├── requirements.txt
├── .env.example
│
├── cogs/
│   ├── ai/                   # AI 對話、儀表板、擁有者指令、統計
│   ├── events/               # on_ready 狀態套用、DM 轉發橋接
│   ├── guild/                # 伺服器設定、成員進出事件
│   ├── minecraft/            # 珍珠炮計算機
│   ├── moderation/           # ban/kick/mute/warn 等管理指令
│   ├── music/                # 14 個音樂播放指令 + 控制面板
│   ├── roles/                # 身份組自助面板（Button Roles）
│   ├── system/               # 擁有者工具、熱載入、監控、設定指令
│   ├── talk/                 # say/embed/typing/webhook
│   ├── ticket/               # 工單系統（面板 + 私人頻道 + 關閉按鈕）
│   ├── utility/              # ping/help/hi/botinfo + 音樂收藏
│   └── voice/                # JTC 臨時語音頻道
│
├── core/
│   ├── ai/                   # AI 生成、記憶、搜尋、濫用偵測、file_parser
│   ├── music/                # Song / Queue / GuildPlayer / Embeds / Views
│   ├── minecraft/            # pearl_calculator（純函式）
│   ├── logging/              # LogManager、discord 錯誤通知
│   └── system/               # ExtensionLoader、settings.py 熱載入、event_bus
│
├── database/
│   ├── ai/                   # SQLite 連線、背景.txt、關鍵字.json、禁詞.json
│   └── repository/           # 8 個 Repository（user/memory/audit/guild/
│                             #   ticket/mod/vc/favorites）
│
└── utils/
    ├── ai/prompt_guard.py    # 提示詞注入偵測
    ├── checks.py             # owner_only() / slash_owner_only()
    ├── converters.py         # VoiceChannelConverter / TimeConverter
    ├── formatter.py          # format_duration / truncate_text / human_bytes
    └── helpers.py            # format_exception / safe_int / safe_float
```

---

## Discord Bot 設定需求

**Privileged Gateway Intents（必須在 Developer Portal 開啟）**
- `SERVER MEMBERS INTENT` — 成員進出、JTC、自動身份組
- `MESSAGE CONTENT INTENT` — AI 對話、前綴指令

**OAuth2 Scopes**：`bot` + `applications.commands`

**建議 Bot Permissions**：`Administrator`

或逐項授予：Send Messages、Read Message History、Manage Messages、Manage Channels、Manage Roles、Ban Members、Kick Members、Moderate Members、Move Members、Connect、Speak、Manage Webhooks

---

## 初次設定流程

1. 填寫 `.env`
2. 編輯 `settings.json`（Bot 狀態、AI 角色名、音樂設定等）
3. 啟動 Bot：`python bot.py`
4. 輸入 `$slash_guild` 立即同步 Slash Commands（測試）
5. 測試通過後輸入 `$slash` 全域同步
6. `/server welcome #頻道` 設定歡迎頻道
7. `/vc setup #加入即建立` 設定 JTC 觸發頻道
8. `/ticket panel` 在工單入口頻道發送面板
9. `/roles panel` 建立身份組自助面板

