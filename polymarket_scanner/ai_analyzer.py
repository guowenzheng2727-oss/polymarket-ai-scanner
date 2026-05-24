"""
Polymarket AI Scanner - DeepSeek AI 分析模块 v2.1
v2.0: 基础 LLM 分析
v2.1: RAG 增强 — 分层搜索策略 + 市场分类 + 定向新闻检索
"""

import os
import re

# 自动加载 .env（适配 Streamlit Cloud secrets 优先）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ============================================================
# DeepSeek 客户端
# ============================================================


def get_deepseek_client():
    """获取 DeepSeek 客户端（OpenAI 兼容接口）
    返回 (client, error_message)
    """
    try:
        from openai import OpenAI
    except ImportError:
        return None, "❌ 需要安装 openai 库: pip install openai"

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        try:
            import streamlit as st
            api_key = st.secrets.get("DEEPSEEK_API_KEY", "")
        except Exception:
            pass

    if not api_key:
        return None, "⚠️ 未配置 DEEPSEEK_API_KEY（请在 .env 或 Streamlit secrets 中设置）"

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    return client, None


# ============================================================
# 市场分类器 — 决定搜什么
# ============================================================

# 关键词 → 市场类型映射
_CATEGORY_PATTERNS = [
    ("sports", [
        r"\b(NBA|NFL|MLB|NHL|Premier League|La Liga|Serie A|Bundesliga|Champions League|UFC|boxing|F1|tennis|Grand Slam|Wimbledon|World Cup|Olympic|Super Bowl|playoff|championship)\b",
        r"\b(vs\.?|versus|defeats?|wins?|loses?|scores?|points?|touchdown|home run|goals?|knockout)\b",
        r"\b(Knicks|Lakers|Celtics|Warriors|Nets|Heat|Bucks|Nuggets|Mavericks|76ers|Cavaliers|Thunder|Timberwolves|Yankees|Dodgers|Red Sox|Patriots|Chiefs|Cowboys)\b",
        r"\b(spread|over/under|prop bet|MVP|Rookie of the Year)\b",
    ]),
    ("politics", [
        r"\b(Trump|Biden|Xi|Putin|Zelensky|Macron|Modi|president|election|vote|poll|congress|senate|parliament|democrat|republican|GOP|DNC)\b",
        r"\b(sanctions?|tariff|trade war|executive order|bill|legislation|impeach|scandal|campaign|rally|debate|primary|midterm)\b",
        r"\b(White House|Kremlin|Downing Street|UN|NATO|EU|OPEC|Fed|Federal Reserve)\b",
    ]),
    ("crypto_finance", [
        r"\b(Bitcoin|BTC|Ethereum|ETH|Solana|SOL|crypto|blockchain|DeFi|NFT|token|altcoin|stablecoin|halving|staking|mining)\b",
        r"\b(S&P 500|NASDAQ|Dow Jones|stock market|interest rate|inflation|CPI|GDP|unemployment|recession|bull market|bear market|IPO|SPAC)\b",
        r"\b(Fed|ECB|BOJ|central bank|rate hike|rate cut|QE|quantitative|yield curve|bond|treasury)\b",
        r"\b(\\$[0-9,]+[KMB]?|market cap|ATH|all.time.high)\b",
    ]),
    ("entertainment", [
        r"\b(movie|film|box office|Oscar|Academy Award|Grammy|Emmy|Golden Globe|Netflix|Disney|Marvel|DC|Star Wars)\b",
        r"\b(album|single|chart|Billboard|Spotify|streaming|concert|tour|festival|Coachella)\b",
        r"\b(celebrity|actor|actress|director|singer|rapper|influencer)\b",
    ]),
    ("science_tech", [
        r"\b(AI|artificial intelligence|GPT|LLM|OpenAI|Google|Apple|Microsoft|Meta|Tesla|SpaceX|NASA|rocket|launch|satellite)\b",
        r"\b(quantum|fusion|breakthrough|discovery|study|research|paper|clinical trial|FDA|vaccine|drug)\b",
    ]),
]


def classify_market(question: str, tags: str = "") -> str:
    """根据问题和标签判断市场类型。

    Returns:
        "sports" | "politics" | "crypto_finance" | "entertainment" | "science_tech" | "general"
    """
    # 安全处理 tags：pandas NaN / None / 非字符串都兜底
    tags_str = ""
    if tags is not None and isinstance(tags, str):
        tags_str = tags
    text = (question + " " + tags_str).lower()
    scores = {}
    for category, patterns in _CATEGORY_PATTERNS:
        score = sum(1 for p in patterns if re.search(p, question, re.IGNORECASE))
        if score > 0:
            scores[category] = score

    if not scores:
        return "general"
    return max(scores, key=scores.get)


# ============================================================
# 搜索策略选择器 — ROI 分诊
# ============================================================


def should_search(ev_score: int, yes_price: float, volume: float, ie_score: int = 0) -> tuple:
    """判断是否需要搜索，以及搜索深度。

    Returns:
        (search_depth, skip_reason)
        search_depth: 0=跳过, 1=快速, 2=深度
    """
    # 价格极端 → 市场已有结论，搜索无意义
    if yes_price < 0.05 or yes_price > 0.95:
        return 0, "价格极端（<5% 或 >95%），市场已有结论"

    # 信息优势高 → 必须搜，这是找信息差的核心
    if ie_score >= 7:
        return 2, ""
    if ie_score >= 5:
        # IE 够高但 EV 中等 → 至少快速搜
        return 2 if ev_score >= 7 else 1, ""

    # 低 EV + 低成交量 → 不值得
    if ev_score < 5 and volume < 50000:
        return 0, "EV 低且成交量低，不值得搜索"
    # 高 EV → 深度搜索
    if ev_score >= 8:
        return 2, ""
    # 中等 EV → 快速搜索
    if ev_score >= 5:
        return 1, ""
    # 其余跳过
    return 0, "EV 不足"


# ============================================================
# 搜索查询构建器 — 按市场类型定向搜
# ============================================================


def build_search_queries(question: str, market_type: str) -> list:
    """根据市场类型生成定向搜索词列表。

    策略：不同市场类型搜不同的关键信息。
    - 体育：搜伤病、阵容、近期战绩
    - 政治：搜当事人最新言论、民调
    - 加密/金融：搜行情分析、政策动态
    - 娱乐：搜票房、收视率、话题热度
    - 科技：搜最新进展、里程碑
    - 通用：直接搜问题核心关键词
    """
    # 提取关键实体：去重、去停用词、保留前 8 个词
    stopwords = {"will", "the", "a", "an", "is", "be", "to", "of", "in", "on", "at",
                 "for", "with", "by", "from", "or", "and", "not", "no", "but", "if",
                 "has", "have", "was", "were", "are", "can", "could", "would", "should",
                 "this", "that", "these", "those", "it", "its", "his", "her", "their"}
    words = [w for w in question.split() if w.lower() not in stopwords]
    # 保留标点但去掉末尾标点
    entities = " ".join(words[:10])
    entities = entities.rstrip(",.?!;:")

    queries = []

    if market_type == "sports":
        # 核心：伤病报告 + 赛前分析
        queries.append(f"{entities} injury report lineup today")
        queries.append(f"{entities} preview analysis latest")

    elif market_type == "politics":
        # 核心：当事人最新言论 + 民调
        queries.append(f"{entities} latest statement news today")
        queries.append(f"{entities} poll survey recent")

    elif market_type == "crypto_finance":
        # 核心：行情分析 + 政策
        queries.append(f"{entities} price analysis today")
        queries.append(f"{entities} news policy regulation latest")

    elif market_type == "entertainment":
        # 核心：票房 / 收视率 / 话题
        queries.append(f"{entities} box office ratings latest")
        queries.append(f"{entities} trending news today")

    elif market_type == "science_tech":
        # 核心：最新进展 / 突破
        queries.append(f"{entities} latest breakthrough milestone")
        queries.append(f"{entities} news update today")

    else:
        # 通用：直接搜问题
        queries.append(f"{entities} latest news")
        queries.append(f"{entities} update today")

    return queries


# ============================================================
# DuckDuckGo 搜索 — 免费，无需 API Key
# ============================================================


def _google_news_rss(query: str, max_results: int = 5, timeout: int = 10) -> list:
    """[云端降级] Google News RSS 搜索 — 几乎不会被封。

    RSS 端点不需要 JS、不触发 CAPTCHA，是最稳定的云端新闻源。
    自动跟随 Google News 的重定向链获取真实文章 URL。

    Returns:
        [(title, snippet, url), ...] — 失败时返回空列表
    """
    import httpx
    import xml.etree.ElementTree as ET
    from bs4 import BeautifulSoup

    try:
        r = httpx.get(
            "https://news.google.com/rss/search",
            params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/rss+xml,application/xml,text/xml",
            },
            timeout=timeout,
            follow_redirects=True,
        )
    except Exception:
        return []

    if r.status_code != 200:
        return []

    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return []

    items = root.findall(".//item")
    results = []
    for item in items[:max_results]:
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")

        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        gnews_url = link_el.text.strip() if link_el is not None and link_el.text else ""
        desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""

        # 清洗 description 中的 HTML 标签
        if desc:
            desc = BeautifulSoup(desc, "html.parser").get_text()[:300]

        # 跟随 Google News 重定向获取真实 URL
        real_url = gnews_url
        if "news.google.com/rss/articles/" in gnews_url:
            try:
                rr = httpx.get(gnews_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5, follow_redirects=False)
                if rr.status_code in (301, 302):
                    real_url = rr.headers.get("Location", gnews_url)
                else:
                    # Google News 有时候用 meta refresh
                    from bs4 import BeautifulSoup as BS
                    soup = BS(rr.text, "html.parser")
                    meta = soup.find("meta", attrs={"http-equiv": "refresh"})
                    if meta and meta.get("content"):
                        content = meta["content"]
                        if "url=" in content:
                            real_url = content.split("url=", 1)[1].strip()
            except Exception:
                pass  # 保持 gnews_url

        if title:
            results.append((title, desc, real_url))

    return results


def _bing_news_rss(query: str, max_results: int = 5, timeout: int = 10) -> list:
    """[备用] Bing News RSS 搜索 — 无需 API Key。

    Returns:
        [(title, snippet, url), ...] — 失败时返回空列表
    """
    import httpx
    import xml.etree.ElementTree as ET

    try:
        r = httpx.get(
            "https://www.bing.com/news/search",
            params={"q": query, "format": "rss"},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/rss+xml,application/xml,text/xml",
            },
            timeout=timeout,
            follow_redirects=True,
        )
    except Exception:
        return []

    if r.status_code != 200:
        return []

    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return []

    items = root.findall(".//item")
    results = []
    for item in items[:max_results]:
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")

        title = title_el.text.strip() if title_el is not None and title_el.text else ""
        url = link_el.text.strip() if link_el is not None and link_el.text else ""
        desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""

        if title:
            results.append((title, desc[:300], url))

    return results


