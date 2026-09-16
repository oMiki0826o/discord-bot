# 流螢醬 Discord Bot

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)
![discord.py](https://img.shields.io/badge/discord.py-2.7.1-5865F2)
![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-green)

以 Python 與 discord.py 開發的多功能 Discord Bot，整合 AI 對話、YouTube 音樂、連結預覽修復、伺服器管理、身份組面板、工單與臨時語音頻道。

## 主要功能

- AI 對話：支援 `/ai`、在伺服器 @提及 Bot、附件解析、長期記憶與使用者輪廓。
- 音樂播放：YouTube 單曲與播放清單、播放佇列、播放歷史、互動控制面板與個人收藏。
- 連結預覽：處理 Bilibili、Instagram、Threads、Pinterest、Twitter/X 與 TikTok，並支援「摘要 + 網址」的網頁摘要。
- 伺服器管理：封禁、踢出、Timeout、警告、批量刪除與管理日誌。
- 社群工具：歡迎訊息、自動身份組、自助身份組面板、工單系統與 Join-to-Create 語音頻道。
- 維運能力：Cog 自動載入與重載、Slash Command 同步、輪替日誌、Owner 錯誤通知與關機報告。

## 環境需求

- Python 3.11 以上
- FFmpeg（音樂播放必需）
- Discord Bot Token
- Google Gemini API Key（僅 AI 對話與摘要功能需要）
- MediaInfo 系統程式（如需完整的媒體附件資訊）

SQLite 由 Python 內建模組提供，不需額外安裝資料庫伺服器。

## Discord 應用程式設定

在 Discord Developer Portal 建立 Bot 後：

1. 啟用 `Server Members Intent` 與 `Message Content Intent`。
2. 邀請 Bot 時勾選 `bot` 與 `applications.commands` scopes。
3. 依需要開啟 Bot 權限。常用功能需要：查看頻道、發送訊息、嵌入連結、附加檔案、讀取訊息歷史。
4. 工單、臨時語音、管理、Webhook 與身份組功能還需對應的管理權限。

Bot 不必使用 `Administrator`。生產環境建議只授予已啟用功能需要的權限，並將 Bot 身份組放在它需要管理的身份組上方。

## 安裝

```bash
git clone https://github.com/oMiki0826o/discord-bot.git
cd discord-bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell 啟用虛擬環境：

```powershell
.venv\Scripts\Activate.ps1
```

## 環境變數

編輯 `.env`：

```env
DISCORD_TOKEN=Discord_Bot_Token
GEMINI_API=Google_Gemini_API_Key
OWNER_ID=Discord_User_ID
DB_PATH=database/ai/memory.db
```

| 變數 | 必要性 | 用途 |
|---|---|---|
| `DISCORD_TOKEN` | 必要 | Discord Bot Token；未設定時無法啟動 |
| `GEMINI_API` | 選填 | Google AI API Key；未設定時 AI 與摘要功能不可用 |
| `OWNER_ID` | 建議 | Owner 指令與私訊轉發目標 |
| `DB_PATH` | 選填 | SQLite 路徑，預設 `database/ai/memory.db` |
| `EXTENSION_PACKAGES` | 進階 | 要掃描的 Cog package，預設 `cogs` |
| `EXTENSION_BLACKLIST` | 進階 | 不載入的模組，多個值用逗號分隔 |
| `EXCLUDED_DIRS` | 進階 | Cog 掃描時排除的目錄 |

不要將 `.env`、Bot Token 或 API Key 提交到版本庫。

## 啟動

```bash
python bot.py
```

macOS 也可執行：

```bash
./start.command
```

啟動時 Bot 會初始化 SQLite、自動掃描 `cogs/`、載入 Extension，並將 Slash Commands 同步到 Discord。全域 Slash Command 的顯示可能不會立即更新；`$slash` 可手動重新同步全域指令，`$slash_guild` 用於清除當前伺服器殘留的重複指令。

## 開始使用

- `/help`：顯示目前載入的 Slash Commands。
- `/ai`：使用 AI，可在伺服器與私訊使用。
- `/play`：在使用者所在的語音頻道播放 YouTube。
- `/server info`：查看目前伺服器設定。
- `/ticket panel`：發送工單建立面板。
- `/vc setup`：設定 Join-to-Create 語音頻道。

`/say` 會以 `「user ID」説：內容` 格式代發訊息，並要求使用者具備「管理訊息」權限。

完整指令、權限、設定與架構說明請見 [開發與維運文件](docs/DEVELOPMENT.md)。

## 測試

安裝 pytest 後執行：

```bash
python -m pip install pytest
python -m pytest -q
```

## 授權

本專案採用 [PolyForm Noncommercial License 1.0.0](LICENSE)。商業用途請先確認授權條件。
