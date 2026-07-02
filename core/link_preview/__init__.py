"""
core/link_preview/__init__.py

職責：
- core.link_preview 的 Package 標記，無實作內容。
- 子模組清單：
    base           LinkPreview / LinkStat 資料模型
    flags          get_flag() 讀取 settings.json 的布林設定
    http           build_client() httpx 非同步客戶端
    og_meta        extract_og_tags() HTML og 標籤解析
    video          影片 URL 解析與限制下載
    detector       detect_links() 從訊息偵測支援平台的連結
    registry       get_extractor() 平台字串對應擷取器函式
    article        fetch_text() 通用網頁純文字擷取
    summary_trigger find_summary_request() 關鍵字觸發偵測
    summarizer     summarize() Gemma 摘要生成
    bilibili       Bilibili 影片 Embed 擷取器
    instagram      Instagram 貼文 Embed 擷取器
    pinterest      Pinterest Pin Embed 擷取器
    threads        Threads 貼文 Embed 擷取器

Modification():

- 新增本 package：整合使用者提供的連結預覽系統。
"""
