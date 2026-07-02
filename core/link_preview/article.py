"""
core/link_preview/article.py

Modification():
- 新增本檔案：支援「關鍵字 + 網址」觸發的通用摘要功能，不限定平台，
  任何回應 HTML 的網址皆可嘗試擷取純文字內容。

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

logger = logging.getLogger("bot.link_preview.article")

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


async def fetch_text(url: str, *, max_chars: int) -> str | None:
    """
    抓取網頁並回傳清理過的純文字內容；請求失敗、非文字內容、或清理
    後為空時皆回傳 None，由呼叫端統一視為「無法爬取」。
    """
    async with build_client() as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except Exception:
            logger.exception("[文章擷取] 請求失敗 url=%s", url)
            return None

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type and "text" not in content_type:
        logger.info(
            "[文章擷取] 非文字內容，略過 url=%s content_type=%s", url, content_type
        )
        return None

    text = _html_to_text(response.text)
    if not text:
        logger.info("[文章擷取] 清理後內容為空 url=%s", url)
        return None
    return text[:max_chars]


def _html_to_text(raw_html: str) -> str:
    """粗略清理 HTML：移除 script/style 與所有標籤，還原實體字元並壓縮空白。"""
    cleaned = _SCRIPT_STYLE_RE.sub(" ", raw_html)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned
