"""
cogs/events/bilibili.py

Modification():
- 偵測 Discord 訊息中的 Bilibili URL
- 擷取 BV
- 呼叫 Bilibili API（含防 412 headers）
- 建立 Discord Embed
- 自動輸出到頻道
"""

import re
import discord
from discord.ext import commands
import requests

# ─────────────────────────────────────
# URL Regex（穩定版）
# ─────────────────────────────────────
URL_REGEX = re.compile(r"(https?://[^\s<>()]+)")

# ─────────────────────────────────────
# Bilibili API
# ─────────────────────────────────────
BILI_API = "https://api.bilibili.com/x/web-interface/view?bvid={}"

# ─────────────────────────────────────
# 防 412 headers（關鍵）
# ─────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com"
}


# ─────────────────────────────────────
# 判斷是否 Bilibili
# ─────────────────────────────────────
def is_bilibili(url: str) -> bool:
    return "bilibili.com" in url or "b23.tv" in url


# ─────────────────────────────────────
# BV 擷取
# ─────────────────────────────────────
def extract_bvid(url: str) -> str | None:
    match = re.search(r"(BV\w+)", url)
    return match.group(1) if match else None


# ─────────────────────────────────────
# API 抓資料
# ─────────────────────────────────────
def fetch_bilibili(bvid: str) -> dict | None:
    try:
        res = requests.get(
            BILI_API.format(bvid),
            headers=HEADERS,
            timeout=8
        )

        # debug（如果還壞可以打開）
        # print(res.status_code, res.text[:100])

        data = res.json()

        if data.get("code") != 0:
            return None

        info = data["data"]

        return {
            "title": info["title"],
            "image": info["pic"],
            "url": f"https://www.bilibili.com/video/{bvid}",
            "author": info["owner"]["name"],
            "duration": info.get("duration")
        }

    except Exception as e:
        print("[BILIBILI ERROR]", e)
        return None


# ─────────────────────────────────────
# Embed 建立
# ─────────────────────────────────────
def build_embed(data: dict) -> discord.Embed:
    embed = discord.Embed(
        title=data["title"],
        url=data["url"],
        color=0x00A1D6
    )

    if data.get("image"):
        embed.set_image(url=data["image"])

    if data.get("author"):
        embed.add_field(
            name="作者",
            value=data["author"],
            inline=True
        )

    if data.get("duration"):
        embed.add_field(
            name="時長",
            value=str(data["duration"]),
            inline=True
        )

    return embed


# ─────────────────────────────────────
# Cog 主體
# ─────────────────────────────────────
class BilibiliPreview(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):

        # 防 bot 自己觸發
        if message.author.bot:
            return

        # 抓 URL
        urls = URL_REGEX.findall(message.content)

        if not urls:
            return

        for url in urls:

            if not is_bilibili(url):
                continue

            bvid = extract_bvid(url)

            if not bvid:
                continue

            data = fetch_bilibili(bvid)

            if not data:
                return

            embed = build_embed(data)

            await message.channel.send(embed=embed)


# ─────────────────────────────────────
# Discord extension entry point
# ─────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(BilibiliPreview(bot))