def _ddg_html_search(query: str, max_results: int = 5, timeout: int = 10) -> list:
    """[降级方案] 用 httpx 直接请求 DDG HTML 搜索页面，解析结果。

    当 ddgs 库不可用时尝试，但 DDG 对云端 IP 可能返回挑战页（202）。

    Returns:
        [(title, snippet, url), ...] — 失败时返回空列表
    """
    import httpx
    from bs4 import BeautifulSoup

    try:
        r = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=timeout,
            follow_redirects=True,
        )
    except Exception:
        return []

    if r.status_code != 200:
        return []

    soup = BeautifulSoup(r.text, "lxml")
    results = []
    for el in soup.select(".result")[:max_results]:
        title_el = el.select_one(".result__title a")
        snippet_el = el.select_one(".result__snippet")
        if title_el:
            title = title_el.get_text(strip=True)
            url = title_el.get("href", "")
            # 提取真实 URL（DDG 的 uddg 参数）
            from urllib.parse import urlparse, parse_qs
            if "uddg=" in url:
                parsed = parse_qs(urlparse(url).query)
                url = parsed.get("uddg", [url])[0]
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            results.append((title, snippet, url))

    return results


def search_news(query: str, max_results: int = 5, timeout: int = 10) -> list:
    """多引擎搜索新闻，返回 [(title, snippet, url), ...]

    五级降级策略（适配本地 + 云端 IP 封禁）：
    1. Google News RSS（云端最稳定，优先 — 几乎永不被封）
    2. ddgs 库 → news 搜索（本地最快最准）
    3. ddgs 库 → text 搜索（news 不可用时降级）
    4. httpx → DDG HTML 直搜（最后手段，取决于 IP）
    5. Bing News RSS（终极备用）

    不用 API Key，免费。云端和本地均可用。
    """
    import httpx

    # —— 策略 1: Google News RSS（最稳定的云端方案，优先）——
    rss_results = _google_news_rss(query, max_results=max_results, timeout=timeout)
    if rss_results:
        return rss_results

    # —— 策略 2+3: ddgs 库 ——
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # 旧版兼容
        except ImportError:
            ddgs_available = False
            pass
    else:
        ddgs_available = True

    ddgs_failed = False
    if ddgs_available:
        try:
            with DDGS() as ddgs:
                results = list(ddgs.news(query, max_results=max_results, timelimit="w"))
            output = []
            for r in results:
                title = (r.get("title") or "").strip()
                body = (r.get("body") or r.get("snippet") or "").strip()
                url = (r.get("url") or r.get("link") or "").strip()
                if title or body:
                    output.append((title, body, url))
            if output:
                return output[:max_results]
        except Exception:
            # 可能被限流，降级到 text 搜索
            try:
                with DDGS() as ddgs:
                    raw = list(ddgs.text(query, max_results=max_results))
                output = []
                for r in raw:
                    title = (r.get("title") or "").strip()
                    body = (r.get("body") or "").strip()
                    url = (r.get("href") or "").strip()
                    if title or body:
                        output.append((title, body, url))
                if output:
                    return output[:max_results]
            except Exception:
                ddgs_failed = True

    # —— 策略 4: Bing News RSS ——
    bing_results = _bing_news_rss(query, max_results=max_results, timeout=timeout)
    if bing_results:
        return bing_results

    # —— 策略 5: DDG HTML 直搜（最后手段）——
    html_results = _ddg_html_search(query, max_results=max_results, timeout=timeout)
    if html_results:
        return html_results

    # 全部失败
    return [("", f"⚠️ 搜索暂时不可用（Google News RSS 无结果 + DDG {'失败' if ddgs_failed else '未安装'} + Bing RSS 无结果）", "")]


# ============================================================
# 全文抓取 — 从 URL 提取正文（替代 snippet-only）
# ============================================================

# 常见反爬 UA
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
}

# 需要移除的标签/属性（脚本、样式、导航等非正文内容）
_REMOVE_TAGS = ["script", "style", "nav", "footer", "header", "aside",
                "noscript", "iframe", "form", "button", "input", "select"]

# 弃用标记：部分网站用 JS 动态渲染，纯 HTTP GET 拿不到正文
_SKIP_DOMAINS = {
    "twitter.com", "x.com", "instagram.com", "facebook.com",
    "reddit.com",  # Reddit 返回 JSON 而非可读正文
}


def fetch_article(url: str, max_chars: int = 2000, timeout: int = 10) -> str:
    """从 URL 抓取全文正文。

    策略：
    1. HTTP GET → 检测 Content-Type（跳过非 HTML）
    2. BeautifulSoup 解析 → 移除 script/style/nav 等噪音标签
    3. 提取 <article> / <main> / <body> 中的可见文本
    4. 清洗空白 → 截取前 max_chars 字符

    Args:
        url: 文章 URL
        max_chars: 最大返回字符数
        timeout: HTTP 超时秒数

    Returns:
        正文文本字符串；失败时返回空字符串
    """
    import httpx

    # 跳过明确不可抓的域名
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower()
    domain = domain.removeprefix("www.")
    if any(blocked in domain for blocked in _SKIP_DOMAINS):
        return ""

    try:
        r = httpx.get(url, headers=_FETCH_HEADERS, timeout=timeout, follow_redirects=True)
    except Exception:
        return ""

    if r.status_code != 200:
        return ""

    ct = r.headers.get("content-type", "")
    if "html" not in ct and "text" not in ct:
        # 不是 HTML/文本 — 跳过（PDF、图片等）
        return ""

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    # —— HTML → 正文 ——
    soup = BeautifulSoup(r.text, "lxml")

    # 移除噪音标签
    for tag in _REMOVE_TAGS:
        for el in soup.find_all(tag):
            el.decompose()

    # 移除 hidden 元素
    for el in soup.find_all(attrs={"hidden": True}):
        el.decompose()
    for el in soup.find_all(style=re.compile(r"display\s*:\s*none")):
        el.decompose()

    # 优先取 <article> 或 <main>，否则取 <body>
    main = soup.find("article") or soup.find("main") or soup.find("body")
    if main is None:
        return ""

    text = main.get_text(separator="\n")

    # 清洗：压缩连续空行 → 截断
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [l.strip() for l in text.split("\n")]
    lines = [l for l in lines if l]  # 去空行

    cleaned = "\n".join(lines)
    if len(cleaned) > max_chars:
        # 尽量在句号处截断
        cut = cleaned.rfind("。", 0, max_chars)
        if cut == -1:
            cut = cleaned.rfind(". ", 0, max_chars)
        if cut > max_chars * 0.5:
            cleaned = cleaned[:cut + 1] + "\n...[截断]"
        else:
            cleaned = cleaned[:max_chars] + "\n...[截断]"

    return cleaned


