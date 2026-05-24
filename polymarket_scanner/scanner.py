"""
Polymarket AI Scanner - Phase 4: 筛选增强 + EV 检测
一个人 + AI = 小型量化团队
"""

import subprocess
import json
import csv
import os
import tempfile
import urllib.parse
from datetime import datetime, timezone, timedelta


# ============================================================
# 配置
# ============================================================
GAMMA_API = "https://gamma-api.polymarket.com"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
NOW = datetime.now(timezone.utc)


# ============================================================
# HTTP 客户端 — 跨平台
# - Linux/macOS (Streamlit Cloud): requests 直连，无代理问题
# - Windows: 先试 requests，遇到 VPN 代理 SSL 问题则走 PowerShell
# ============================================================
def _http_get(url: str, params: dict = None, timeout: int = 30) -> dict:
    """Cross-platform HTTP GET with Windows VPN proxy fallback."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)

    # Linux / macOS: 直连，requests 无代理兼容问题
    if os.name != "nt":
        import requests
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()

    # Windows: 先试 requests → 失败则走 PowerShell（兼容 VPN 代理）
    try:
        import requests
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        pass  # 代理 SSL 兼容问题 → 走 PowerShell fallback

    tmpfile = os.path.join(tempfile.gettempdir(), f"_pm_scan_{os.getpid()}.json")
    ps_cmd = (
        f"$r = Invoke-WebRequest '{url}' -TimeoutSec {timeout} -UseBasicParsing;"
        f"$utf8 = [System.Text.UTF8Encoding]::new($false);"
        f"[IO.File]::WriteAllText('{tmpfile}', $r.Content, $utf8)"
    )

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True, text=True, timeout=timeout + 10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        err = result.stderr.strip() or "powershell exited with code " + str(result.returncode)
        raise RuntimeError(f"HTTP request failed: {err[:300]}")

    with open(tmpfile, "r", encoding="utf-8") as f:
        data = json.load(f)
    os.unlink(tmpfile)
    return data


# ============================================================
# 工具函数
# ============================================================
def parse_prices(market: dict) -> dict:
    """健壮地解析价格数据，统一返回 {yes, no, bestBid, bestAsk, competitive}

    优先级：bestBid/bestAsk（实盘可交易价） > outcomePrices（中间价）
    """
    result = {"yes": 0.0, "no": 0.0, "spread": 0.0, "bestBid": None, "bestAsk": None, "competitive": None}

    # 实盘订单簿价格（最精确）
    best_bid = market.get("bestBid")
    best_ask = market.get("bestAsk")
    competitive = market.get("competitive")

    if best_bid is not None:
        result["bestBid"] = float(best_bid)
    if best_ask is not None:
        result["bestAsk"] = float(best_ask)
    if competitive is not None:
        result["competitive"] = float(competitive)

    # outcomePrices 作为基准
    prices_str = market.get("outcomePrices", "[]")
    try:
        prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
        if prices and len(prices) >= 2:
            result["yes"] = float(prices[0]) if prices[0] else 0
            result["no"] = float(prices[1]) if prices[1] else 0
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # fallback: 用 bestBid/bestAsk 修正 yes/no
    if result["yes"] == 0 and result["bestBid"] is not None:
        result["yes"] = result["bestBid"]
    if result["no"] == 0 and result["bestAsk"] is not None:
        # bestAsk 是 YES 的卖价，NO = 1 - bestAsk
        result["no"] = round(1.0 - result["bestAsk"], 4)

    result["spread"] = abs(1.0 - (result["yes"] + result["no"]))
    return result


def parse_end_time(market: dict):
    """解析结束时间，返回 datetime 或 None"""
    end_str = market.get("endDate") or market.get("closeTime") or ""
    if not end_str:
        return None
    try:
        # ISO 8601: "2026-05-22T14:00:00Z" 或带时区
        dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        return dt
    except (ValueError, TypeError):
        return None


def time_urgency(end_dt, now=None) -> tuple:
    """
    时间紧迫度分级
    返回 (level, label, emoji)
    支持 datetime / ISO字符串 / None
    """
    if now is None:
        now = NOW
    if end_dt is None:
        return (4, "未知", "⚪")
    if isinstance(end_dt, str):
        parsed = parse_end_time({"endDate": end_dt})
        if parsed is None:
            return (4, "未知", "⚪")
        end_dt = parsed

    remaining = end_dt - now
    hours = remaining.total_seconds() / 3600

    if hours < 0:
        return (0, "已结束", "⚫")
    elif hours < 1:
        return (1, "< 1小时", "🔴")
    elif hours < 6:
        return (2, "< 6小时", "🟠")
    elif hours < 24:
        return (3, "< 24小时", "🟡")
    elif hours < 72:
        return (4, "< 3天", "🟢")
    elif hours < 168:
        return (5, "< 1周", "🔵")
    else:
        return (6, "> 1周", "⚪")


def extract_tags(market: dict) -> list:
    """提取标签列表"""
    tags = market.get("tags", [])
    if not tags:
        return ["未分类"]
    result = []
    for t in tags:
        if isinstance(t, dict):
            label = t.get("label", "")
        else:
            label = str(t)
        if label:
            result.append(label)
    return result or ["未分类"]


def calc_volume_rank(market: dict) -> float:
    """计算综合热度分 (volume * liquidity_factor)"""
    vol = float(market.get("volume24hr") or market.get("volumeNum") or market.get("volume") or 0)
    liq = float(market.get("liquidityNum") or market.get("liquidity") or 0)
    return vol + liq * 0.3  # 流动性权重 0.3


# ============================================================
# 信息优势评分 (Info Edge Score)
# 衡量"这个市场是否有可被 AI 分析利用的信息差"
# 纯本地计算，不依赖外部 API
# ============================================================
def info_edge_score(
    yes: float,
    volume: float,
    hours_remaining: float | None,
    spread: float,
    tags: list,
) -> dict:
    """
    信息优势评分 — 预测市场是否存在可利用的信息不对称

    评分维度：
    1. 价格博弈区间 — uncertainty = information matters
    2. 催化剂临近度 — near event = news coverage peaks
    3. 成交量信号 — high vol = many participants with info
    4. 话题可搜索性 — tags indicate news availability
    5. 价差质量 — tight spread = efficient to trade

    Returns:
        {"score": int, "flags": list, "rating": str}
    """
    score = 0
    flags = []

    # ── 因子1: 价格博弈区间 (0-3) ──
    # 45-55¢ = 最大不确定性 = 信息最有价值
    if 0.45 <= yes <= 0.55:
        score += 3
        flags.append("核心博弈区间(45-55%)")
    elif 0.35 <= yes <= 0.65:
        score += 2
        flags.append("博弈区间(35-65%)")
    elif 0.25 <= yes <= 0.75:
        score += 1
        flags.append("可交易区间")

    # ── 因子2: 催化剂临近度 (0-3) ──
    if hours_remaining is not None:
        if hours_remaining <= 6:
            score += 3
            flags.append("即将揭晓(<6h)")
        elif hours_remaining <= 24:
            score += 2
            flags.append("今日揭晓(<24h)")
        elif hours_remaining <= 72:
            score += 1
            flags.append("近期事件(<3天)")

    # ── 因子3: 成交量信号 (0-2) ──
    if volume > 200000:
        score += 2
        flags.append("高关注(>$200K)")
    elif volume > 50000:
        score += 1
        flags.append("有交易量(>$50K)")

    # ── 因子4: 话题可搜索性 (0-2) ──
    # 这些话题在 DuckDuckGo 上容易搜到相关新闻
    tag_str = " ".join(tags).lower()
    high_info_keywords = [
        "politics", "sports", "crypto", "election", "economy", "fed",
        "trump", "nba", "nfl", "bitcoin", "ethereum", "sec", "rate",
        "war", "trade", "tariff", "gdp", "inflation", "oil", "gold",
        "政治", "经济", "选举", "体育", "加密", "战争", "贸易",
    ]
    match_count = sum(1 for kw in high_info_keywords if kw in tag_str)
    if match_count >= 2:
        score += 2
        flags.append("高信息密度话题")
    elif match_count >= 1:
        score += 1
        flags.append("可搜索话题")

    # ── 因子5: 价差质量 (0-1) ──
    if spread < 0.02:
        score += 1
        flags.append("低摩擦可交易")

    # ── 评级 ──
    if score >= 8:
        rating = "🟢 高信息优势"
    elif score >= 5:
        rating = "🟡 有信息机会"
    elif score >= 3:
        rating = "🟠 信息有限"
    else:
        rating = "⚪ 信息不足"

    return {"score": score, "flags": flags, "rating": rating}


# ============================================================
# EV 检测引擎 (v2 — 多因子评分)
# ============================================================
def ev_signal(market: dict, prices: dict) -> dict:
    """
    多因子 EV 信号评分系统

    不依赖外部「真实概率」模型，基于可观测市场特征综合打分：
    因子1: 价格博弈区间 — 不确定性 = 交易机会
    因子2: 成交量热度 — 市场关注度
    因子3: 流动性深度 — 能否进出
    因子4: 价差效率 — 定价质量
    因子5: 时间衰减 — 到期紧迫度
    """
    yes = prices["yes"]
    no = prices["no"]
    spread_val = prices.get("spread", abs(1.0 - yes - no))

    # 成交量: 优先 volume24hr → volumeNum → volume → 0
    vol = float(
        market.get("volume24hr") or market.get("volumeNum") or market.get("volume") or 0
    )
    liq = float(market.get("liquidityNum") or market.get("liquidity") or 0)
    competitive_val = prices.get("competitive")

    score = 0
    flags = []

    # ── 因子1: 价格博弈区间 (0~3) ──
    if 0.35 <= yes <= 0.65:
        score += 3
        flags.append("高不确定性(35-65%)")
    elif 0.25 <= yes <= 0.75:
        score += 2
        flags.append("博弈区间(25-75%)")
    elif 0.15 <= yes <= 0.85:
        score += 1
        flags.append("可交易区间")

    # ── 因子2: 成交量热度 (0~3, 基于实际分位数) ──
    if vol > 1000000:
        score += 3
        flags.append("超高成交量(>$1M)")
    elif vol > 300000:
        score += 2
        flags.append("高成交量")
    elif vol > 100000:
        score += 1
        flags.append("中等成交量")
    elif vol < 5000:
        score -= 1
        flags.append("流动性不足")

    # ── 因子3: 流动性深度 (0~1) ──
    if liq > 10000:
        score += 1
        flags.append("深度流动性")

    # ── 因子4: 价差效率 (-1~2) ──
    if spread_val < 0.01:
        score += 2
        flags.append("极低摩擦")
    elif spread_val < 0.03:
        score += 1
        flags.append("低摩擦定价")
    elif spread_val > 0.10:
        score -= 1
        flags.append("价差偏大")

    # ── 因子5: 竞争度 (0~3, 核心差异化因子) ──
    if competitive_val is not None:
        if competitive_val > 0.90:
            score += 3
            flags.append("高度竞争(薄价差)")
        elif competitive_val > 0.80:
            score += 2
            flags.append("中等竞争")
        elif competitive_val > 0.60:
            score += 1
            flags.append("轻度竞争")

    # ── 因子6: 极价动量 (0~2) ──
    # 极价+高量 = 强共识（套利/对冲机会）
    if yes > 0.85 and vol > 50000:
        score += 2
        flags.append("极价共识(强方向)")
    elif yes < 0.15 and vol > 50000:
        score += 2
        flags.append("极低共识(强方向)")

    # 极价+低量 = 可能被市场忽视的机会
    if 0.80 < yes < 0.96 and vol < 5000:
        score += 1
        flags.append("极价低量(被忽视?)")
    if 0.04 < yes < 0.20 and vol < 5000:
        score += 1
        flags.append("极低低量(黑马?)")

    # ── 综合评级 ──
    if score >= 10:
        signal_summary = "🔥 强烈推荐"
    elif score >= 7:
        signal_summary = "⭐ 优质信号"
    elif score >= 5:
        signal_summary = "💡 值得关注"
    elif score >= 3:
        signal_summary = "👀 可观察"
    else:
        signal_summary = "—"

    return {
        "score": max(score, 0),
        "flags": flags,
        "summary": signal_summary,
    }


# ============================================================
# API 层
# ============================================================
def fetch_markets(limit: int = 200, tag: str = None, offset: int = 0) -> list:
    """从 Polymarket Gamma API 拉取市场数据（按24h交易量排序）"""
    url = f"{GAMMA_API}/markets"
    params = {
        "limit": str(min(limit, 500)),
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
        "offset": str(offset),
    }
    if tag:
        params["tag"] = tag

    return _http_get(url, params=params)


def fetch_events(limit: int = 50) -> list:
    """获取事件列表"""
    url = f"{GAMMA_API}/events"
    params = {"limit": str(limit), "closed": "false"}
    return _http_get(url, params=params)


# ============================================================
# 筛选 & 分析
# ============================================================
def enrich_market(market: dict) -> dict:
    """给单个市场附加所有计算字段"""
    prices = parse_prices(market)
    end_dt = parse_end_time(market)
    urgency_level, urgency_label, urgency_emoji = time_urgency(end_dt)
    tags = extract_tags(market)
    signal = ev_signal(market, prices)
    heat = calc_volume_rank(market)

    return {
        "id": market.get("id", ""),
        "question": market.get("question", "N/A"),
        "slug": market.get("slug", ""),
        "yes": prices["yes"],
        "no": prices["no"],
        "spread": prices["spread"],
        "volume": float(market.get("volume24hr") or market.get("volumeNum") or market.get("volume") or 0),
        "liquidity": float(market.get("liquidityNum") or market.get("liquidity") or 0),
        "end_date": end_dt.isoformat() if end_dt else None,
        "end_date_display": end_dt.strftime("%m-%d %H:%M") if end_dt else "未知",
        "urgency_level": urgency_level,
        "urgency_label": urgency_label,
        "urgency_emoji": urgency_emoji,
        "tags": tags,
        "heat_score": heat,
        "ev_signal": signal,
    }


def scan_all(markets_raw: list) -> list:
    """全量扫描 + 增强"""
    enriched = [enrich_market(m) for m in markets_raw]
    return enriched


# ============================================================
# 报告输出
# ============================================================
def report_urgency_grid(enriched: list):
    """紧迫度网格：按结束时间排列"""
    print(f"\n{'='*70}")
    print(f"  ⏰ 时间紧迫度排行 (TOP 15 即将到期)")
    print(f"{'='*70}")

    # 只显示未结束的
    active = [m for m in enriched if m["urgency_level"] >= 1]
    active.sort(key=lambda x: (x["urgency_level"], -x["heat_score"]))

    print(f"  {'市场':<45} {'YES':>6} {'Volume':>8} {'结束':>12} {'紧迫':>8}")
    print(f"  {'-'*45} {'-'*6} {'-'*8} {'-'*12} {'-'*8}")

    for m in active[:15]:
        urgency_tag = f"{m['urgency_emoji']} {m['urgency_label']}"
        print(f"  {m['question'][:43]:<45} {m['yes']:>6.4f} ${m['volume']:>7,.0f} {m['end_date_display']:>12} {urgency_tag:>8}")

    print(f"\n  共 {len(active)} 个活跃市场")


def report_ev_signals(enriched: list):
    """EV 信号报告"""
    print(f"\n{'='*70}")
    print(f"  🎯 EV 信号检测")
    print(f"{'='*70}")

    signals = [m for m in enriched if m["ev_signal"]["score"] >= 3]
    signals.sort(key=lambda x: -x["ev_signal"]["score"])

    if not signals:
        print("  当前无显著信号")
        return

    print(f"  {'市场':<45} {'YES':>6} {'Vol':>7} {'信号':>12} {'详情'}")
    print(f"  {'-'*45} {'-'*6} {'-'*7} {'-'*12} {'-'*20}")

    for m in signals[:15]:
        detail = ", ".join(m["ev_signal"]["flags"][:3])
        print(f"  {m['question'][:43]:<45} {m['yes']:>6.4f} ${m['volume']:>6,.0f} {m['ev_signal']['summary']:>12} {detail}")


def report_categories(enriched: list):
    """分类统计"""
    print(f"\n{'='*70}")
    print(f"  📂 市场分类分布")
    print(f"{'='*70}")

    cat_count = {}
    cat_vol = {}
    for m in enriched:
        for tag in m["tags"]:
            cat_count[tag] = cat_count.get(tag, 0) + 1
            cat_vol[tag] = cat_vol.get(tag, 0) + m["volume"]

    print(f"  {'分类':<25} {'数量':>5} {'总成交量':>15}")
    print(f"  {'-'*25} {'-'*5} {'-'*15}")
    for tag, count in sorted(cat_count.items(), key=lambda x: -x[1])[:15]:
        print(f"  {tag:<25} {count:>5} ${cat_vol.get(tag, 0):>14,.0f}")


def report_summary(enriched: list):
    """总览"""
    total = len(enriched)
    active = [m for m in enriched if m["urgency_level"] >= 1]
    high_signal = [m for m in enriched if m["ev_signal"]["score"] >= 3]
    closing_soon = [m for m in enriched if m["urgency_level"] in (1, 2)]
    high_vol = [m for m in enriched if m["volume"] > 50000]
    total_vol = sum(m["volume"] for m in enriched)

    print(f"\n{'='*70}")
    print(f"  📊 Polymarket AI Scanner — 综合报告")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*70}")
    print(f"  总市场数:     {total}")
    print(f"  活跃市场:     {len(active)}")
    print(f"  即将到期:     {len(closing_soon)} (1h-6h)")
    print(f"  高成交量(>$50k): {len(high_vol)}")
    print(f"  EV 信号:      {len(high_signal)} 个值得关注")
    print(f"  总成交量:     ${total_vol:,.0f}")


def export_json(enriched: list, filename: str = "scan_result.json"):
    """导出 JSON"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, filename)
    # 简化字段，只保留关键信息
    export = []
    for m in enriched:
        export.append({
            "question": m["question"],
            "yes": m["yes"],
            "no": m["no"],
            "volume": m["volume"],
            "liquidity": m["liquidity"],
            "end_date": m["end_date"],
            "urgency": m["urgency_label"],
            "tags": m["tags"],
            "ev_score": m["ev_signal"]["score"],
            "ev_flags": m["ev_signal"]["flags"],
            "ev_summary": m["ev_signal"]["summary"],
        })

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)
    print(f"\n📁 结果已导出: {filepath}")
    return filepath


