"""
Polymarket AI Scanner - DeepSeek AI 分析模块 v2.1
v2.0: 基础 LLM 分析
v2.1: RAG 增强 — 分层搜索策略 + 市场分类 + 定向新闻检索
"""

import os
import re

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
    text = (question + " " + (tags or "")).lower()
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


def should_search(ev_score: int, yes_price: float, volume: float) -> tuple:
    """判断是否需要搜索，以及搜索深度。

    Returns:
        (search_depth, skip_reason)
        search_depth: 0=跳过, 1=快速, 2=深度
    """
    # 价格极端 → 市场已有结论，搜索无意义
    if yes_price < 0.05 or yes_price > 0.95:
        return 0, "价格极端（<5% 或 >95%），市场已有结论"
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


def search_news(query: str, max_results: int = 5, timeout: int = 8) -> list:
    """通过 DuckDuckGo 搜索新闻，返回 [(title, snippet, url), ...]

    不用 API Key，免费。云端和本地均可用。
    """
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return [("", "⚠️ 需安装 duckduckgo_search 库: pip install duckduckgo_search", "")]

    try:
        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=max_results, timelimit="w"))
    except Exception as e:
        # 可能被限流，降级到 text 搜索
        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results))
            results = [{"title": r["title"], "body": r["body"], "url": r.get("href", "")} for r in raw]
        except Exception:
            return [("", f"⚠️ 搜索暂时不可用: {str(e)[:80]}", "")]

    output = []
    for r in results:
        title = (r.get("title") or "").strip()
        body = (r.get("body") or r.get("snippet") or "").strip()
        url = (r.get("url") or r.get("link") or "").strip()
        if title or body:
            output.append((title, body, url))
    return output[:max_results]


def gather_context(question: str, tags: str, ev_score: int, yes_price: float, volume: float) -> dict:
    """一站式 RAG 上下文收集。

    Returns:
        {
            "context_text": str,      # 格式化的搜索上下文，可直接喂给 LLM
            "market_type": str,
            "search_depth": int,      # 0/1/2
            "sources": list,          # 新闻来源列表
            "skipped": bool,          # 是否跳过了搜索
            "skip_reason": str,
        }
    """
    market_type = classify_market(question, tags)
    depth, skip_reason = should_search(ev_score, yes_price, volume)

    if depth == 0:
        return {
            "context_text": "",
            "market_type": market_type,
            "search_depth": 0,
            "sources": [],
            "skipped": True,
            "skip_reason": skip_reason,
        }

    # 构建搜索词并执行
    queries = build_search_queries(question, market_type)
    max_queries = depth  # depth=1 → 搜 1 条, depth=2 → 搜 2 条

    all_news = []
    sources = []
    for q in queries[:max_queries]:
        results = search_news(q, max_results=3)
        for title, body, url in results:
            if title and body and not title.startswith("⚠️"):
                all_news.append(f"- **{title}**: {body[:300]}")
                if url:
                    sources.append(url)

    if not all_news:
        return {
            "context_text": "",
            "market_type": market_type,
            "search_depth": 0,
            "sources": [],
            "skipped": True,
            "skip_reason": "搜索无结果",
        }

    context_text = "【实时搜索上下文】\n" + "\n".join(all_news[:6])
    return {
        "context_text": context_text,
        "market_type": market_type,
        "search_depth": depth,
        "sources": sources[:5],
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
) -> str:
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
        AI 分析报告（中文）
    """
    client, error = get_deepseek_client()
    if error:
        return error

    price_pct = yes_price * 100

    # 构建 prompt：有搜索上下文就用 RAG prompt，否则用基础 prompt
    ctx = search_context or {}
    context_text = ctx.get("context_text", "")
    market_type = ctx.get("market_type", "")
    sources = ctx.get("sources", [])

    if context_text:
        # RAG 增强 prompt
        prompt = f"""你是预测市场分析专家。请结合以下**实时搜索信息**分析这个 Polymarket 市场：

【市场问题】{question}
【市场类型】{market_type}
【YES价格】${yes_price:.4f}（市场定价概率 ~{price_pct:.0f}%）
【NO价格】${no_price:.4f}
【24h成交量】${volume:,.0f}
【结束时间】{end_date}
【量化EV评分】{ev_score}/12分（{ev_summary}）
【时间紧迫度】{urgency_label}

{context_text}

请用简洁中文给出（200字内）：
1. **你的真实概率判断**：结合搜索结果，这件事实际发生的概率大概多少？为什么？
2. **与市场定价的偏差**：市场是高估还是低估了？
3. **操作建议**：买YES / 买NO / 观望，简要理由
4. **信息可信度**：搜索结果是否有足够信息支撑判断？（足/一般/不足）
5. **一句话风险提示**
6. **自信度**（1-5星，5=非常确定）"""
    else:
        # 无搜索上下文的纯 LLM 分析
        prompt = f"""你是预测市场分析专家。分析以下 Polymarket 市场：

【问题】{question}
【标签】{tags if tags else "无"}
【YES价格】${yes_price:.4f}（市场定价概率 ~{price_pct:.0f}%）
【NO价格】${no_price:.4f}
【24h成交量】${volume:,.0f}
【结束时间】{end_date}
【量化EV评分】{ev_score}/12分（{ev_summary}）
【时间紧迫度】{urgency_label}

请用简洁中文给出：
1. **你的真实概率判断**：这件事实际发生的概率大概多少？为什么？
2. **与市场定价的偏差**：市场是高估还是低估了？
3. **操作建议**：买YES / 买NO / 观望，简要理由
4. **一句话风险提示**
5. **自信度**（1-5星，5=非常确定）

控制在200字以内，直接给结论。"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=600,
        )
        result = response.choices[0].message.content

        # 附上搜索来源
        if sources:
            src_list = "\n\n---\n📰 参考来源:\n" + "\n".join(s[:80] + "..." for s in sources[:3])
            result += src_list

        return result
    except Exception as e:
        return f"❌ AI 分析失败: {str(e)}"


# ============================================================
# 快速扫描（不变）
# ============================================================


def quick_scan(question: str, yes_price: float, end_date: str) -> str:
    """快速扫一眼市场，只给一句话判断。"""
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
