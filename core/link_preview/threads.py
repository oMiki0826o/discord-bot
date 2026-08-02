"""
core/link_preview/threads.py

Modification():

- 修正 _DEFAULT_HOSTS 與 settings.json 的候選網域：
  1. www.fixthreads.net 對應的原始專案（fixthreads）已由作者封存
     下線，不是暫時性故障，繼續留著只會每次都白白嘗試一個確定會
     失敗的候選，已移除。
  2. www.vxthreads.net 誤加了官方沒有的 www 字首——官方專案文件
     明確寫的是不含 www 的 vxthreads.net；官方沒有配置 www 子網域，
     這正是 log 中這個候選會等到 request_timeout_seconds（10 秒）
     逾時才失敗的原因。已修正為 vxthreads.net，並新增
     viewthreads.com 作為第二個獨立候選（不同開發者維運）。
- 新增「候選網域全部失敗時，最後嘗試原始網址本身」的備援：
  fixthreads 專案封存公告本身提到的原因是「Threads 官方現在的
  metadata 比以前完整」，代表現在直接抓 threads.net/com 原始頁面，
  說不定就能拿到堪用的 og 標籤，不再完全依賴第三方代理服務。做法
  是把解析出的原始 hostname 併入候選清單最後一位，_THREADS_HOST_RE
  替換成同樣的 host 等於維持原網址不變，讓 try_hosts() 額外多打一次
  原始網址；所有代理都掛掉時，原本會直接放棄，現在多一次「至少
  試試看官方頁面本身」的機會，不會更差。
- 新增讀取 og:video 標籤，供 Cog 層決定是否下載內嵌播放。

職責：

- 將 Threads 貼文連結轉換為 LinkPreview
- Threads（threads.net / threads.com）的 Discord 原生 Embed 僅
  顯示純連結，沒有任何預覽內容；改用社群維運的代理服務取得完整
  的 og:title / og:description / og:image（部分服務亦提供
  og:video），全部代理都失敗時退回原始網址本身
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

from core.link_preview.base import LinkPreview
from core.link_preview.fallback import try_hosts
from core.link_preview.og_meta import extract_og_tags
from core.system.settings import get_list

logger = logging.getLogger("bot.link_preview.threads")

_THREADS_HOST_RE = re.compile(r"(www\.)?threads\.(net|com)", re.IGNORECASE)

# 候選代理網域的內建後備清單；settings.json 未設定時使用此值
_DEFAULT_HOSTS = ["vxthreads.net", "viewthreads.com"]


# ── 對外介面 ──────────────────────

async def extract(url: str) -> LinkPreview | None:
    """將 Threads 連結轉換為 LinkPreview，失敗時回傳 None。"""
    hosts = list(get_list("link_preview.threads_proxy_hosts", _DEFAULT_HOSTS))

    # 所有代理都失敗時，最後試一次原始網址本身（見上方 Modification 說明）。
    original_host = urlsplit(url).hostname
    if original_host and original_host not in hosts:
        hosts.append(original_host)

    response = await try_hosts(
        build_url      = lambda host: _THREADS_HOST_RE.sub(host, url, count=1),
        hosts          = hosts,
        platform_label = "Threads",
    )
    if response is None:
        return None

    tags = extract_og_tags(response.text)
    if not tags:
        logger.warning("[Threads] 未取得任何 og 標籤 url=%s", url)
        return None

    return LinkPreview(
        platform       = "threads",
        platform_label = "Threads",
        source_label   = f"via {response.url.host}",
        url            = url,
        title          = tags.get("title"),
        description    = tags.get("description"),
        thumbnail_url  = tags.get("image"),
        video_url      = tags.get("video"),
        color          = 0x101010,
    )
