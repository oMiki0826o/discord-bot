"""
core/link_preview/og_meta.py

職責：
- 從 HTML 字串中提取 Open Graph meta 標籤（og:title、og:description、
  og:image 等），供 Pinterest、Instagram、Threads 等使用 og:* 的
  擷取器使用。

Modification():

- 新增本檔案：集中 og:* 解析邏輯，避免各平台擷取器各自實作相同的
  正規表示式。
- 使用輕量正規表示式解析方案，維持專案目前不引入 BeautifulSoup 的
  依賴慣例；對於標準靜態 HTML 頁面效果良好，JavaScript 渲染頁面
  因伺服器端回傳的 HTML 本身已含 og:* 標籤所以通常也適用。
- 屬性順序不定問題：og:* 標籤的 property 與 content 屬性出現
  順序不固定（RFC 並未規定），兩種順序皆能正確解析。

解析範例：
    <meta property="og:title" content="影片標題">
    <meta content="影片描述" property="og:description">
"""

from __future__ import annotations

import html
import re

# ── 解析正規表示式 ──────────────────────
# 匹配 <meta> 標籤的完整屬性字串（自閉合或非自閉合皆支援）
_META_TAG_RE = re.compile(r"<meta\s+([^>]+?)(?:\s*/)?>", re.IGNORECASE | re.DOTALL)

# 從屬性字串提取 property="og:xxx" 中的 xxx
_OG_PROP_RE = re.compile(r'property=["\']og:(\w+)["\']', re.IGNORECASE)

# 從屬性字串提取 content="..."
_CONTENT_RE = re.compile(r'content=["\']([^"\']*)["\']', re.IGNORECASE)


# ── 對外介面 ──────────────────────

def extract_og_tags(html_content: str) -> dict[str, str]:
    """
    從 HTML 字串提取 og:* meta 標籤，回傳 {prop: value} 字典。

    例如：{"title": "...", "description": "...", "image": "..."}

    同一屬性出現多次時，以第一筆為準（setdefault 行為）。
    value 中的 HTML 實體字元（&amp; 等）會自動還原為普通字元。
    """
    result: dict[str, str] = {}

    for match in _META_TAG_RE.finditer(html_content):
        attrs = match.group(1)

        prop_match = _OG_PROP_RE.search(attrs)
        if prop_match is None:
            continue

        content_match = _CONTENT_RE.search(attrs)
        if content_match is None:
            continue

        prop  = prop_match.group(1).lower()
        value = html.unescape(content_match.group(1))
        result.setdefault(prop, value)

    return result