def _ddg_search_with_fallback(queries: list, question: str) -> list:
    """DDG 搜索 + 2 级降级重试，返回 [(title, snippet, url), ...]"""

    _STOP = {"will", "the", "a", "an", "is", "be", "to", "of", "in", "on", "at",
             "for", "with", "by", "from", "or", "and", "not", "no", "but", "if",
             "has", "have", "was", "were", "are", "can", "could", "would", "should",
             "this", "that", "these", "those", "it", "its", "his", "her", "their"}

    all_results = []
    for q in queries:
        results = search_news(q, max_results=5)
        for title, body, url in results:
            if title and body and not title.startswith("⚠️"):
                all_results.append((title, body, url))

    if not all_results:
        # 降级1: 核心实体
        core = " ".join([w for w in question.split() if w.lower() not in _STOP][:5])
        results = search_news(f"{core} latest news", max_results=5)
        for title, body, url in results:
            if title and body and not title.startswith("⚠️"):
                all_results.append((title, body, url))

    if not all_results:
        # 降级2: question 前半段
        short_q = " ".join(question.split()[:8])
        results = search_news(short_q, max_results=5)
        for title, body, url in results:
            if title and body and not title.startswith("⚠️"):
                all_results.append((title, body, url))

    return all_results


# ============================================================
# Layer 2: Twitter/X 实时情绪搜索
# ============================================================

# 触发 Twitter 搜索的阈值
_TWITTER_MIN_IE_SCORE = 7   # ie_score ≥ 7 才加 Twitter
_TWITTER_MIN_EV_SCORE = 8   # 或者 ev_score ≥ 8

# 成本控制
_TWITTER_MAX_TWEETS = 5       # 每个市场最多搜 5 条推文
_TWITTER_SEARCH_DAYS = 7      # 搜最近 7 天


def _build_twitter_query(question: str, market_type: str) -> str:
    """从市场问题提取 Twitter 搜索关键词。

    策略：提取 3-5 个核心词，用 Twitter 高级搜索语法组合。
    """
    _STOP = {"will", "the", "a", "an", "is", "be", "to", "of", "in", "on", "at",
             "for", "with", "by", "from", "or", "and", "not", "no", "but", "if",
             "has", "have", "was", "were", "are", "can", "could", "would", "should",
             "this", "that", "these", "those", "it", "its", "his", "her", "their",
             "what", "which", "who", "whom", "how", "when", "where", "do", "does",
             "did", "more", "than", "any", "all", "some", "every", "each", "just"}

    words = question.split()
    core = [w for w in words if w.lower() not in _STOP][:5]

    if not core:
        core = words[:4]

    # 给重要词加引号确保精确匹配
    quoted = " ".join(f'"{w}"' for w in core)
    return quoted


def search_twitter(question: str, market_type: str, max_tweets: int = _TWITTER_MAX_TWEETS) -> dict:
    """搜索 Twitter/X 获取实时情绪数据。

    使用 twitterapi.io Advanced Search API。

    Args:
        question: 市场问题文本
        market_type: 市场类型（sports/politics/crypto/finance 等）
        max_tweets: 最大推文数

    Returns:
        {
            "tweets": [{"text": str, "likes": int, "retweets": int, "author": str, "url": str}, ...],
            "error": str | None,     # None = 成功
            "query": str,            # 实际使用的搜索词
        }
    """
    import httpx

    api_key = os.getenv("TWITTER_API_KEY", "")
    if not api_key:
        return {"tweets": [], "error": "未配置 TWITTER_API_KEY", "query": ""}

    # 构建搜索词
    raw_query = _build_twitter_query(question, market_type)

    # 添加时间范围（Unix 时间戳）
    now_ts = int(__import__("time").time())
    since_ts = now_ts - _TWITTER_SEARCH_DAYS * 86400
    query = f"{raw_query} since_time:{since_ts} until_time:{now_ts}"
    # 注意：since_time/until_time 可能不被完全支持，先放 query 里试试

    try:
        r = httpx.get(
            "https://api.twitterapi.io/twitter/tweet/advanced_search",
            params={
                "query": raw_query,       # query 放 params，不手工拼接时间
                "queryType": "Latest",
                "cursor": "",
            },
            headers={
                "X-API-Key": api_key,
                "Accept": "application/json",
            },
            timeout=15,
        )
    except Exception as e:
        return {"tweets": [], "error": f"请求失败: {e}", "query": raw_query}

    if r.status_code != 200:
        return {"tweets": [], "error": f"HTTP {r.status_code}: {r.text[:200]}", "query": raw_query}

    try:
        data = r.json()
    except Exception:
        return {"tweets": [], "error": "JSON 解析失败", "query": raw_query}

    tweets_raw = data.get("tweets", [])
    if not tweets_raw:
        return {"tweets": [], "error": None, "query": raw_query}  # 无结果是正常的

    tweets = []
    for t in tweets_raw[:max_tweets]:
        # 跳过转推（用 retweeted_tweet 字段判断）
        if t.get("retweeted_tweet"):
            # 如果是转推，用原文内容
            rt = t["retweeted_tweet"]
            text = rt.get("text", "")
            author = rt.get("author", {}).get("userName", "unknown")
        else:
            text = t.get("text", "")
            author = t.get("author", {}).get("userName", "unknown")

        tweets.append({
            "text": text,
            "likes": t.get("likeCount", 0),
            "retweets": t.get("retweetCount", 0),
            "author": author,
            "url": t.get("url", ""),
        })

    return {"tweets": tweets, "error": None, "query": raw_query}


