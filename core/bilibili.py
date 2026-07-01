"""
core/utils/bilibili.py

Modification():
- 負責 Bilibili URL 判斷
- BV 擷取
- API 抓取影片資訊
"""

import re
import requests

# ── API endpoint ──────────────────────
BILI_API = "https://api.bilibili.com/x/web-interface/view?bvid={}"

# ── URL regex（穩定版） ──────────────────────
URL_REGEX = re.compile(r"(https?://[^\s<>()]+)")


# ── 判斷是否為 Bilibili ──────────────────────
def is_bilibili(url: str) -> bool:
    return "bilibili.com" in url or "b23.tv" in url


# ── 擷取 BV ID ──────────────────────
def extract_bvid(url: str) -> str | None:
    match = re.search(r"(BV\w+)", url)
    return match.group(1) if match else None


# ── API 取得資料（推薦） ──────────────────────
def fetch_bilibili(bvid: str) -> dict | None:
    try:
        res = requests.get(BILI_API.format(bvid), timeout=5)
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

    except Exception:
        return None