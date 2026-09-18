# 流螢醬 Discord Bot

![Version](https://img.shields.io/badge/version-unreleased-blue)
![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-green)
![Tech](https://img.shields.io/badge/stack-Python%20%2B%20discord.py-lightgrey)
![Python](https://img.shields.io/badge/Python-3.11%2B-orange)

[中文](#中文) | [English](#english)

---

## 中文

### 目錄

- [關於](#關於)
- [功能](#功能)
- [安裝](#安裝)
- [使用方式](#使用方式)
- [授權](#授權)

### 關於

流螢醬 Discord Bot 是以 Python 與 discord.py 開發的多功能 Discord Bot, 整合 AI 對話, YouTube 音樂播放, 連結預覽, 伺服器管理, 身份組面板, 工單系統與 Join-to-Create 臨時語音頻道.

專案採模組化架構. Discord 指令與事件入口放在 `cogs/`, 可重用業務邏輯放在 `core/`, 資料存取集中於 `database/repository/`. Cog 由 Extension Loader 自動掃描與載入.

AI 子系統使用 Google GenAI SDK, 並包含規則式模型路由, 搜尋判斷, Prompt 組裝, 頻道上下文, 長期記憶, 對話摘要, 使用者輪廓, 附件解析, Prompt injection 偵測, 請求頻率限制與模型 fallback. 需要隔離的對話與記憶會攜帶 `user_id + channel_id`, 避免不同伺服器或頻道互相污染.

完整架構, 指令, 設定, AI 流程, 權限與故障排除說明請見 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

### 功能

- AI 對話: 支援 `/ai`, Bot mention 對話, 模型池路由, Gemini Grounding, 附件解析, 長期記憶, 對話摘要與使用者輪廓.
- 音樂系統: 支援 YouTube 單曲與播放清單, 播放佇列, 歷史紀錄, 個人收藏, 循環, 音量與互動控制面板.
- 連結預覽: 支援 Bilibili, Instagram, Threads, Pinterest, Twitter/X 與 TikTok, 以及關鍵字觸發的通用網頁摘要.
- 伺服器管理: 提供 Ban, Kick, Timeout, Warn, Purge, Mod Log 與 Guild 設定.
- 社群工具: 提供歡迎與離開訊息, 自動身份組, 自助身份組面板, 工單系統與 Join-to-Create 語音頻道.
- 維運工具: 支援 Cog 自動載入與 reload, Slash Command 同步, settings 熱更新, 日誌, Owner 管理指令與錯誤通報.
- 安全機制: 包含 Discord 權限與身份組階層檢查, AI 請求頻率限制, Prompt injection 偵測, URL/SSRF 防護與長期記憶秘密資料過濾.

### 安裝

需求:

- Python 3.11 或以上.
- FFmpeg, 音樂功能需要.
- Discord Bot Token.
- Google Gemini API Key, AI 對話與部分摘要功能需要.
- MediaInfo, 僅在需要完整媒體附件資訊時使用.

```bash
git clone https://github.com/oMiki0826o/discord-bot.git
cd discord-bot

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cp .env.example .env
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

編輯 `.env`:

```env
DISCORD_TOKEN=你的_Discord_Bot_Token
GEMINI_API=你的_Google_Gemini_API_Key
OWNER_ID=你的_Discord_使用者_ID
DB_PATH=database/ai/memory.db
```

只有 `DISCORD_TOKEN` 是 Bot 啟動所必需. 未設定 `GEMINI_API` 時, AI 相關功能無法正常使用, 但其他 Discord 功能仍可運作.

請勿提交 `.env`, Bot Token, API Key, Cookie, Session 或其他秘密憑證.

### 使用方式

Discord Developer Portal 至少啟用:

- Server Members Intent.
- Message Content Intent.

OAuth2 邀請至少包含:

- `bot`.
- `applications.commands`.

不建議直接授予 Administrator. 請依實際功能配置 View Channels, Send Messages, Embed Links, Attach Files, Read Message History, Connect, Speak, Manage Messages, Moderate Members, Manage Roles, Manage Channels, Manage Webhooks 等必要權限.

啟動:

```bash
python bot.py
```

macOS 也可使用:

```bash
./start.command
```

常用入口:

```text
/help
/ai
/play
/server info
/ticket panel
/vc setup
```

開發檢查:

```bash
pytest
ruff check .
mypy .
```

`settings.json` 保存非機密行為設定, 多數設定會影響後續請求. Token 與 API Key 應只放在 `.env`.

### 授權

本專案採用 PolyForm Noncommercial License 1.0.0, 詳見 [LICENSE](./LICENSE).

此授權不是 MIT. 使用, 修改, 散布或衍生作品時, 請依 LICENSE 內的非商業用途條件與通知要求辦理.

---

## English

### Table of Contents

- [About](#about)
- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [License](#license)

### About

Firefly Discord Bot is a multi-purpose Discord bot built with Python and discord.py. It combines AI chat, YouTube music playback, link previews, moderation, role panels, tickets, and Join-to-Create temporary voice channels.

Discord-facing commands and events live in `cogs/`, reusable application logic lives in `core/`, and persistent storage access is centralized under `database/repository/`. Extensions are discovered and loaded automatically.

The AI subsystem uses the Google GenAI SDK and includes rule-based model routing, search decisions, prompt construction, channel-scoped context, long-term memory, summaries, user profiles, attachment parsing, prompt-injection detection, abuse throttling, and model fallback.

For architecture, commands, configuration, AI internals, permissions, testing, and troubleshooting, see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

### Features

- AI chat: `/ai`, bot-mention conversations, model pools, Gemini Grounding, attachment parsing, long-term memory, summaries, and user profiles.
- Music: YouTube tracks and playlists, queues, playback history, favorites, looping, volume controls, and interactive controls.
- Link previews: Bilibili, Instagram, Threads, Pinterest, Twitter/X, TikTok, plus generic webpage summaries.
- Moderation: ban, kick, timeout, warnings, purge, moderation logs, and guild configuration.
- Community tools: welcome and leave messages, autoroles, self-role panels, tickets, and Join-to-Create voice channels.
- Operations: automatic Cog discovery, reload support, Slash Command synchronization, hot-reloaded settings, logging, owner commands, and error reporting.
- Security: permission checks, role hierarchy checks, AI abuse throttling, prompt-injection detection, URL/SSRF protection, and secret filtering for long-term memory.

### Installation

Requirements:

- Python 3.11 or newer.
- FFmpeg for music playback.
- A Discord Bot Token.
- A Google Gemini API key for AI chat and related summarization features.
- MediaInfo only when richer media attachment metadata is required.

```bash
git clone https://github.com/oMiki0826o/discord-bot.git
cd discord-bot

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cp .env.example .env
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Edit `.env`:

```env
DISCORD_TOKEN=your_discord_bot_token
GEMINI_API=your_google_gemini_api_key
OWNER_ID=your_discord_user_id
DB_PATH=database/ai/memory.db
```

Only `DISCORD_TOKEN` is required for the bot process to start. Without `GEMINI_API`, AI-related features are unavailable while non-AI Discord features can continue to operate.

Never commit `.env`, bot tokens, API keys, cookies, sessions, or other credentials.

### Usage

Enable Server Members Intent and Message Content Intent in the Discord Developer Portal. The OAuth2 invitation should include `bot` and `applications.commands`.

Avoid granting Administrator by default. Assign only the permissions required by enabled features.

```bash
python bot.py
```

On macOS:

```bash
./start.command
```

Development checks:

```bash
pytest
ruff check .
mypy .
```

`settings.json` stores non-secret runtime behavior. Secrets belong in `.env`.

### License

This project is licensed under the PolyForm Noncommercial License 1.0.0. See [LICENSE](./LICENSE).

This is not the MIT License. Use, modification, distribution, and derived works must follow the noncommercial conditions and notice requirements in the license text.