# ============================================================
# Layer 3: 专题增强 — 按市场类型注入结构化数据
# ============================================================

# CoinGecko 免费 API（无需 Key，速率限制 30 req/min）
_COINGECKO_API = "https://api.coingecko.com/api/v3"

# 缓存简单的价格数据（全局 dict，避免重复请求）
_crypto_cache: dict = {}
_crypto_cache_ts: float = 0.0
_CACHE_TTL = 300  # 5 分钟缓存

# Oddpool 跨平台赔率（免费层：1K req/month，需注册 https://oddpool.com）
_ODDPOOL_API = "https://api.oddpool.com"

# Layer 3 触发条件（比 Layer 2 更严格，控制 API 用量）
_L3_MIN_IE_SCORE = 8  # ie_score ≥ 8 才启用专题增强
_L3_MIN_EV_SCORE = 8  # 或者 ev_score ≥ 8


def _get_crypto_prices() -> dict:
    """获取 BTC/ETH 实时价格（CoinGecko 免费 API，带缓存）。

    Returns:
        {"btc": {"usd": float, "change_24h": float}, "eth": {...}, "error": str|None}
    """
    import time as _time
    import httpx
    global _crypto_cache, _crypto_cache_ts

    now = _time.time()
    if _crypto_cache and (now - _crypto_cache_ts) < _CACHE_TTL:
        return _crypto_cache

    try:
        r = httpx.get(
            f"{_COINGECKO_API}/simple/price",
            params={
                "ids": "bitcoin,ethereum",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            timeout=8,
        )
    except Exception as e:
        return {"btc": {}, "eth": {}, "error": f"CoinGecko 请求失败: {e}"}

    if r.status_code != 200:
        return {"btc": {}, "eth": {}, "error": f"CoinGecko HTTP {r.status_code}"}

    try:
        data = r.json()
    except Exception:
        return {"btc": {}, "eth": {}, "error": "CoinGecko JSON 解析失败"}

    result = {
        "btc": {
            "usd": data.get("bitcoin", {}).get("usd"),
            "change_24h": data.get("bitcoin", {}).get("usd_24h_change"),
        },
        "eth": {
            "usd": data.get("ethereum", {}).get("usd"),
            "change_24h": data.get("ethereum", {}).get("usd_24h_change"),
        },
        "error": None,
    }
    _crypto_cache = result
    _crypto_cache_ts = now
    return result


def search_cross_platform(question: str) -> dict:
    """搜索 Kalshi/PredictIt 同主题市场（Oddpool API）。

    需要 OODPOOL_API_KEY 环境变量。免费注册: https://oddpool.com

    Returns:
        {
            "matches": [{"title": str, "exchange": str, "price": float, "volume": float}, ...],
            "error": str | None,
        }
    """
    api_key = os.getenv("ODDPOOL_API_KEY", "")
    if not api_key:
        return {"matches": [], "error": "未配置 OODPOOL_API_KEY（免费注册 https://oddpool.com）"}

    import httpx

    # 提取核心关键词（前 5 个有效词）
    words = [w for w in question.split() if len(w) > 2][:5]
    search_q = " ".join(words)

    try:
        r = httpx.get(
            f"{_ODDPOOL_API}/search/events",
            params={"q": search_q},
            headers={"X-API-Key": api_key},
            timeout=10,
        )
    except Exception as e:
        return {"matches": [], "error": f"Oddpool 请求失败: {e}"}

    if r.status_code == 401 or r.status_code == 403:
        return {"matches": [], "error": "Oddpool API Key 无效或免费层不支持此端点"}
    if r.status_code == 500:
        return {"matches": [], "error": "Oddpool 服务暂时不可用（500），自动降级跳过"}
    if r.status_code != 200:
        return {"matches": [], "error": f"Oddpool HTTP {r.status_code}"}

    try:
        results = r.json()
    except Exception:
        return {"matches": [], "error": "Oddpool JSON 解析失败"}

    matches = []
    for item in results[:5]:
        if not isinstance(item, dict):
            continue
        matches.append({
            "title": item.get("title", ""),
            "exchange": item.get("exchange", "unknown"),
            "price": item.get("last_price") or item.get("yes_ask") or 0,
            "volume": item.get("total_volume") or item.get("volume", 0),
        })

    return {"matches": matches, "error": None}


def get_special_context(question: str, market_type: str, tags: str = "") -> str:
    """按市场类型选择专题增强数据源（Layer 3）。

    Returns:
        格式化的增强上下文字符串；无增强时返回空字符串
    """
    parts = []

    # —— 加密市场 → CoinGecko 实时价格 ——
    if market_type in ("crypto", "crypto_finance"):
        crypto = _get_crypto_prices()
        if not crypto.get("error"):
            btc = crypto.get("btc", {})
            eth = crypto.get("eth", {})
            parts.append("【Layer 3: 加密市场实时数据】")
            if btc.get("usd"):
                chg = btc.get("change_24h", 0) or 0
                arrow = "📈" if chg > 0 else ("📉" if chg < 0 else "➡️")
                parts.append(f"BTC: ${btc['usd']:,.0f} (24h {chg:+.2f}%) {arrow}")
            if eth.get("usd"):
                chg = eth.get("change_24h", 0) or 0
                arrow = "📈" if chg > 0 else ("📉" if chg < 0 else "➡️")
                parts.append(f"ETH: ${eth['usd']:,.0f} (24h {chg:+.2f}%) {arrow}")
        elif crypto.get("error"):
            parts.append(f"【Layer 3: 加密数据】⚠️ {crypto['error']}")

    # —— 政治/经济 → 跨平台赔率对比 ——
    if market_type in ("politics", "finance"):
        cross = search_cross_platform(question)
        if cross["matches"]:
            parts.append("【Layer 3: 跨平台赔率对比 (Kalshi/PredictIt)】")
            for m in cross["matches"]:
                parts.append(
                    f"- [{m['exchange']}] {m['title'][:80]}: "
                    f"${m['price']:.4f} | Vol ${m['volume']:,.0f}"
                )
        elif cross["error"] and "未配置" not in cross["error"]:
            parts.append(f"【Layer 3: 跨平台赔率】⚠️ {cross['error']}")

    return "\n".join(parts) if parts else ""


def gather_context(question: str, tags: str, ev_score: int, yes_price: float, volume: float, ie_score: int = 0) -> dict:
    """多源 RAG 上下文收集（v2.5 — Layer 1 全文 + Layer 2 Twitter + Layer 3 专题增强）。

    管线：
    1. DDG 发现 URL → fetch_article() 抓取全文（Layer 1）
    2. 全文获取失败的 URL → 降级为 snippet
    3. Twitter/X 实时搜索（Layer 2，仅高价值市场）
    4. 专题增强（Layer 3，仅极高价值市场）

    Returns:
        {
            "context_text": str,      # 格式化的搜索上下文
            "market_type": str,
            "search_depth": int,
            "sources": list,
            "full_articles": int,     # Layer 1 全文数量
            "twitter_tweets": int,    # Layer 2 推文数量
            "skipped": bool,
            "skip_reason": str,
        }
    """
    market_type = classify_market(question, tags)
    depth, skip_reason = should_search(ev_score, yes_price, volume, ie_score=ie_score)

    if depth == 0:
        return {
            "context_text": "",
            "market_type": market_type,
            "search_depth": 0,
            "sources": [],
            "full_articles": 0,
            "twitter_tweets": 0,
            "special_enhancements": 0,
            "skipped": True,
            "skip_reason": skip_reason,
        }

    # ——— Layer 1: DDG 发现 URL ———
    queries = build_search_queries(question, market_type)
    max_queries = depth  # depth=1 → 1 条查询, depth=2 → 2 条查询
    ddg_results = _ddg_search_with_fallback(queries[:max_queries], question)

    if not ddg_results:
        return {
            "context_text": "",
            "market_type": market_type,
            "search_depth": 0,
            "sources": [],
            "full_articles": 0,
            "twitter_tweets": 0,
            "special_enhancements": 0,
            "skipped": True,
            "skip_reason": "搜索无结果（DDG + 2次降级均失败）",
        }

    # ——— Layer 2: 抓取全文 ———
    # 前 N 个有 URL 的结果 → 尝试全文抓取
    FULL_FETCH_COUNT = 2  # 最多抓 2 篇全文
    context_parts = []
    sources = []
    full_count = 0

    for title, snippet, url in ddg_results:
        if not url:
            # 无 URL，保留 snippet
            context_parts.append(f"- **[DDG摘要] {title}**: {snippet[:200]}")
            continue

        sources.append(url)

        # 已抓到足够的全文 → 剩下的用 snippet
        if full_count >= FULL_FETCH_COUNT:
            context_parts.append(f"- **[DDG摘要] {title}**: {snippet[:200]}")
            continue

        # 尝试抓全文
        full_text = fetch_article(url, max_chars=2000)
        if full_text and len(full_text) > 100:  # 至少 100 字符才认为有效
            full_count += 1
            context_parts.append(
                f"═══════════════════════════════════\n"
                f"【全文 #{full_count}】{title}\n"
                f"来源: {url}\n"
                f"───────────────────────────────────\n"
                f"{full_text}\n"
            )
        else:
            # 全文抓取失败 → 降级为 snippet
            context_parts.append(f"- **[DDG摘要] {title}**: {snippet[:200]}")

    if not context_parts:
        return {
            "context_text": "",
            "market_type": market_type,
            "search_depth": 0,
            "sources": sources[:5],
            "full_articles": 0,
            "twitter_tweets": 0,
            "special_enhancements": 0,
            "skipped": True,
            "skip_reason": "DDG 有结果但解析后无有效内容",
        }

    # ——— Layer 2: Twitter/X 实时情绪 ———
    should_twitter = (ie_score >= _TWITTER_MIN_IE_SCORE or ev_score >= _TWITTER_MIN_EV_SCORE)
    twitter_count = 0
    if should_twitter:
        tw_result = search_twitter(question, market_type, max_tweets=_TWITTER_MAX_TWEETS)
        if tw_result["tweets"]:
            twitter_count = len(tw_result["tweets"])
            tw_lines = [
                "",
                "═══════════════════════════════════",
                "【Layer 2: Twitter/X 实时情绪】",
                f"搜索词: {tw_result['query']}",
                f"最近 {_TWITTER_SEARCH_DAYS} 天 · {twitter_count} 条推文 · 按时间排序",
                "───────────────────────────────────",
            ]
            for i, t in enumerate(tw_result["tweets"], 1):
                engagement = f"♥{t['likes']} 🔄{t['retweets']}"
                tw_lines.append(f"{i}. @{t['author']} ({engagement}):")
                tw_lines.append(f"   {t['text'][:280]}")
                tw_lines.append("")
            context_parts.append("\n".join(tw_lines))

    # ——— Layer 3: 专题增强 ———
    should_special = (ie_score >= _L3_MIN_IE_SCORE or ev_score >= _L3_MIN_EV_SCORE)
    special_count = 0
    if should_special:
        special_text = get_special_context(question, market_type, tags)
        if special_text:
            special_count = 1
            context_parts.append("\n" + special_text)
        elif (cross := search_cross_platform(question)) and cross.get("error"):
            # 跨平台搜索有结果但无匹配 → 静默跳过
            pass

    context_text = "【多源搜索上下文（Layer 1 全文 + Layer 2 Twitter + Layer 3 专题增强）】\n\n" + "\n".join(context_parts[:14])
    return {
        "context_text": context_text,
        "market_type": market_type,
        "search_depth": depth,
        "sources": sources[:5],
        "full_articles": full_count,
        "twitter_tweets": twitter_count,
        "special_enhancements": special_count,
        "skipped": False,
        "skip_reason": "",
    }


# ============================================================
# 核心分析函数
# ============================================================


def analyze_market(
    question: str,
    yes_price: float,
    no_price: float,
    volume: float,
    end_date: str,
    ev_score: int = 0,
    ev_summary: str = "",
    urgency_label: str = "",
    tags: str = "",
    search_context: dict = None,
) -> dict:
    """调用 DeepSeek 对预测市场做深度分析（含可选的 RAG 搜索上下文）

    Args:
        question: 市场问题
        yes_price: YES 当前价格
        no_price: NO 当前价格
        volume: 24h 成交量
        end_date: 结束时间字符串
        ev_score: EV 量化评分
        ev_summary: EV 信号摘要
        urgency_label: 时间紧迫度标签
        tags: 标签
        search_context: gather_context() 的返回结果（可选）

    Returns:
        dict: {
            "text": str,       # 完整分析文本（用于展示）
            "direction": str,  # "buy_yes" / "buy_no" / "hold"
            "confidence": int, # 1-5
            "summary": str,    # 一句话总结
        }
    """
    client, error = get_deepseek_client()
    if error:
        return {"text": error, "direction": "hold", "confidence": 0, "summary": "API 未配置"}

    price_pct = yes_price * 100

    # 构建 prompt：有搜索上下文就用 RAG prompt，否则用基础 prompt
    ctx = search_context or {}
    context_text = ctx.get("context_text", "")
    market_type = ctx.get("market_type", "")
    sources = ctx.get("sources", [])

    base_info = f"""【市场问题】{question}
【市场类型】{market_type}
【YES价格】${yes_price:.4f}（市场定价概率 ~{price_pct:.0f}%）
【NO价格】${no_price:.4f}
【24h成交量】${volume:,.0f}
【结束时间】{end_date}
【量化EV评分】{ev_score}/12分（{ev_summary}）
【时间紧迫度】{urgency_label}"""

    full_articles = ctx.get("full_articles", 0)
    twitter_tweets = ctx.get("twitter_tweets", 0)
    special_enhancements = ctx.get("special_enhancements", 0)
    max_tok = 600  # 默认 token 预算

    if context_text:
        # 根据信息量调整分析深度
        # 构建数据层描述
        data_layers = []
        if full_articles >= 2:
            data_layers.append(f"{full_articles} 篇完整新闻文章")
        elif full_articles == 1:
            data_layers.append("1 篇完整文章 + 若干摘要")
        else:
            data_layers.append("若干新闻标题摘要")
        if twitter_tweets > 0:
            data_layers.append(f"{twitter_tweets} 条 Twitter/X 实时推文")
        if special_enhancements > 0:
            data_layers.append("Layer 3 专题增强（跨平台赔率对比 / 加密实时行情）")

        data_desc = "、".join(data_layers)

        # 构建分析指引
        guide_lines = [f"你拿到了以下信息：{data_desc}。"]
        analysis_items = []

        if full_articles >= 2:
            analysis_items.extend([
                "交叉验证文章和推文的关键事实是否一致",
                "区分「确定性事实」和「推测性观点」",
                "Twitter 情绪可反映市场短期预期",
                "如果有引用具体数据（民调、赔率、统计），优先使用",
            ])
            if special_enhancements > 0:
                analysis_items.append("对比 Layer 3 跨平台赔率 vs Polymarket 定价：哪边更合理？有无套利空间？")
            word_limit = "分析控制在 400 字以内，重点是证据质量和交叉验证"
            max_tok = 900
        elif full_articles == 1:
            analysis_items.extend([
                "用文章细节和推文观点支撑判断",
            ])
            if special_enhancements > 0:
                analysis_items.append("跨平台赔率数据可作为独立定价参考，与 Polymarket 对比")
            word_limit = "分析控制在 300 字以内"
            max_tok = 800
        elif twitter_tweets > 0:
            analysis_items.append("结合社交平台情绪和新闻摘要综合判断")
            if special_enhancements > 0:
                analysis_items.append("跨平台赔率 / 加密行情为额外信号来源，权衡权重")
            word_limit = "分析控制在 280 字以内"
            max_tok = 750
        else:
            if special_enhancements > 0:
                analysis_items.append("虽然无全文文章，但跨平台赔率/加密行情提供独立定价信号")
                word_limit = "分析控制在 250 字以内"
                max_tok = 700
            else:
                analysis_items.append("保守判断，标注信息不足")
                word_limit = "分析控制在 200 字以内"
                max_tok = 600

        if analysis_items:
            guide_lines.append("请：\n" + "\n".join(f"{i}. {item}" for i, item in enumerate(analysis_items, 1)))

        info_guide = "。".join(guide_lines) + "。"

        # 构建动态分析维度
        analysis_dims = [
            "1. **你的真实概率判断**：结合搜索证据，这件事实际发生的概率大概多少？为什么？",
            "2. **与市场定价的偏差**：市场是高估还是低估了？证据强度如何？",
            "3. **关键证据**：从搜索材料中引述 1-2 条最有力的证据（标注来源：文章/Twitter/Layer3）",
        ]
        dim_idx = 4
        if twitter_tweets > 0:
            analysis_dims.append(f"{dim_idx}. **Twitter 情绪**：整体偏多/偏空/中性，有无关键意见领袖表态")
            dim_idx += 1
        if special_enhancements > 0:
            analysis_dims.append(f"{dim_idx}. **跨平台/专题数据**：Layer 3 数据提供了什么额外信号？与 Polymarket 定价有无偏差？")
            dim_idx += 1
        analysis_dims.append(f"{dim_idx}. **操作建议**：买YES / 买NO / 观望，简要理由")
        dim_idx += 1

        # 信息质量描述
        iq_parts = []
        if full_articles > 0:
            iq_parts.append(f"全文{full_articles}篇")
        if twitter_tweets > 0:
            iq_parts.append(f"Twitter{twitter_tweets}条")
        if special_enhancements > 0:
            iq_parts.append("Layer3专题增强")
        iq_parts.append("摘要若干")
        iq_desc = " + ".join(iq_parts)

        analysis_dims.append(f"{dim_idx}. **信息质量**：足/一般/不足（{iq_desc}）")
        dim_idx += 1
        analysis_dims.append(f"{dim_idx}. **一句话风险提示**")
        dim_idx += 1
        analysis_dims.append(f"{dim_idx}. **自信度**（1-5星，5=非常确定；信息不足时应偏低）")

        analysis_dim_text = "\n".join(analysis_dims)

        prompt = f"""你是预测市场分析专家。请结合以下**实时搜索信息**分析这个 Polymarket 市场：

{base_info}

{context_text}

{info_guide}

请用简洁中文给出（{word_limit}）：
{analysis_dim_text}

最后，在最后一行用严格格式输出结构化结论（必须包含）：
DIRECTION: buy_yes / buy_no / hold
CONFIDENCE: 1-5
SUMMARY: 一句话总结"""
    else:
        prompt = f"""你是预测市场分析专家。分析以下 Polymarket 市场：

{base_info}
【标签】{tags if tags else "无"}

请用简洁中文给出：
1. **你的真实概率判断**：这件事实际发生的概率大概多少？为什么？
2. **与市场定价的偏差**：市场是高估还是低估了？
3. **操作建议**：买YES / 买NO / 观望，简要理由
4. **一句话风险提示**
5. **自信度**（1-5星，5=非常确定）

控制在200字以内，直接给结论。

最后，在最后一行用严格格式输出结构化结论（必须包含）：
DIRECTION: buy_yes / buy_no / hold
CONFIDENCE: 1-5
SUMMARY: 一句话总结"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=max_tok,
        )
        raw = response.choices[0].message.content

        # 解析结构化结论
        direction = "hold"
        confidence = 3
        summary = "AI 未给出明确结论"

        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("DIRECTION:"):
                d = line.replace("DIRECTION:", "").strip().lower()
                if d in ("buy_yes", "buy_no", "hold"):
                    direction = d
            elif line.startswith("CONFIDENCE:"):
                try:
                    c = int(line.replace("CONFIDENCE:", "").strip())
                    if 1 <= c <= 5:
                        confidence = c
                except ValueError:
                    pass
            elif line.startswith("SUMMARY:"):
                summary = line.replace("SUMMARY:", "").strip()

        # 去掉结构化行，展示文本更干净
        text_lines = [l for l in raw.split("\n") if not l.strip().startswith(("DIRECTION:", "CONFIDENCE:", "SUMMARY:"))]
        display_text = "\n".join(text_lines).strip()

        # 附上搜索来源
        if sources:
            src_list = "\n\n---\n📰 参考来源:\n" + "\n".join(s[:80] + "..." for s in sources[:3])
            display_text += src_list

        return {
            "text": display_text,
            "direction": direction,
            "confidence": confidence,
            "summary": summary,
        }
    except Exception as e:
        return {"text": f"❌ AI 分析失败: {str(e)}", "direction": "hold", "confidence": 0, "summary": "分析失败"}


# ============================================================
# 快速扫描（已废弃 — 请用 analyze_market()）
# ============================================================


def quick_scan(question: str, yes_price: float, end_date: str) -> str:
    """[已废弃] 快速扫一眼市场，只给一句话判断。
    
    此函数缺乏 RAG 实时搜索和结构化输出，与 analyze_market() 结果矛盾。
    v2.2 起不再使用，保留仅为向后兼容。
    """
    client, error = get_deepseek_client()
    if error:
        return error

    prompt = f"""快速判断这个预测市场是否值得交易，一句话回答：

市场: {question}
当前YES价格: ${yes_price:.4f} (概率 ~{yes_price*100:.0f}%)
结束: {end_date}

只回答以下格式之一：
- 🟢 值得 [理由,15字内]
- 🟡 谨慎 [理由,15字内]
- 🔴 避开 [理由,15字内]"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=80,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"分析失败: {str(e)}"