def export_csv(enriched: list, filename: str = "scan_result.csv"):
    """导出 CSV"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, filename)

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["问题", "YES", "NO", "成交量", "流动性", "结束时间", "紧迫度", "分类", "EV分数", "EV信号"])
        for m in enriched:
            writer.writerow([
                m["question"],
                f"{m['yes']:.4f}",
                f"{m['no']:.4f}",
                m["volume"],
                m["liquidity"],
                m["end_date_display"],
                m["urgency_label"],
                ", ".join(m["tags"]),
                m["ev_signal"]["score"],
                ", ".join(m["ev_signal"]["flags"]),
            ])
    print(f"📁 CSV 已导出: {filepath}")
    return filepath


# ============================================================
# 主入口
# ============================================================
def main():
    print("\n🚀 Polymarket AI Scanner v2.0 — Phase 4 启动")
    print("   筛选增强 + EV 检测 + 时间紧迫度\n")

    try:
        # 1. 拉取数据（尽量多拿）
        print("📡 正在连接 Polymarket Gamma API...")
        all_markets = []
        for offset in [0, 100]:
            batch = fetch_markets(limit=100, offset=offset)
            all_markets.extend(batch)
            print(f"   批次 offset={offset}: {len(batch)} 条")
            if len(batch) < 100:
                break

        print(f"   总计拉取: {len(all_markets)} 条市场")

        # 2. 全量扫描
        print("🔍 正在分析...")
        enriched = scan_all(all_markets)

        # 3. 综合报告
        report_summary(enriched)

        # 4. 时间紧迫度
        report_urgency_grid(enriched)

        # 5. EV 信号
        report_ev_signals(enriched)

        # 6. 分类统计
        report_categories(enriched)

        # 7. 导出
        export_json(enriched)
        export_csv(enriched)

        print(f"\n✅ 全量扫描完成！")

    except requests.exceptions.RequestException as e:
        print(f"\n❌ 网络错误: {e}")
    except Exception as e:
        print(f"\n❌ 运行错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
