# 流螢醬 Discord Bot

![Version](https://img.shields.io/badge/version-1.1-blue)
![License](https://img.shields.io/badge/license-PolyForm--Noncommercial--1.0.0-green)
![Tech](https://img.shields.io/badge/stack-Python-lightgrey)
![Python](https://img.shields.io/badge/Python-3.11%2B-orange)
![discord.py](https://img.shields.io/badge/discord.py-2.7.1-orange)

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
流螢醬是一款以 Python 與 discord.py 打造的多功能 Discord Bot，整合 Google Gemini 驅動的 AI 對話與長期記憶、音樂播放、連結預覽修復、身份組自助面板、臨時語音頻道與客服工單系統。多數行為都能透過設定檔即時調整，不需要修改程式碼或重新啟動。

### 功能
- AI 對話與長期記憶（Gemini／Gemma 驅動，支援語意搜尋記憶與使用者輪廓；`/ai` 指令可在伺服器頻道與私訊中使用）
- 音樂播放與個人收藏清單
- 連結預覽修復（Bilibili／Instagram／Threads／Twitter(X)／TikTok／Pinterest，含影片內嵌播放）
- 身份組自助領取面板
- 加入即建立的臨時語音頻道
- 客服工單系統

### 安裝
```bash
git clone https://github.com/oMiki0826o/discord-bot.git
cd discord-bot
pip install -r requirements.txt
cp .env.example .env
# 編輯 .env，填入 DISCORD_TOKEN 等必要環境變數
```

### 使用方式
```bash
python bot.py
```

啟動後於 Discord 使用 `/ai`、`/play` 等 Slash 指令，或直接 @提及 Bot 開始 AI 對話。完整指令清單、設定檔說明與開發文件請見 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

### 授權
本專案採用 PolyForm Noncommercial License 1.0.0 授權，詳見 [LICENSE](./LICENSE)。

---

## English

### Table of Contents
- [About](#about)
- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [License](#license)

### About
Liuying-chan is a multi-purpose Discord bot built with Python and discord.py, combining Google Gemini-powered AI chat with long-term memory, music playback, link-preview fixes, self-service role panels, temporary voice channels, and a ticket support system. Most behavior can be adjusted live through a settings file, without editing code or restarting the bot.

### Features
- AI chat with long-term memory (Gemini/Gemma-powered, with semantic memory search and user profiles; the `/ai` command works in both server channels and DMs)
- Music playback with personal favorites
- Link preview fixes (Bilibili/Instagram/Threads/Twitter(X)/TikTok/Pinterest, with embedded video playback)
- Self-service role panels
- Join-to-create temporary voice channels
- Ticket support system

### Installation
```bash
git clone https://github.com/oMiki0826o/discord-bot.git
cd discord-bot
pip install -r requirements.txt
cp .env.example .env
# Edit .env and fill in DISCORD_TOKEN and other required variables
```

### Usage
```bash
python bot.py
```

Once running, use Slash commands like `/ai` or `/play` in Discord, or @mention the bot to start an AI conversation. See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full command list, settings reference, and developer documentation.

### License
This project is licensed under the PolyForm Noncommercial License 1.0.0, see [LICENSE](./LICENSE).
