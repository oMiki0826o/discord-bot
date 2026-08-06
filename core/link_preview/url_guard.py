"""
core/link_preview/url_guard.py

Modification():

- 新增本檔案：為「任意使用者提供網址」的請求提供 SSRF
  （Server-Side Request Forgery，伺服器端請求偽造）防護。

  背景：core.link_preview 底下多數擷取器（bilibili / instagram /
  threads / twitter / tiktok / pinterest）都只會連到我們自己在
  settings.json 或程式碼常數裡設定的固定代理網域，使用者無法透過
  訊息內容控制實際連線目標，不需要這層防護。但
  core.link_preview.article 的 fetch_text()（「摘要 <任意網址>」
  這個關鍵字觸發功能）相反：使用者在訊息裡打的網址會被直接拿去
  發送請求。若不驗證，使用者可以讓 Bot 對內網服務發送請求（例如
  http://localhost:PORT/ 探測內部服務、http://192.168.x.x/ 探測
  區域網路），或對雲端 metadata 端點發送請求（AWS / GCP / Azure
  皆使用 169.254.169.254 這個連結本地位址，若 Bot 架設在對應雲端
  環境，可能洩漏 IAM 憑證等敏感資訊）。這是這一類「把使用者輸入
  的網址原封不動拿去發送請求」功能的典型風險，因此新增這一層
  驗證，僅套用在 article.py 這個唯一有此風險的呼叫端。

殘留風險（已知限制，非本次範圍）：
- 本模組在「發送請求前」驗證 DNS 解析結果，屬於請求時間點的
  檢查，無法完全防禦 DNS rebinding（攻擊者控制的網域，在驗證當下
  回應安全 IP，實際連線瞬間才回應內網 IP 這種進階攻擊手法）。
  完整防禦需要釘住（pin）驗證當下解析到的 IP，強制連線時使用
  同一個 IP、不再重新查詢 DNS，這需要客製化 httpx 的連線層，
  超出本次修正範圍，先以請求時驗證加上重定向逐跳驗證，防禦
  絕大多數常見情境。

職責：
- is_safe_url()：驗證單一網址的 scheme 與其解析出的所有 IP
  位址，判斷是否可安全發送請求。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlsplit

logger = logging.getLogger("bot.link_preview.url_guard")

_ALLOWED_SCHEMES = frozenset({"http", "https"})


def is_safe_url(url: str) -> tuple[bool, str]:
    """
    驗證網址是否可安全發送請求。回傳 (是否安全, 原因說明)；
    安全時原因說明為空字串。

    檢查項目：
    1. scheme 僅允許 http / https（拒絕 file:// 等其他協定）
    2. hostname 必須能解析出至少一個 IP
    3. 解析出的每一個 IP 都不能落在私有網段、迴路、連結本地
       （含雲端 metadata 端點）、多播、保留位址等範圍內

    只驗證「這一次要連線的網址」本身；若後續會追隨重定向，
    呼叫端必須在每一次追隨前，對新網址重新呼叫本函式再次驗證
    （見 article.py 的手動重定向迴圈），否則第一層檢查通過後，
    仍可能被伺服器以 3xx 導向到內網位址，繞過檢查。
    """
    parts = urlsplit(url)

    if parts.scheme not in _ALLOWED_SCHEMES:
        return False, f"不支援的協定：{parts.scheme or '(空白)'}"

    hostname = parts.hostname
    if not hostname:
        return False, "網址缺少主機名稱"

    try:
        # getaddrinfo 解析出該主機名稱對應的所有 IP（同時涵蓋 IPv4 與 IPv6）
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        return False, f"無法解析主機名稱：{e}"

    if not infos:
        return False, "主機名稱未解析出任何位址"

    for info in infos:
        raw_ip = info[4][0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            return False, f"無法解析的 IP 格式：{raw_ip}"

        if (
            ip.is_private        # 10/8、172.16/12、192.168/16 等內網網段
            or ip.is_loopback     # 127.0.0.0/8、::1
            or ip.is_link_local   # 169.254.0.0/16（含雲端 metadata 端點）、fe80::/10
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified  # 0.0.0.0、::
        ):
            return False, f"目標位址不可存取：{raw_ip}"

    return True, ""
