"""
core/link_preview/instagram.py

Modification():

- 修正「影片截取」作法：不再透過 Cog 層下載 og:video 網址重新上傳，
  改為當偵測到影片時，把「本次成功回應的代理網址本身」
  （response.url，例如 https://ddinstagram.com/p/xxx）設為
  embed_video_link，交由 Cog 層當作純文字內容送出，讓 Discord 自己
  的爬蟲原生嵌入播放。這些代理服務本來就是設計給 Discord／Telegram
  的爬蟲讀取用的，讓它們原生處理沒有 Discord 附件的檔案大小上限
  問題，也不需要消耗頻寬下載＋上傳。參考真實案例 FixTweetBot（一款
  成熟的公開 Discord 連結修復 Bot）的做法：它從頭到尾都不下載
  影片，只送出修復後的連結文字。
- _DEFAULT_HOSTS 新增 oginstagram.com：查證真實案例 FixTweetBot
  目前實際採用的 Instagram 修復網域正是 oginstagram.com（且是它
  唯一使用的選擇，沒有其他備援），可信度較高，因此排在第二順位。
- _DEFAULT_HOSTS 新增 instagramez.com：與既有的 ddinstagram /
  kkinstagram 系列由不同開發者獨立維運，單純增加一個不會同時故障
  的候選來源。
- 新增「候選網域全部失敗時，最後嘗試原始網址本身」的備援，與
  threads.py／twitter.py／tiktok.py 統一做法：把解析出的原始
  hostname 併入候選清單最後一位，_INSTA_HOST_RE 替換成同樣的
  host 等於維持原網址不變，讓 try_hosts() 額外多打一次原始網址。
  說明：Instagram 未登入時的 og 標籤通常為空（見下方職責說明），
  這一步不像 Threads 那樣有明確證據會提升成功率，但全部代理都已
  失敗時，多一次「至少試試看」的機會不會讓結果更差，只是不保證
  真的有幫助。
- 修正 ddinstagram.com 網域完全無法解析（DNS gaierror）導致
  Instagram 預覽整個失效的問題：改用 core.link_preview.fallback
  依序嘗試多個候選代理網域，任一個能連上即可，不再綁死單一網域。
  候選清單由 settings.json 的 link_preview.instagram_proxy_hosts
  控制，之後若某個代理服務又停止運作，只需調整設定即可，
  不需要修改程式碼。

職責：

- 將 Instagram 貼文／Reels 連結轉換為 LinkPreview
- Instagram 的 og:* meta 標籤在未登入狀態下通常為空或僅含通用
  說明，因此改用社群維運的公開代理服務，於伺服器端模擬登入後
  回傳完整的 og:title / og:description / og:image（部分服務
  亦提供 og:video），全部代理都失敗時退回原始網址本身
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

from core.link_preview.base import LinkPreview
from core.link_preview.fallback import try_hosts
from core.link_preview.og_meta import extract_og_tags
from core.system.settings import get_list

logger = logging.getLogger("bot.link_preview.instagram")

_INSTA_HOST_RE = re.compile(r"(www\.)?instagram\.com", re.IGNORECASE)

# 候選代理網域的內建後備清單；settings.json 未設定時使用此值
_DEFAULT_HOSTS = ["ddinstagram.com", "oginstagram.com", "instagramez.com", "kkinstagram.com", "d.ddinstagram.com"]


# ── 對外介面 ──────────────────────

async def extract(url: str) -> LinkPreview | None:
    """將 Instagram 連結轉換為 LinkPreview，失敗時回傳 None。"""
    hosts = list(get_list("link_preview.instagram_proxy_hosts", _DEFAULT_HOSTS))

    # 所有代理都失敗時，最後試一次原始網址本身（見上方 Modification 說明）。
    original_host = urlsplit(url).hostname
    if original_host and original_host not in hosts:
        hosts.append(original_host)

    response = await try_hosts(
        build_url      = lambda host: _INSTA_HOST_RE.sub(host, url, count=1),
        hosts          = hosts,
        platform_label = "Instagram",
    )
    if response is None:
        return None

    tags = extract_og_tags(response.text)
    if not tags:
        logger.warning("[Instagram] 未取得任何 og 標籤 url=%s", url)
        return None

    video = tags.get("video")

    return LinkPreview(
        platform         = "instagram",
        platform_label   = "Instagram",
        source_label     = f"via {response.url.host}",
        url              = url,
        title            = tags.get("title"),
        description      = tags.get("description"),
        thumbnail_url    = tags.get("image"),
        video_url        = video,
        embed_video_link = str(response.url) if video else None,
        color            = 0xE1306C,
    )
