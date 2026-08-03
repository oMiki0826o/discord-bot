#!/bin/bash

# 切換到腳本所在資料夾
cd "$(dirname "$0")"

# 啟動虛擬環境（如果存在）
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# 執行 Bot
python3 bot.py

echo
echo "Bot 已結束，按 Enter 關閉..."
read