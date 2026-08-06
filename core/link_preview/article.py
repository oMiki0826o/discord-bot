"""
core/link_preview/article.py

Modification():

- 修正 SSRF（Server-Side Request Forgery）風險：原本直接對使用者
  在「摘要 <網址>」訊息裡打的任意網址發送請求，沒有做任何驗證，
  使用者可以讓 Bot 對內網服務或雲端 metadata 端點（例如
  169.254.169.254）發送請求。改為：
  1. 發送前先用 core.link_preview.url_guard.is_safe_url() 驗證
     scheme 與解析出的 IP 是否落在私有／迴路／連結本地等範圍。
  2. 關閉 httpx 的自動追隨重定向（build_client(follow_redirects=
     False)），改為手動迴圈逐跳處理：每一次追隨重定向前，都對
     「重定向目標」重新呼叫 is_safe_url() 驗證。原本
     follow_redirects=True 只驗證了最初的網址，之後的每一跳
     重定向完全沒有驗證，等於檢查形同虛設——伺服器只要回應一個
     指向內網位址的 3xx，就能繞過最初那一次檢查。
  3. 新增回應大小上限（link_preview.article_fetch_max_bytes，
     預設 3MB）：原本用 response.text 一次性讀入整個回應內容，
     沒有任何大小限制，一個刻意回傳超大內容的惡意網站可能造成
     Bot 記憶體用量失控。改用串流讀取，累積超過上限就停止讀取
     （已讀取的部分仍會嘗試擷取文字，不會直接整個放棄）。

職責：
- 抓取任意網址並清理成可讀純文字，供 summarizer.py 生成摘要使用。

備註：
- 以正規表示式做輕量清理（移除 script/style、剝除標籤、還原 HTML
  實體字元），維持專案目前不引入 BeautifulSoup / lxml 的依賴慣例。
  對於高度依賴 JavaScript 渲染內容的網站（例如 Bilibili／Instagram
  等單頁應用），伺服器端回傳的 HTML 本身文字量就很少，擷取結果
  可能不理想；這屬於純文字擷取方式的固有限制，而非程式錯誤。
"""

from __future__ import annotations

import html
import logging
import re

from core.link_preview.http import build_client
from core.link_preview.url_guard import is_safe_url
from core.system.settings import get_int

logger = logging.getLogger("bot.link_preview.article")

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")

# 最多追隨幾次重定向；超過視為異常（多半是重定向迴圈），直接放棄。
_MAX_REDIRECTS = 5


async def fetch_text(url: str, *, max_chars: int) -> str | None:
    """
    抓取網頁並回傳清理過的純文字內容；請求失敗、非文字內容、清理
    後為空、或未通過 SSRF 安全檢查時皆回傳 None，由呼叫端統一視為
    「無法爬取」。
    """
    max_bytes = get_int("link_preview.article_fetch_max_bytes", 3_000_000)
    current_url = url

    async with build_client(follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS):
            safe, reason = is_safe_url(current_url)
            if not safe:
                logger.warning(
                    "[文章擷取] 網址未通過安全檢查 url=%s reason=%s",
                    current_url, reason,
                )
                return None

            try:
                async with client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            logger.info(
                                "[文章擷取] 重定向缺少 Location url=%s", current_url
                            )
                            return None
                        # Location 可能是相對路徑，需相對於目前網址解析
                        current_url = str(response.url.join(location))
                        continue

                    response.raise_for_status()

                    content_type = response.headers.get("content-type", "")
                    if "html" not in content_type and "text" not in content_type:
                        logger.info(
                            "[文章擷取] 非文字內容，略過 url=%s content_type=%s",
                            current_url, content_type,
                        )
                        return None

                    raw_bytes = await _read_capped(response, max_bytes)
            except Exception:
                logger.exception("[文章擷取] 請求失敗 url=%s", current_url)
                return None

            raw_html = raw_bytes.decode(
                response.encoding or "utf-8", errors="replace"
            )
            text = _html_to_text(raw_html)
            if not text:
                logger.info("[文章擷取] 清理後內容為空 url=%s", current_url)
                return None
            return text[:max_chars]

    logger.warning("[文章擷取] 重定向次數過多，放棄 url=%s", url)
    return None


async def _read_capped(response, max_bytes: int) -> bytes:
    """
    串流讀取回應內容，累積超過 max_bytes 就停止（保留已讀取的部分，
    不整個放棄），避免惡意網站回傳超大內容造成記憶體用量失控。
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            logger.info(
                "[文章擷取] 回應內容超過大小上限 %d bytes，僅使用已讀取的部分",
                max_bytes,
            )
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _html_to_text(raw_html: str) -> str:
    """粗略清理 HTML：移除 script/style 與所有標籤，還原實體字元並壓縮空白。"""
    cleaned = _SCRIPT_STYLE_RE.sub(" ", raw_html)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned
