"""
Polymarket AI Scanner — Streamlit GUI v2.0
双模式架构: 🟢 新手傻瓜式 / 🔵 专业模式
Phase 12: DeepSeek AI 智能分析
"""

import streamlit as st
import pandas as pd
import json
import os
import sys
import time
import subprocess
from pathlib import Path
import urllib.parse
from datetime import datetime, timezone

BASE_DIR     = Path(__file__).parent.resolve()
CONFIG_FILE  = BASE_DIR / "auto_config.json"
HISTORY_FILE = BASE_DIR / "trade_history.json"
STATUS_FILE  = BASE_DIR / "daemon_status.json"

from scanner import (
    fetch_markets, parse_prices, parse_end_time,
    time_urgency, ev_signal, extract_tags, calc_volume_rank,
    info_edge_score,
)
from ai_analyzer import analyze_market, gather_context


# ============================================================
# 页面配置
# ============================================================
st.set_page_config(
    page_title="Polymarket AI Scanner",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": "Polymarket AI Scanner v2.0 | 一个人 + AI = 小型量化团队",
    },
)

st.markdown("""
<style>
#MainMenu {visibility: hidden;}
header {visibility: hidden;}
.stDeployButton {display:none !important;}
footer {visibility: hidden;}

/* ===== 禁止 spinner 全屏灰白遮罩 ===== */
.stSpinner > div {
    border-color: #1f77b4 !important;
}
/* 用 status 替代 spinner 时不灰屏 */
section[data-testid="stStatusBlock"] {
    animation: none !important;
}

/* ===== 防止文字跨格/溢出 ===== */
/* 代码块内强制换行 */
.stCodeBlock code, .stCodeBlock pre, [data-testid="stCodeBlock"] code {
    word-break: break-all !important;
    white-space: pre-wrap !important;
    overflow-wrap: break-word !important;
}
/* 链接文字溢出截断 */
.stMarkdown a {
    word-break: break-all;
    overflow-wrap: break-word;
}
/* 所有文本列防溢出 */
div[data-testid="column"] * {
    overflow-wrap: break-word;
}

/* ===== 紧凑按钮 ===== */
.compact-btn button {
    font-size: 0.8rem !important;
    padding: 0.25rem 0.5rem !important;
    min-height: 2rem !important;
    white-space: nowrap !important;
}
.compact-btn button p {
    font-size: 0.8rem !important;
}

/* ===== 紧凑卡片 ===== */
.compact-card {
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 8px;
    background: #fafafa;
}

/* ===== 已分析标记：不换行 ===== */
.analyzed-badge {
    white-space: nowrap;
    font-size: 0.8rem;
    color: #0a8f3c;
    font-weight: 600;
}
</style>
""", unsafe_allow_html=True)


# ============================================================
# Session State 初始化
# ============================================================
if "mode" not in st.session_state:
    st.session_state.mode = "beginner"
if "ai_result" not in st.session_state:
    st.session_state.ai_result = None
if "ai_loading" not in st.session_state:
    st.session_state.ai_loading = False


# ============================================================
# 数据加载（缓存 60 秒）
# ============================================================
@st.cache_data(ttl=60)
def load_markets(limit: int = 300):
    all_markets = []
    for offset in [0, 100, 200]:
        batch = fetch_markets(limit=100, offset=offset)
        all_markets.extend(batch)
        if len(batch) < 100:
            break

    enriched = []
    now = datetime.now(timezone.utc)
    for m in all_markets:
        prices = parse_prices(m)
        end_dt = parse_end_time(m)
        urgency_level, urgency_label, urgency_emoji = time_urgency(end_dt, now=now)
        signal = ev_signal(m, prices)

        question  = m.get("question", "未知")
        volume   = float(m.get("volume", 0) or 0)
        liquidity = float(m.get("liquidity", 0) or 0)

        slug = (m.get("slug") or m.get("marketSlug") or "").strip()
        # 优先用 /event/{slug} 直接链接；slug 无效时降级为搜索链接
        if slug and not any(c in slug for c in [" ", "?", "/"]):
            direct_url = f"https://polymarket.com/event/{slug}"
        else:
            direct_url = ""
        search_url = f"https://polymarket.com/search?_q={urllib.parse.quote(question)}"

        tags_list = extract_tags(m)
        hours_rem = (end_dt - now).total_seconds() / 3600 if end_dt else None
        ie_signal = info_edge_score(
            yes=prices["yes"],
            volume=volume,
            hours_remaining=hours_rem,
            spread=prices["spread"],
            tags=tags_list,
        )

        enriched.append({
            "id":              m.get("id", ""),
            "question":        question,
            "slug":            slug,
            "url":             direct_url or search_url,
            "search_url":      search_url,
            "direct_url":      direct_url,
            "yes":             prices["yes"],
            "no":              prices["no"],
            "spread":          prices["spread"],
            "volume":          volume,
            "liquidity":       liquidity,
            "end_dt":          end_dt,
            "end_date":        end_dt.strftime("%m-%d %H:%M") if end_dt else "未知",
            "urgency_level":   urgency_level,
            "urgency_label":   urgency_label,
            "urgency_emoji":   urgency_emoji,
            "tags":            tags_list,
            "heat_score":      calc_volume_rank(m),
            "ev_score":        signal["score"],
            "ev_flags":        ", ".join(signal["flags"]),
            "ev_summary":      signal["summary"],
            "hours_remaining": hours_rem,
            "ie_score":        ie_signal["score"],
            "ie_flags":        ", ".join(ie_signal["flags"]),
            "ie_rating":       ie_signal["rating"],
        })

    return pd.DataFrame(enriched)


df = load_markets()

# ============================================================
# 环境检测
# ============================================================
IS_CLOUD = os.name != "nt"


# ============================================================
# 辅助函数
# ============================================================
def _read_json(path, default=None):
    try:
        if Path(path).exists():
            return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _load_config():
    return _read_json(CONFIG_FILE, {})

def _load_status():
    return _read_json(STATUS_FILE, {})

def _load_history():
    return _read_json(HISTORY_FILE, [])

def _save_config(cfg):
    Path(CONFIG_FILE).write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ============================================================
# 侧边栏 — 模式切换 + 筛选
# ============================================================
st.sidebar.markdown("## 🎛️ 模式")

# 模式切换
mode_labels = {"beginner": "🟢 新手模式", "pro": "🔵 专业模式"}
current_mode_key = st.session_state.mode
mode_choice = st.sidebar.radio(
    "选择模式",
    options=["beginner", "pro"],
    format_func=lambda x: mode_labels[x],
    index=0 if current_mode_key == "beginner" else 1,
    key="mode_radio",
    horizontal=True,
)

if mode_choice != st.session_state.mode:
    st.session_state.mode = mode_choice
    st.rerun()

mode = st.session_state.mode

st.sidebar.markdown("---")

# ───────── 新手模式侧边栏 ─────────
if mode == "beginner":
    with st.sidebar.expander("💡 概念速查（必读）", expanded=True):
        st.markdown("""
**YES 价格** = 市场认为事件"会发生"的概率
> $0.45 → 45% | $0.10 → 10%（不划算！）

**NO 价格** = 市场认为"不会发生"
> YES + NO ≈ $1.00

**EV 评分** (0-12分):  
> 🔥10+强信号 | ⭐7-9优质 | 💡5-6可看

**博弈区间 = YES $0.35-$0.65**
> 这才是该交易的价格区间！

⚠️ **远离 $0.01-$0.15 的彩票市场！**
""")

    st.sidebar.info(
        "🔰 **新手模式**：直接看推荐，复制链接去交易。\n\n"
        "切换到「🔵 专业模式」可使用完整筛选器。"
    )

    # 新手筛选：硬编码博弈区间
    price_min, price_max = 0.35, 0.65
    vol_min = 5000
    ev_min = 3
    urgency_selected = [1, 2, 3, 4, 5, 6]
    search = st.sidebar.text_input("🔎 搜索", placeholder="输入关键词...")

# ───────── 专业模式侧边栏 ─────────
else:
    with st.sidebar.expander("💡 概念速查", expanded=False):
        st.markdown("""
        **YES 价格** = 事件发生概率  
        **NO 价格** = 事件不发生概率  
        **EV 评分** (0-12): 多因子得分  
        **博弈区间**: YES $0.35-$0.65  
        **彩票市场**: YES < $0.20（远离）
        """)

    # 快速预设
    st.sidebar.markdown("### ⚡ 快速筛选")
    preset_cols = st.sidebar.columns(3)
    with preset_cols[0]:
        if st.button("🧠 博弈", use_container_width=True, help="YES $0.35-$0.65"):
            st.session_state["price_min"] = 0.35
            st.session_state["price_max"] = 0.65
            st.session_state["ev_min"] = 3
            st.session_state["vol_min"] = 5000
    with preset_cols[1]:
        if st.button("⏰ 到期", use_container_width=True, help="24h内结算"):
            st.session_state["urgency_filter"] = [1, 2, 3]
            st.session_state["ev_min"] = 3
    with preset_cols[2]:
        if st.button("🔥 高EV", use_container_width=True, help="EV≥7强信号"):
            st.session_state["ev_min"] = 7
            st.session_state["price_min"] = 0.0
            st.session_state["price_max"] = 1.0

    st.sidebar.markdown("---")

    search = st.sidebar.text_input("🔎 搜索市场", placeholder="输入关键词...", key="pro_search")

    # 紧迫度
    urgency_options = {
        1: "🔴 < 1h", 2: "🟠 < 6h", 3: "🟡 < 24h",
        4: "🟢 < 3d", 5: "🔵 < 1w", 6: "⚪ > 1w",
    }
    urgency_selected = st.sidebar.multiselect(
        "⏰ 时间紧迫度",
        options=list(urgency_options.keys()),
        format_func=lambda x: urgency_options[x],
        default=st.session_state.get("urgency_filter", [1, 2, 3, 4, 5, 6]),
        key="pro_urgency",
    )

    price_min = st.sidebar.slider(
        "💰 YES 最低价格", 0.0, 1.0,
        st.session_state.get("price_min", 0.35), 0.05,
    )
    price_max = st.sidebar.slider(
        "💰 YES 最高价格", 0.0, 1.0,
        st.session_state.get("price_max", 0.65), 0.05,
    )
    vol_min = st.sidebar.number_input(
        "📊 最低成交量 ($)", min_value=0,
        value=st.session_state.get("vol_min", 5000), step=1000,
    )
    ev_min = st.sidebar.slider(
        "🎯 最低 EV 分数", 0, 10,
        st.session_state.get("ev_min", 3),
    )


# ============================================================
# 筛选数据
# ============================================================
filtered = df.copy()

if search:
    filtered = filtered[filtered["question"].str.contains(search, case=False, na=False)]

if urgency_selected:
    filtered = filtered[filtered["urgency_level"].isin(urgency_selected)]

filtered = filtered[
    (filtered["yes"] >= price_min) &
    (filtered["yes"] <= price_max) &
    (filtered["volume"] >= vol_min) &
    (filtered["ev_score"] >= ev_min)
]


# ============================================================
# 页面标题
# ============================================================
st.title("🎯 Polymarket AI Scanner")
if mode == "beginner":
    st.caption("新手模式 — 直接看推荐，复制链接去交易 | 一个人+AI=小型量化团队")
else:
    st.caption("专业模式 — 完整数据 & 筛选器 | 一个人+AI=小型量化团队")


# ============================================================
# 指标卡片
# ============================================================
col1, col2, col3, col4, col5 = st.columns(5)
with col1:
    st.metric("总市场", len(df))
with col2:
    st.metric("筛选后", len(filtered))
with col3:
    st.metric("EV≥3", len(df[df["ev_score"] >= 3]))
with col4:
    st.metric("即将到期", len(df[df["urgency_level"].isin([1, 2])]))
with col5:
    total_vol = df["volume"].sum()
    st.metric("总成交量", f"${total_vol/1e6:.1f}M")


# ============================================================
# ╔═══════════════════════════════════════════════════════════╗
# ║              🟢 新手模式 主内容区                           ║
# ╚═══════════════════════════════════════════════════════════╝
# ============================================================
if mode == "beginner":

    # ── 今日推荐 Hero Section ──
    st.markdown("---")
    st.markdown("## 🎯 今日推荐交易")

    # --- AI 分析状态管理（全模式共享） ---
    if "ai_pending" not in st.session_state:
        st.session_state.ai_pending = None
    if "ai_results" not in st.session_state:
        st.session_state.ai_results = {}
    if "batch_ai_pending" not in st.session_state:
        st.session_state.batch_ai_pending = False
    if "show_only_actionable" not in st.session_state:
        st.session_state.show_only_actionable = False
    if "batch_scan_results" not in st.session_state:
        st.session_state.batch_scan_results = {}
    if "batch_scan_pending" not in st.session_state:
        st.session_state.batch_scan_pending = False
    if "auto_batch_enabled" not in st.session_state:
        st.session_state.auto_batch_enabled = False

    # 批量 AI 分析：逐个分析推荐市场
    if st.session_state.batch_ai_pending:
        recommend_batch = df[
            (df["ie_score"] >= 5) & (df["volume"] > 5000)
        ].sort_values("ie_score", ascending=False).head(8)

        batch_ids = recommend_batch["id"].tolist()
        total_batch = len(batch_ids)
        st.info(f"🚀 批量 AI 分析中...共 {total_batch} 个市场")

        progress_bar = st.progress(0, text="准备...")
        for i, rid in enumerate(batch_ids):
            if rid in st.session_state.ai_results:
                continue  # 跳过已分析的
            target = df[df["id"] == rid]
            if target.empty:
                continue
            row = target.iloc[0]
            progress_bar.progress(
                (i + 1) / total_batch,
                text=f"🤖 ({i+1}/{total_batch}) 分析: {row['question'][:40]}...",
            )
            search_ctx = gather_context(
                question=row["question"], tags=row["tags"],
                ev_score=int(row["ev_score"]), yes_price=row["yes"], volume=row["volume"], ie_score=int(row["ie_score"]),
            )
            result = analyze_market(
                question=row["question"], yes_price=row["yes"], no_price=row["no"],
                volume=row["volume"], end_date=str(row["end_date"]),
                ev_score=int(row["ev_score"]), ev_summary=row["ev_summary"],
                urgency_label=row["urgency_label"], tags=row["tags"],
                search_context=search_ctx,
            )
            st.session_state.ai_results[rid] = {
                "text": result.get("text", ""),
                "direction": result.get("direction", "hold"),
                "confidence": result.get("confidence", 0),
                "summary": result.get("summary", ""),
                "search_info": {
                    "market_type": search_ctx["market_type"],
                    "search_depth": search_ctx["search_depth"],
                    "skipped": search_ctx["skipped"],
                    "skip_reason": search_ctx.get("skip_reason", ""),
                },
                "question": row["question"], "yes": row["yes"], "no": row["no"],
                "volume": row["volume"], "ev_score": int(row["ev_score"]),
                "end_date": row["end_date"], "url": row["url"],
            }
            # 节流：避免 API 限流
            import time as _time
            _time.sleep(1.5)

        progress_bar.progress(1.0, text="✅ 分析完成!")
        st.session_state.batch_ai_pending = False
        st.session_state.show_only_actionable = True  # 默认只显示有方向的
        st.rerun()

    # --- 单个 AI 分析处理（新手模式也需要！） ---
    if st.session_state.ai_pending:
        pid = st.session_state.ai_pending
        target = df[df["id"] == pid]
        if not target.empty:
            row = target.iloc[0]
            with st.status(f"🤖 AI 分析: {row['question'][:40]}...", expanded=True) as status:
                status.update(label="🔎 搜索最新信息...")
                search_ctx = gather_context(
                    question=row["question"], tags=row["tags"],
                    ev_score=int(row["ev_score"]), yes_price=row["yes"], volume=row["volume"], ie_score=int(row["ie_score"]),
                )
                status.update(label="🧠 DeepSeek 推理...")
                result = analyze_market(
                    question=row["question"], yes_price=row["yes"], no_price=row["no"],
                    volume=row["volume"], end_date=str(row["end_date"]),
                    ev_score=int(row["ev_score"]), ev_summary=row["ev_summary"],
                    urgency_label=row["urgency_label"], tags=row["tags"],
                    search_context=search_ctx,
                )
                st.session_state.ai_results[pid] = {
                    "text": result.get("text", ""),
                    "direction": result.get("direction", "hold"),
                    "confidence": result.get("confidence", 0),
                    "summary": result.get("summary", ""),
                    "search_info": {
                        "market_type": search_ctx["market_type"],
                        "search_depth": search_ctx["search_depth"],
                        "skipped": search_ctx["skipped"],
                        "skip_reason": search_ctx.get("skip_reason", ""),
                    },
                    "question": row["question"], "yes": row["yes"], "no": row["no"],
                    "volume": row["volume"], "ev_score": int(row["ev_score"]),
                    "end_date": row["end_date"], "url": row["url"],
                }
                status.update(label="✅ 分析完成", state="complete")
        st.session_state.ai_pending = None

    # --- 推荐筛选：信息优势驱动 ---
    recommend = df[
        (df["ie_score"] >= 5) & (df["volume"] > 5000)
    ].sort_values("ie_score", ascending=False).head(8)

    # 如果有批量分析结果且开启了"只看可操作"，只保留 AI 给了方向的
    actionable_count = 0
    if st.session_state.show_only_actionable and st.session_state.ai_results:
        actionable_ids = []
        for _, row in recommend.iterrows():
            rid = row["id"]
            if rid in st.session_state.ai_results:
                d = st.session_state.ai_results[rid].get("direction", "hold")
                if d in ("buy_yes", "buy_no"):
                    actionable_ids.append(rid)
        if actionable_ids:
            recommend = recommend[recommend["id"].isin(actionable_ids)]
            actionable_count = len(actionable_ids)

    # ── 控制栏 ──
    ctl1, ctl2, ctl3 = st.columns([2, 1, 1])
    with ctl1:
        if st.button("🚀 批量 AI 分析推荐", type="primary", use_container_width=True,
                     help="对推荐市场逐个运行 AI 深度分析（含联网搜索），找到真正有信息差的机会"):
            st.session_state.batch_ai_pending = True
            st.rerun()
    with ctl2:
        analyzed_count = len([
            rid for _, row in recommend.iterrows()
            if row["id"] in st.session_state.ai_results
        ])
        st.caption(f"已分析: {analyzed_count}/{len(recommend)}")
    with ctl3:
        if st.session_state.ai_results:
            show_only = st.toggle(
                "只看可操作", value=st.session_state.show_only_actionable,
                help="开启后只显示 AI 明确给出买 YES/买 NO 的市场"
            )
            st.session_state.show_only_actionable = show_only

    if not recommend.empty:
        for pos, (idx, row) in enumerate(recommend.iterrows()):
            rid = row["id"]
            ie = int(row["ie_score"])
            ev = int(row["ev_score"])
            yes = row["yes"]
            vol = row["volume"]
            q = row["question"]
            url = row["url"]

            # 信息优势评级
            if ie >= 8:
                ie_star = "🟢"
                ie_label = "高信息优势"
            elif ie >= 6:
                ie_star = "🟡"
                ie_label = "有信息机会"
            else:
                ie_star = "🟠"
                ie_label = "可关注"

            # 已有 AI 分析结果的话，按方向决定是否灰掉
            ai_direction = None
            ai_confidence = 0
            if rid in st.session_state.ai_results:
                ai_res = st.session_state.ai_results[rid]
                ai_direction = ai_res.get("direction")
                ai_confidence = ai_res.get("confidence", 0)

            # hold 且"只看可操作"开启时跳过（前面已经过滤，双重保险）
            if st.session_state.show_only_actionable and ai_direction == "hold":
                continue

            with st.container():
                c1, c2, c3, c4, c5, c6, c7 = st.columns([2.2, 0.9, 0.8, 0.9, 1.3, 1.5, 1.3])
                with c1:
                    st.markdown(f"### {ie_star} #{pos+1}  {q[:65]}")
                    st.caption(
                        f"{row['urgency_emoji']} {row['end_date']} | "
                        f"信息优势: {ie}分 | {row['ie_rating']}"
                    )
                with c2:
                    st.metric("YES", f"${yes:.3f}")
                    st.caption(f"NO ${row['no']:.3f}")
                with c3:
                    st.metric("信息差", f"{ie}分", delta=ie_label)
                with c4:
                    st.metric("成交量", f"${vol/1000:.0f}K")
                with c5:
                    # 方向判断 — AI 结果优先
                    if ai_direction == "buy_yes":
                        st.success(f"🤖 买YES {ai_confidence}★")
                    elif ai_direction == "buy_no":
                        st.error(f"🤖 买NO {ai_confidence}★")
                    elif ai_direction == "hold":
                        st.warning(f"🤖 观望 {ai_confidence}★")
                    elif yes <= 0.45:
                        st.success("👉 偏买YES")
                    elif yes >= 0.55:
                        st.error("👉 偏买NO")
                    else:
                        st.info("⚖️ 均衡")
                with c6:
                    direct = row["direct_url"]
                    search = row["search_url"][:55] + "…" if len(row["search_url"]) > 58 else row["search_url"]
                    if direct:
                        st.markdown(f"[🔗 直达]({direct})")
                    st.code(search, language=None)
                with c7:
                    already = rid in st.session_state.ai_results
                    if already:
                        st.markdown('<span class="analyzed-badge">✅ 已分析</span>', unsafe_allow_html=True)
                    if st.button(
                        "🤖分析" if not already else "🔄重分析",
                        key=f"card_ai_btn_{rid}",
                        use_container_width=True,
                        help="AI 深度分析：为什么推荐？怎样赚钱？",
                    ):
                        st.session_state.ai_pending = rid
                        st.rerun()

                # 展示 AI 分析结果
                if rid in st.session_state.ai_results:
                    ai_data = st.session_state.ai_results[rid]
                    with st.expander("📊 AI 分析结果", expanded=(ai_direction != "hold")):
                        search_info = ai_data.get("search_info", {})
                        depth = search_info.get("search_depth", 0)
                        mtype = search_info.get("market_type", "")
                        if search_info.get("skipped"):
                            st.caption(f"🔍 搜索: 跳过（{search_info.get('skip_reason', '')}）| {mtype} | 纯 LLM 分析")
                        else:
                            st.caption(f"🔍 搜索: {'⭐' * depth} | {mtype} | RAG 增强分析")
                        # 方向标签
                        if ai_direction == "buy_yes":
                            st.success(f"🤖 AI 方向: 买 YES | 自信度 {ai_confidence}星")
                        elif ai_direction == "buy_no":
                            st.error(f"🤖 AI 方向: 买 NO | 自信度 {ai_confidence}星")
                        else:
                            st.warning(f"🤖 AI 方向: 观望 | 自信度 {ai_confidence}星")
                        st.info(ai_data.get("text", ""))
                        st.caption(
                            f"YES ${ai_data['yes']:.4f} | NO ${ai_data['no']:.4f} | "
                            f"成交量 ${ai_data['volume']:,.0f} | 信息差 {ie}分 | EV {ai_data['ev_score']}分 | "
                            f"{ai_data['end_date']}"
                        )
                        st.code(ai_data["url"], language=None)
                        st.caption("⬆️ 复制链接 → 浏览器打开 → MetaMask 下单")

                st.divider()

        # 底部统计
        if actionable_count > 0:
            st.success(f"🎯 {actionable_count} 个可操作市场 | 已有 AI 明确方向 | 去交易吧")
        elif st.session_state.show_only_actionable:
            st.warning("当前推荐市场 AI 尚未给出明确方向。关闭「只看可操作」查看全部，或手动逐个分析。")
        else:
            st.caption(f"🔍 {len(recommend)} 个推荐市场 | 信息优势 ≥ 5分 | 🚀 点批量分析找机会")
    else:
        if st.session_state.show_only_actionable:
            st.info("当前无 AI 确认的可操作市场。关闭「只看可操作」查看候选，或过几分钟刷新数据。")
        else:
            st.info("当前无信息优势 ≥ 5 分的市场。可能市场比较平静，过几分钟再刷新看看。")

    # ── AI 深度分析 ──
    st.markdown("---")
    st.markdown("## 🤖 AI 深度分析")

    has_api_key = bool(os.getenv("DEEPSEEK_API_KEY"))
    if not IS_CLOUD and not has_api_key:
        st.warning(
            "⚠️ 未配置 DeepSeek API Key。\n\n"
            "在项目目录的 `.env` 文件中添加:\n"
            "`DEEPSEEK_API_KEY=sk-your-key`\n\n"
            "注册地址: https://platform.deepseek.com（新用户送 500万 tokens）"
        )
    elif IS_CLOUD and not has_api_key:
        try:
            has_api_key = bool(st.secrets.get("DEEPSEEK_API_KEY", ""))
        except Exception:
            pass
        if not has_api_key:
            st.info(
                "💡 云端部署需要在 Streamlit Cloud 添加 secrets:\n"
                "`DEEPSEEK_API_KEY = sk-your-key`\n"
                "路径: App Settings → Secrets"
            )

    if has_api_key:
        ai_col1, ai_col2 = st.columns([3, 1])

        with ai_col1:
            # 让用户选市场 — 优先推荐市场 + 全局高EV
            recommend_ids = set(recommend["id"].tolist()) if not recommend.empty else set()
            # 推荐市场（优先）
            rec_for_ai = recommend.copy() if not recommend.empty else pd.DataFrame()
            # 全局高EV补充（排除已在推荐中的）
            global_ev = df[
                (df["ev_score"] >= 3) & (df["volume"] > 5000) &
                (~df["id"].isin(recommend_ids))
            ].nlargest(20, "ev_score")
            ai_candidates = pd.concat([rec_for_ai, global_ev], ignore_index=True).drop_duplicates(subset="id")

            if not ai_candidates.empty:
                ai_options = ai_candidates["question"].tolist()
                selected_question = st.selectbox(
                    "选择一个市场进行 AI 深度分析",
                    options=ai_options,
                    index=None,
                    placeholder="点击选择（推荐市场优先）...",
                    key="ai_select",
                )

                if selected_question:
                    row = ai_candidates[ai_candidates["question"] == selected_question].iloc[0]

                    with ai_col2:
                        st.markdown("<br>", unsafe_allow_html=True)  # 对齐
                        if st.button("🔍 开始 AI 分析", type="primary", use_container_width=True):
                            with st.status("🔎 联网搜索 + AI 分析中...", expanded=True) as status:
                                status.update(label="🔎 Step 1/2: DuckDuckGo + Twitter + 专题数据搜索...")
                                search_ctx = gather_context(
                                    question=row["question"],
                                    tags=row["tags"],
                                    ev_score=int(row["ev_score"]),
                                    yes_price=row["yes"],
                                    volume=row["volume"],
                                    ie_score=int(row["ie_score"]),
                                )
                                status.update(label="🧠 Step 2/2: DeepSeek 深度推理...")
                                result = analyze_market(
                                    question=row["question"],
                                    yes_price=row["yes"],
                                    no_price=row["no"],
                                    volume=row["volume"],
                                    ie_score=int(row["ie_score"]),
                                    end_date=str(row["end_date"]),
                                    ev_score=int(row["ev_score"]),
                                    ev_summary=row["ev_summary"],
                                    urgency_label=row["urgency_label"],
                                    tags=row["tags"],
                                    search_context=search_ctx,
                                )
                                st.session_state.ai_result = result
                                st.session_state.ai_market = row["question"]
                                st.session_state.ai_search_info = {
                                    "market_type": search_ctx["market_type"],
                                    "search_depth": search_ctx["search_depth"],
                                    "skipped": search_ctx["skipped"],
                                    "skip_reason": search_ctx.get("skip_reason", ""),
                                }
                                status.update(label="✅ 分析完成", state="complete")

                    # 显示分析结果
                    if st.session_state.get("ai_result") and st.session_state.get("ai_market") == row["question"]:
                        st.markdown("---")
                        st.markdown(f"### 📊 AI 分析结果: {row['question'][:60]}")

                        # 搜索状态标签
                        search_info = st.session_state.get("ai_search_info", {})
                        if search_info:
                            depth = search_info.get("search_depth", 0)
                            mtype = search_info.get("market_type", "")
                            if search_info.get("skipped"):
                                st.caption(f"🔍 搜索: 跳过（{search_info.get('skip_reason', '')}）| 市场类型: {mtype} | 纯 LLM 分析")
                            else:
                                st.caption(f"🔍 搜索深度: {'⭐' * depth} | 市场类型: {mtype} | RAG 增强分析")

                        # AI 方向标签
                        ai_res = st.session_state.ai_result
                        direction = ai_res.get("direction", "hold") if isinstance(ai_res, dict) else "hold"
                        conf = ai_res.get("confidence", 0) if isinstance(ai_res, dict) else 0
                        if direction == "buy_yes":
                            st.success(f"🤖 AI 方向: 买 YES | 自信度 {conf}星")
                        elif direction == "buy_no":
                            st.error(f"🤖 AI 方向: 买 NO | 自信度 {conf}星")
                        else:
                            st.warning(f"🤖 AI 方向: 观望 | 自信度 {conf}星")

                        text = ai_res.get("text", ai_res) if isinstance(ai_res, dict) else ai_res
                        st.info(text)

                        # 市场数据摘要
                        st.caption(
                            f"YES ${row['yes']:.4f} | NO ${row['no']:.4f} | "
                            f"成交量 ${row['volume']:,.0f} | EV {int(row['ev_score'])}分 | "
                            f"{row['end_date']}"
                        )

                        # 操作区
                        st.markdown("#### 📋 去交易")
                        st.code(row["url"], language=None)
                        st.caption("复制链接 → 浏览器打开 → MetaMask 下单")
            else:
                st.info("暂无足够数据用于 AI 分析，请稍后刷新。")

    # ── 所有博弈市场速览 ──
    st.markdown("---")
    st.markdown("### 📊 博弈市场一览（YES $0.35-$0.65）")

    game_df = df[df["yes"].between(0.35, 0.65)].sort_values("ev_score", ascending=False).head(20)
    if not game_df.empty:
        display_game = game_df[[
            "urgency_emoji", "question", "yes", "volume", "ev_score", "ev_summary", "end_date"
        ]].copy()
        display_game.columns = ["紧迫", "市场", "YES", "成交量", "EV分", "信号", "结束"]
        display_game["YES"]    = display_game["YES"].apply(lambda x: f"${x:.4f}")
        display_game["成交量"]  = display_game["成交量"].apply(lambda x: f"${x:,.0f}")
        st.dataframe(display_game, use_container_width=True, hide_index=True, height=400)
    else:
        st.info("当前无博弈区间市场。")


# ============================================================
# ╔═══════════════════════════════════════════════════════════╗
# ║              🔵 专业模式 主内容区                           ║
# ╚═══════════════════════════════════════════════════════════╝
# ============================================================
else:

    # 构建 Tab
    TAB_NAMES = ["📊 市场总览", "🎯 EV 计算器", "📈 信号分析", "🤖 AI 分析"]
    if not IS_CLOUD:
        TAB_NAMES.append("⚙️ 自动交易")

    tabs = st.tabs(TAB_NAMES)
    tab1, tab2, tab3, tab4 = tabs[0], tabs[1], tabs[2], tabs[3]
    tab5 = tabs[4] if not IS_CLOUD else None


    # ========== Tab 1: 市场总览 ==========
    with tab1:
        st.subheader("📊 市场数据表")

        display_filtered = filtered.sort_values("ev_score", ascending=False).copy()
        display_filtered["market_type"] = display_filtered["yes"].apply(
            lambda y: "🧠 博弈" if 0.35 <= y <= 0.65 else ("🎲 彩票" if y < 0.20 else "—")
        )

        display_cols = {
            "market_type":    "类型",
            "urgency_emoji":  "⏰",
            "question":       "市场",
            "yes":            "YES $",
            "no":             "NO $",
            "volume":         "成交量",
            "liquidity":      "流动性",
            "end_date":       "结束",
            "ev_score":       "EV分",
            "ev_summary":     "信号说明",
        }

        display_df = display_filtered[list(display_cols.keys())].copy()
        display_df.columns = list(display_cols.values())

        display_df["YES $"]   = display_df["YES $"].apply(lambda x: f"{x:.4f}")
        display_df["NO $"]    = display_df["NO $"].apply(lambda x: f"{x:.4f}")
        display_df["成交量"]   = display_df["成交量"].apply(lambda x: f"${x:,.0f}")
        display_df["流动性"]   = display_df["流动性"].apply(lambda x: f"${x:,.0f}")

        st.dataframe(
            display_df, use_container_width=True, hide_index=True, height=500,
            column_config={
                "⏰":     st.column_config.TextColumn("紧迫", width="small",
                            help="🔴<1h 🟠<6h 🟡<24h 🟢<3d ⚪>1w"),
                "市场":    st.column_config.TextColumn("市场名称", width="large"),
                "YES $":  st.column_config.TextColumn("YES", width="small",
                            help="$0.35-$0.65=博弈区间（推荐）"),
                "NO $":   st.column_config.TextColumn("NO", width="small"),
                "成交量":  st.column_config.TextColumn("24h量", width="small"),
                "EV分":   st.column_config.NumberColumn("EV", width="small",
                            help="多因子评分(0-12)"),
                "信号说明": st.column_config.TextColumn("信号", width="medium"),
                "类型":    st.column_config.TextColumn("类型", width="small",
                            help="🧠博弈 🎲彩票"),
            },
        )

        st.caption(f"显示 {len(filtered)} / {len(df)} 个市场 | 按 EV 评分降序")

        game_count   = len(display_filtered[display_filtered["market_type"] == "🧠 博弈"])
        lottery_count = len(display_filtered[display_filtered["market_type"] == "🎲 彩票"])
        st.caption(f"🧠 博弈 {game_count} | 🎲 彩票 {lottery_count}（不推荐交易）")


    # ========== Tab 2: EV 计算器 ==========
    with tab2:
        st.subheader("🎯 EV 计算器")
        st.markdown("**EV = (你的概率 - 市场价) × 赔率**")

        market_options = filtered["question"].tolist()
        if market_options:
            selected_market = st.selectbox(
                "选择一个市场", options=market_options,
                index=None, placeholder="点击选择...",
            )

            if selected_market:
                row = filtered[filtered["question"] == selected_market].iloc[0]
                col_left, col_right = st.columns([1, 1])

                with col_left:
                    st.markdown("### 📋 市场信息")
                    st.write(f"**{row['question']}**")
                    st.write(f"⏰ {row['end_date']} | 📊 ${row['volume']:,.0f} | 💧 ${row['liquidity']:,.0f}")
                    c1, c2 = st.columns(2)
                    with c1:
                        st.metric("YES", f"{row['yes']:.4f}")
                    with c2:
                        st.metric("NO", f"{row['no']:.4f}")

                with col_right:
                    st.markdown("### 🧠 你的判断")
                    direction = st.radio("押注方向", ["YES", "NO"], horizontal=True)
                    if direction == "YES":
                        default_est = min(row["yes"] + 0.05, 0.99)
                    else:
                        default_est = max(row["no"] + 0.05, 0.01)

                    estimated = st.slider("你估计的真实概率", 0.01, 0.99, default_est, 0.01)
                    market_price = row["yes"] if direction == "YES" else row["no"]
                    payout = 1.0 / market_price if market_price > 0 else 0
                    ev = (estimated - market_price) * payout
                    ev_pct = (estimated - market_price) / market_price * 100
                    edge = estimated - market_price

                    st.markdown("---")
                    st.markdown("### 📐 结果")
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        st.metric("期望值 EV", f"{ev:+.4f}")
                    with c2:
                        st.metric("EV%", f"{ev_pct:+.1f}%")
                    with c3:
                        st.metric("概率优势", f"{edge:+.4f}")

                    st.markdown("---")
                    if ev > 0.10:
                        st.success(f"✅ 强 EV 机会！每 $1 期望 ${1+ev:.2f}")
                    elif ev > 0.03:
                        st.info(f"💡 正 EV，值得考虑")
                    elif ev > 0:
                        st.warning(f"👀 微弱正 EV ({ev:.4f})")
                    elif ev > -0.03:
                        st.warning(f"⚠️ 轻微负 EV ({ev:.4f})")
                    else:
                        st.error(f"❌ 显著负 EV ({ev:.4f})")

                # ── 组合仓位计算器（对冲/套利） ──
                st.markdown("---")
                st.markdown("### 🧮 组合仓位计算器（双向持仓策略）")
                st.caption("同时买 YES + NO，寻找『猜错不亏、猜中大赚』的仓位配比")

                yes_price = float(row['yes'])
                no_price = float(row['no'])
                total_cost = yes_price + no_price

                # 基础信息
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.metric("YES 价格", f"${yes_price:.4f}")
                with c2:
                    st.metric("NO 价格", f"${no_price:.4f}")
                with c3:
                    st.metric("双向成本", f"${total_cost:.4f}", delta="套利!" if total_cost < 1.0 else None)

                if total_cost < 1.0:
                    st.success(f"🎯 **套利机会！** YES+NO=${total_cost:.4f} < $1.00，无风险套利空间 ${1-total_cost:.4f}（{(1-total_cost)/total_cost*100:.1f}%）")
                elif total_cost > 1.02:
                    st.warning(f"⚠️ 双向成本 ${total_cost:.4f} 偏高，需精准判断方向才能盈利")
                else:
                    st.info(f"💡 双向成本 ${total_cost:.4f}，接近平价")

                # 用户输入预算
                budget = st.number_input("💰 你的总预算 ($)", min_value=1.0, max_value=1000.0, value=10.0, step=1.0, key="hedge_budget")

                # 计算最优配比
                st.markdown("#### 📐 策略模拟")

                # 策略1: 等金额对冲（最保守）
                yes_amount_1 = budget / 2
                no_amount_1 = budget / 2
                yes_shares_1 = yes_amount_1 / yes_price if yes_price > 0 else 0
                no_shares_1 = no_amount_1 / no_price if no_price > 0 else 0
                profit_if_yes_1 = yes_shares_1 * (1 - yes_price) - no_amount_1  # YES赢时：YES盈利 - NO亏损
                profit_if_no_1 = no_shares_1 * (1 - no_price) - yes_amount_1   # NO赢时：NO盈利 - YES亏损

                # 策略2: 按价格反比配比（让两边潜在收益相等）
                # 设 YES 投 x, NO 投 (budget-x)
                # YES赢收益 = x/yes_price * (1-yes_price) - (budget-x) = x*(1-yes_price)/yes_price - budget + x
                # NO赢收益 = (budget-x)/no_price * (1-no_price) - x = (budget-x)*(1-no_price)/no_price - x
                # 令两边相等解 x
                if yes_price > 0 and no_price > 0:
                    # YES_win_profit = x * (1-yes_price)/yes_price - (budget-x)
                    #                = x * (1/yes_price - 1) - budget + x
                    #                = x / yes_price - budget
                    # NO_win_profit = (budget-x) * (1-no_price)/no_price - x
                    #               = (budget-x) / no_price - x
                    # 令相等: x/yes_price - budget = (budget-x)/no_price - x
                    # x/yes_price + x = budget + (budget-x)/no_price
                    # x * (1/yes_price + 1) = budget + budget/no_price - x/no_price
                    # x * (1/yes_price + 1 + 1/no_price) = budget * (1 + 1/no_price)
                    denom = (1/yes_price) + 1 + (1/no_price)
                    if denom > 0:
                        x_opt = budget * (1 + 1/no_price) / denom
                        x_opt = max(0, min(budget, x_opt))
                    else:
                        x_opt = budget / 2
                else:
                    x_opt = budget / 2

                yes_amount_2 = x_opt
                no_amount_2 = budget - x_opt
                yes_shares_2 = yes_amount_2 / yes_price if yes_price > 0 else 0
                no_shares_2 = no_amount_2 / no_price if no_price > 0 else 0
                profit_if_yes_2 = yes_shares_2 * (1 - yes_price) - no_amount_2
                profit_if_no_2 = no_shares_2 * (1 - no_price) - yes_amount_2

                # 策略3: 你判断方向，加重注
                your_direction = st.radio("你的判断方向（加重注）", ["均衡", "偏 YES", "偏 NO"], horizontal=True, key="hedge_dir")
                if your_direction == "偏 YES":
                    yes_amount_3 = budget * 0.7
                    no_amount_3 = budget * 0.3
                elif your_direction == "偏 NO":
                    yes_amount_3 = budget * 0.3
                    no_amount_3 = budget * 0.7
                else:
                    yes_amount_3 = budget * 0.5
                    no_amount_3 = budget * 0.5

                yes_shares_3 = yes_amount_3 / yes_price if yes_price > 0 else 0
                no_shares_3 = no_amount_3 / no_price if no_price > 0 else 0
                profit_if_yes_3 = yes_shares_3 * (1 - yes_price) - no_amount_3
                profit_if_no_3 = no_shares_3 * (1 - no_price) - yes_amount_3

                # 展示三种策略对比
                st.markdown("| 策略 | YES 仓位 | NO 仓位 | 若YES赢 | 若NO赢 | 最差情况 |")
                st.markdown("|------|----------|---------|---------|--------|----------|")

                def fmt_profit(p):
                    emoji = "🟢" if p > 0 else ("🔴" if p < 0 else "⚪")
                    return f"{emoji} ${p:+.2f}"

                st.markdown(f"| 等金额对冲 | ${yes_amount_1:.2f} | ${no_amount_1:.2f} | {fmt_profit(profit_if_yes_1)} | {fmt_profit(profit_if_no_1)} | {fmt_profit(min(profit_if_yes_1, profit_if_no_1))} |")
                st.markdown(f"| 收益平衡 | ${yes_amount_2:.2f} | ${no_amount_2:.2f} | {fmt_profit(profit_if_yes_2)} | {fmt_profit(profit_if_no_2)} | {fmt_profit(min(profit_if_yes_2, profit_if_no_2))} |")
                st.markdown(f"| {your_direction} | ${yes_amount_3:.2f} | ${no_amount_3:.2f} | {fmt_profit(profit_if_yes_3)} | {fmt_profit(profit_if_no_3)} | {fmt_profit(min(profit_if_yes_3, profit_if_no_3))} |")

                # 推荐策略
                best_strategy = None
                best_min_profit = -9999
                strategies = [
                    ("等金额对冲", profit_if_yes_1, profit_if_no_1),
                    ("收益平衡", profit_if_yes_2, profit_if_no_2),
                    (your_direction, profit_if_yes_3, profit_if_no_3),
                ]
                for name, py, pn in strategies:
                    min_p = min(py, pn)
                    if min_p > best_min_profit:
                        best_min_profit = min_p
                        best_strategy = name

                if best_min_profit > 0:
                    st.success(f"🎯 **推荐策略：{best_strategy}** — 无论结果如何都盈利！最差盈利 ${best_min_profit:.2f}")
                elif best_min_profit > -budget * 0.1:
                    st.info(f"💡 **推荐策略：{best_strategy}** — 风险可控，最差亏损 ${abs(best_min_profit):.2f}（{abs(best_min_profit)/budget*100:.1f}%）")
                else:
                    st.warning(f"⚠️ **{best_strategy}** 相对最优，但双向成本高，建议精准判断单一方向")

                st.caption("💡 提示：Polymarket 对赢家收取 2% 手续费，实际收益略低于上述计算")
        else:
            st.info("无符合筛选条件的市场。")


    # ========== Tab 3: 信号分析 ==========
    with tab3:
        st.subheader("📈 信号 & 推荐交易")

        # --- 推荐筛选：信息优势驱动（Tab3 也需要定义 recommend） ---
        recommend = df[
            (df["ie_score"] >= 5) & (df["volume"] > 5000)
        ].sort_values("ie_score", ascending=False).head(8)

        # 单个 AI 分析（与新手模式共享 ai_pending 逻辑）
        if st.session_state.ai_pending:
            pid = st.session_state.ai_pending
            target = df[df["id"] == pid]
            if not target.empty:
                row = target.iloc[0]
                with st.status(f"🤖 AI 分析: {row['question'][:40]}...", expanded=True) as status:
                    status.update(label="🔎 搜索最新信息...")
                    search_ctx = gather_context(
                        question=row["question"], tags=row["tags"],
                        ev_score=int(row["ev_score"]), yes_price=row["yes"], volume=row["volume"], ie_score=int(row["ie_score"]),
                    )
                    status.update(label="🧠 DeepSeek 推理...")
                    result = analyze_market(
                        question=row["question"], yes_price=row["yes"], no_price=row["no"],
                        volume=row["volume"], end_date=str(row["end_date"]),
                        ev_score=int(row["ev_score"]), ev_summary=row["ev_summary"],
                        urgency_label=row["urgency_label"], tags=row["tags"],
                        search_context=search_ctx,
                    )
                    st.session_state.ai_results[pid] = {
                        "text": result.get("text", ""),
                        "direction": result.get("direction", "hold"),
                        "confidence": result.get("confidence", 0),
                        "summary": result.get("summary", ""),
                        "search_info": {
                            "market_type": search_ctx["market_type"],
                            "search_depth": search_ctx["search_depth"],
                            "skipped": search_ctx["skipped"],
                            "skip_reason": search_ctx.get("skip_reason", ""),
                        },
                        "question": row["question"], "yes": row["yes"], "no": row["no"],
                        "volume": row["volume"], "ev_score": int(row["ev_score"]),
                        "end_date": row["end_date"], "url": row["url"],
                    }
                    status.update(label="✅ 分析完成", state="complete")
            st.session_state.ai_pending = None

        # 批量 AI 分析
        if st.session_state.batch_ai_pending:
            recommend_batch = df[
                (df["ie_score"] >= 5) & (df["volume"] > 5000)
            ].sort_values("ie_score", ascending=False).head(8)

            batch_ids = recommend_batch["id"].tolist()
            total_batch = len(batch_ids)
            st.info(f"🚀 批量 AI 分析中...共 {total_batch} 个市场")

            progress_bar = st.progress(0, text="准备...")
            for i, rid in enumerate(batch_ids):
                if rid in st.session_state.ai_results:
                    continue
                target = df[df["id"] == rid]
                if target.empty:
                    continue
                row = target.iloc[0]
                progress_bar.progress(
                    (i + 1) / total_batch,
                    text=f"🤖 ({i+1}/{total_batch}) 分析: {row['question'][:40]}...",
                )
                search_ctx = gather_context(
                    question=row["question"], tags=row["tags"],
                    ev_score=int(row["ev_score"]), yes_price=row["yes"], volume=row["volume"], ie_score=int(row["ie_score"]),
                )
                result = analyze_market(
                    question=row["question"], yes_price=row["yes"], no_price=row["no"],
                    volume=row["volume"], end_date=str(row["end_date"]),
                    ev_score=int(row["ev_score"]), ev_summary=row["ev_summary"],
                    urgency_label=row["urgency_label"], tags=row["tags"],
                    search_context=search_ctx,
                )
                st.session_state.ai_results[rid] = {
                    "text": result.get("text", ""),
                    "direction": result.get("direction", "hold"),
                    "confidence": result.get("confidence", 0),
                    "summary": result.get("summary", ""),
                    "search_info": {
                        "market_type": search_ctx["market_type"],
                        "search_depth": search_ctx["search_depth"],
                        "skipped": search_ctx["skipped"],
                        "skip_reason": search_ctx.get("skip_reason", ""),
                    },
                    "question": row["question"], "yes": row["yes"], "no": row["no"],
                    "volume": row["volume"], "ev_score": int(row["ev_score"]),
                    "end_date": row["end_date"], "url": row["url"],
                }
                import time as _time
                _time.sleep(1.5)

            progress_bar.progress(1.0, text="✅ 分析完成!")
            st.session_state.batch_ai_pending = False
            st.session_state.show_only_actionable = True
            st.rerun()

        # 单个 AI 分析
        if st.session_state.ai_pending:
            pid = st.session_state.ai_pending
            target = df[df["id"] == pid]
            if not target.empty:
                row = target.iloc[0]
                with st.status(f"🤖 AI 分析: {row['question'][:40]}...", expanded=True) as status:
                    status.update(label="🔎 搜索最新信息...")
                    search_ctx = gather_context(
                        question=row["question"], tags=row["tags"],
                        ev_score=int(row["ev_score"]), yes_price=row["yes"], volume=row["volume"], ie_score=int(row["ie_score"]),
                    )
                    status.update(label="🧠 DeepSeek 推理...")
                    result = analyze_market(
                        question=row["question"], yes_price=row["yes"], no_price=row["no"],
                        volume=row["volume"], end_date=str(row["end_date"]),
                        ev_score=int(row["ev_score"]), ev_summary=row["ev_summary"],
                        urgency_label=row["urgency_label"], tags=row["tags"],
                        search_context=search_ctx,
                    )
                    st.session_state.ai_results[pid] = {
                        "text": result.get("text", ""),
                        "direction": result.get("direction", "hold"),
                        "confidence": result.get("confidence", 0),
                        "summary": result.get("summary", ""),
                        "search_info": {
                            "market_type": search_ctx["market_type"],
                            "search_depth": search_ctx["search_depth"],
                            "skipped": search_ctx["skipped"],
                            "skip_reason": search_ctx.get("skip_reason", ""),
                        },
                        "question": row["question"], "yes": row["yes"], "no": row["no"],
                        "volume": row["volume"], "ev_score": int(row["ev_score"]),
                        "end_date": row["end_date"], "url": row["url"],
                    }
                    status.update(label="✅ 分析完成", state="complete")
            st.session_state.ai_pending = None

        # --- 推荐：信息优势驱动 ---
        st.markdown("### 🎯 推荐交易（信息优势驱动）")
        st.caption("信息优势 ≥5 分 + 成交量 > $5K → AI 验证方向 → 找到可赚钱的信息差")

        # recommend 已在 Tab3 开头定义，这里复用
        # "只看可操作"过滤
        actionable_count = 0
        if st.session_state.show_only_actionable and st.session_state.ai_results:
            actionable_ids = []
            for _, row in recommend.iterrows():
                rid = row["id"]
                if rid in st.session_state.ai_results:
                    d = st.session_state.ai_results[rid].get("direction", "hold")
                    if d in ("buy_yes", "buy_no"):
                        actionable_ids.append(rid)
            if actionable_ids:
                recommend = recommend[recommend["id"].isin(actionable_ids)]
                actionable_count = len(actionable_ids)

        # 控制栏
        ctl1, ctl2, ctl3 = st.columns([2, 1, 1])
        with ctl1:
            if st.button("🚀 批量 AI 分析推荐", type="primary", use_container_width=True,
                         key="pro_batch_ai_btn",
                         help="对推荐市场逐个运行 AI 深度分析（含联网搜索），找到真正有信息差的机会"):
                st.session_state.batch_ai_pending = True
                st.rerun()
        with ctl2:
            analyzed_count = len([
                rid for _, row in recommend.iterrows()
                if row["id"] in st.session_state.ai_results
            ])
            st.caption(f"已分析: {analyzed_count}/{len(recommend)}")
        with ctl3:
            if st.session_state.ai_results:
                show_only = st.toggle(
                    "只看可操作", value=st.session_state.show_only_actionable,
                    key="pro_show_actionable_toggle",
                    help="开启后只显示 AI 明确给出买 YES/买 NO 的市场"
                )
                st.session_state.show_only_actionable = show_only

        if not recommend.empty:
            for pos, (idx, row) in enumerate(recommend.iterrows()):
                rid = row["id"]
                ie = int(row["ie_score"])
                yes = row["yes"]

                # 信息优势评级
                if ie >= 8:
                    ie_star = "🟢"
                    ie_label = "高信息优势"
                elif ie >= 6:
                    ie_star = "🟡"
                    ie_label = "有信息机会"
                else:
                    ie_star = "🟠"
                    ie_label = "可关注"

                # AI 方向
                ai_direction = None
                ai_confidence = 0
                if rid in st.session_state.ai_results:
                    ai_res = st.session_state.ai_results[rid]
                    ai_direction = ai_res.get("direction")
                    ai_confidence = ai_res.get("confidence", 0)

                if st.session_state.show_only_actionable and ai_direction == "hold":
                    continue

                with st.container():
                    rc1, rc2, rc3, rc4, rc5, rc6, rc7 = st.columns([2.2, 0.9, 0.8, 0.9, 1.3, 1.5, 1.3])
                    with rc1:
                        st.markdown(f"### {ie_star} #{pos+1}  {row['question'][:60]}")
                        st.caption(
                            f"{row['urgency_emoji']} {row['end_date']} | "
                            f"信息优势: {ie}分 | {row['ie_rating']}"
                        )
                    with rc2:
                        st.metric("YES", f"${yes:.3f}")
                        st.caption(f"NO ${row['no']:.3f}")
                    with rc3:
                        st.metric("信息差", f"{ie}分", delta=ie_label)
                    with rc4:
                        st.metric("成交量", f"${row['volume']/1000:.0f}K")
                        st.caption(f"EV {int(row['ev_score'])}分")
                    with rc5:
                        if ai_direction == "buy_yes":
                            st.success(f"🤖 买YES {ai_confidence}★")
                        elif ai_direction == "buy_no":
                            st.error(f"🤖 买NO {ai_confidence}★")
                        elif ai_direction == "hold":
                            st.warning(f"🤖 观望 {ai_confidence}★")
                        elif yes <= 0.45:
                            st.success("👉 偏买YES")
                        elif yes >= 0.55:
                            st.error("👉 偏买NO")
                        else:
                            st.info("⚖️ 均衡")
                    with rc6:
                        direct = row["direct_url"]
                        search = row["search_url"][:55] + "…" if len(row["search_url"]) > 58 else row["search_url"]
                        if direct:
                            st.markdown(f"[🔗 直达]({direct})")
                        st.code(search, language=None)
                    with rc7:
                        already = rid in st.session_state.ai_results
                        if already:
                            st.markdown('<span class="analyzed-badge">✅ 已分析</span>', unsafe_allow_html=True)
                        if st.button(
                            "🤖分析" if not already else "🔄重分析",
                            key=f"pro_card_tab3_ai_btn_{rid}",
                            use_container_width=True,
                            help="AI 深度分析：为什么推荐？怎样赚钱？",
                        ):
                            st.session_state.ai_pending = rid
                            st.rerun()

                    # 展示 AI 结果
                    if rid in st.session_state.ai_results:
                        ai_data = st.session_state.ai_results[rid]
                        with st.expander("📊 AI 分析结果", expanded=(ai_direction != "hold")):
                            search_info = ai_data.get("search_info", {})
                            depth = search_info.get("search_depth", 0)
                            mtype = search_info.get("market_type", "")
                            if search_info.get("skipped"):
                                st.caption(f"🔍 搜索: 跳过（{search_info.get('skip_reason', '')}）| {mtype} | 纯 LLM 分析")
                            else:
                                st.caption(f"🔍 搜索: {'⭐' * depth} | {mtype} | RAG 增强分析")
                            direction = ai_data.get("direction", "hold")
                            conf = ai_data.get("confidence", 0)
                            if direction == "buy_yes":
                                st.success(f"🤖 AI 方向: 买 YES | 自信度 {conf}星")
                            elif direction == "buy_no":
                                st.error(f"🤖 AI 方向: 买 NO | 自信度 {conf}星")
                            else:
                                st.warning(f"🤖 AI 方向: 观望 | 自信度 {conf}星")
                            st.info(ai_data.get("text", ""))
                            st.caption(
                                f"YES ${ai_data['yes']:.4f} | EV {ai_data['ev_score']}分 | {ai_data['end_date']}"
                            )
                            st.code(ai_data["url"], language=None)
                    st.divider()

            if actionable_count > 0:
                st.success(f"🎯 {actionable_count} 个可操作市场 | 已有 AI 明确方向 | 去交易吧")
            elif st.session_state.show_only_actionable:
                st.warning("当前推荐市场 AI 尚未给出明确方向。关闭「只看可操作」查看全部，或手动逐个分析。")
            else:
                st.caption(f"🔍 {len(recommend)} 个推荐市场 | 信息优势 ≥ 5分 | 🚀 点批量分析找机会")
        else:
            if st.session_state.show_only_actionable:
                st.info("当前无 AI 确认的可操作市场。关闭「只看可操作」查看候选，或过几分钟刷新数据。")
            else:
                st.info("当前无信息优势 ≥ 5 分的市场。调整筛选条件或过几分钟刷新。")

        st.markdown("---")
        signals_df = df[df["ev_score"] > 0].copy()
        if not signals_df.empty:
            st.markdown("### 信号分布")
            col1, col2 = st.columns(2)
            with col1:
                score_groups = signals_df.groupby("ev_summary").size().reset_index(name="数量")
                score_groups.columns = ["级别", "数量"]
                st.dataframe(score_groups, use_container_width=True, hide_index=True)
            with col2:
                st.markdown("### 🏆 TOP 10 信号")
                top = signals_df.nlargest(10, "ev_score")[
                    ["urgency_emoji", "question", "yes", "volume", "ev_score", "ev_flags"]
                ].copy()
                top.columns = ["紧迫", "市场", "YES", "量", "EV", "信号"]
                top["YES"]   = top["YES"].apply(lambda x: f"{x:.4f}")
                top["量"]    = top["量"].apply(lambda x: f"${x:,.0f}")
                st.dataframe(top, use_container_width=True, hide_index=True)

        # 争议市场
        st.markdown("### 🔥 争议市场 (YES $0.30-$0.70, 高量)")
        contested = df[(df["yes"].between(0.30, 0.70)) & (df["volume"] > 10000)].nlargest(10, "volume")[
            ["urgency_emoji", "question", "yes", "no", "volume", "end_date"]
        ].copy()
        contested.columns = ["紧迫", "市场", "YES", "NO", "成交量", "结束"]
        contested["YES"]   = contested["YES"].apply(lambda x: f"{x:.4f}")
        contested["NO"]    = contested["NO"].apply(lambda x: f"{x:.4f}")
        contested["成交量"] = contested["成交量"].apply(lambda x: f"${x:,.0f}")
        st.dataframe(contested, use_container_width=True, hide_index=True)


    # ========== Tab 4: AI 分析 ==========
    with tab4:
        st.subheader("🤖 DeepSeek AI 智能分析")
        st.caption("AI 结合新闻、常识、历史规律，对市场做深度分析")

        has_api_key = bool(os.getenv("DEEPSEEK_API_KEY"))
        if not IS_CLOUD and not has_api_key:
            st.warning("⚠️ 未配置 DEEPSEEK_API_KEY。在 `.env` 中添加 `DEEPSEEK_API_KEY=sk-your-key`")
        elif IS_CLOUD and not has_api_key:
            try:
                has_api_key = bool(st.secrets.get("DEEPSEEK_API_KEY", ""))
            except Exception:
                pass
            if not has_api_key:
                st.info("💡 云端: 在 App Settings → Secrets 添加 DEEPSEEK_API_KEY")

        if not has_api_key:
            st.info("🔑 注册 https://platform.deepseek.com — 新用户送 500 万 tokens，够分析 1 万次。")
        else:
            st.success("✅ DeepSeek API 已连接")

        # 选市场分析 — 推荐市场优先 + 全局高EV补充
        recommend_ids = set(recommend["id"].tolist()) if not recommend.empty else set()
        rec_for_ai = recommend.copy() if not recommend.empty else pd.DataFrame()
        global_ev = df[
            (df["ev_score"] >= 3) & (df["volume"] > 5000) &
            (~df["id"].isin(recommend_ids))
        ].nlargest(20, "ev_score")
        ai_candidates = pd.concat([rec_for_ai, global_ev], ignore_index=True).drop_duplicates(subset="id")

        if not ai_candidates.empty:
            ai_options = ai_candidates["question"].tolist()
            ai_sel = st.selectbox(
                "选择市场进行 AI 深度分析",
                options=ai_options, index=None,
                placeholder="点击选择（推荐市场优先）...",
                key="pro_ai_select",
            )

            if ai_sel:
                row = ai_candidates[ai_candidates["question"] == ai_sel].iloc[0]

                if st.button("🔍 开始 AI 分析", type="primary", key="pro_ai_btn"):
                    with st.status("🔎 联网搜索 + AI 分析中...", expanded=True) as status:
                        status.update(label="🔎 Step 1/2: 搜索最新信息...")
                        search_ctx = gather_context(
                            question=row["question"],
                            tags=row["tags"],
                            ev_score=int(row["ev_score"]),
                            yes_price=row["yes"],
                            volume=row["volume"],
                            ie_score=int(row["ie_score"]),
                        )
                        status.update(label="🧠 Step 2/2: DeepSeek 深度推理...")
                        result = analyze_market(
                            question=row["question"],
                            yes_price=row["yes"],
                            no_price=row["no"],
                            volume=row["volume"],
                            end_date=str(row["end_date"]),
                            ev_score=int(row["ev_score"]),
                            ev_summary=row["ev_summary"],
                            urgency_label=row["urgency_label"],
                            tags=row["tags"],
                            search_context=search_ctx,
                        )
                        st.session_state.ai_result_pro = result
                        st.session_state.ai_market_pro = row["question"]
                        st.session_state.ai_search_info_pro = {
                            "market_type": search_ctx["market_type"],
                            "search_depth": search_ctx["search_depth"],
                            "skipped": search_ctx["skipped"],
                            "skip_reason": search_ctx.get("skip_reason", ""),
                        }
                        status.update(label="✅ 分析完成", state="complete")

                if st.session_state.get("ai_result_pro") and st.session_state.get("ai_market_pro") == row["question"]:
                    st.markdown("---")
                    st.markdown("### 📊 AI 分析结果")

                    # 搜索状态标签
                    search_info = st.session_state.get("ai_search_info_pro", {})
                    if search_info:
                        depth = search_info.get("search_depth", 0)
                        mtype = search_info.get("market_type", "")
                        if search_info.get("skipped"):
                            st.caption(f"🔍 搜索: 跳过（{search_info.get('skip_reason', '')}）| 市场类型: {mtype} | 纯 LLM 分析")
                        else:
                            st.caption(f"🔍 搜索深度: {'⭐' * depth} | 市场类型: {mtype} | RAG 增强分析")

                    # AI 方向标签
                    ai_res = st.session_state.ai_result_pro
                    direction = ai_res.get("direction", "hold") if isinstance(ai_res, dict) else "hold"
                    conf = ai_res.get("confidence", 0) if isinstance(ai_res, dict) else 0
                    if direction == "buy_yes":
                        st.success(f"🤖 AI 方向: 买 YES | 自信度 {conf}星")
                    elif direction == "buy_no":
                        st.error(f"🤖 AI 方向: 买 NO | 自信度 {conf}星")
                    else:
                        st.warning(f"🤖 AI 方向: 观望 | 自信度 {conf}星")

                    text = ai_res.get("text", ai_res) if isinstance(ai_res, dict) else ai_res
                    st.info(text)
                    st.caption(
                        f"YES ${row['yes']:.4f} | NO ${row['no']:.4f} | "
                        f"成交量 ${row['volume']:,.0f} | EV {int(row['ev_score'])}分 | {row['end_date']}"
                    )
                    st.markdown("#### 📋 去交易")
                    st.code(row["url"], language=None)

            # ========== 批量联网深度扫描（analyze_market + RAG）==========
            st.markdown("---")
            st.markdown("### 🌐 批量联网深度扫描 (Top 5 RAG)")
            st.caption("与新手模式同款分析：DuckDuckGo 实时搜索 + DeepSeek 深度推理 → 结构化方向+自信度")

            top5 = ai_candidates.head(5)
            top5_unanalyzed = [
                rid for rid in top5["id"].tolist()
                if rid not in st.session_state.batch_scan_results
            ]

            # 自动批量：toggle 开启 + 有未分析市场 → 自动触发
            auto_toggle = st.toggle(
                "🔄 启动时自动批量分析",
                value=st.session_state.auto_batch_enabled,
                key="pro_auto_batch_toggle",
                help="开启后每次数据刷新自动对 Top 5 执行联网深度分析（有 API 消耗）",
            )
            st.session_state.auto_batch_enabled = auto_toggle

            if auto_toggle and top5_unanalyzed and not st.session_state.batch_scan_pending:
                st.session_state.batch_scan_pending = True
                st.rerun()

            # 执行批量扫描
            if st.session_state.batch_scan_pending:
                total = len(top5)
                st.info(f"🌐 联网深度扫描中...共 {total} 个市场（DuckDuckGo 搜索 + AI 分析）")
                progress_bar = st.progress(0, text="准备...")
                for i, (_, r) in enumerate(top5.iterrows()):
                    rid = r["id"]
                    if rid in st.session_state.batch_scan_results:
                        continue
                    progress_bar.progress(
                        (i + 1) / total,
                        text=f"🤖 ({i+1}/{total}) 分析: {r['question'][:40]}...",
                    )
                    search_ctx = gather_context(
                        question=r["question"], tags=r["tags"],
                        ev_score=int(r["ev_score"]), yes_price=r["yes"], volume=r["volume"], ie_score=int(r["ie_score"]),
                    )
                    result = analyze_market(
                        question=r["question"], yes_price=r["yes"], no_price=r["no"],
                        volume=r["volume"], end_date=str(r["end_date"]),
                        ev_score=int(r["ev_score"]), ev_summary=r["ev_summary"],
                        urgency_label=r["urgency_label"], tags=r["tags"],
                        search_context=search_ctx,
                    )
                    st.session_state.batch_scan_results[rid] = {
                        "text": result.get("text", ""),
                        "direction": result.get("direction", "hold"),
                        "confidence": result.get("confidence", 0),
                        "summary": result.get("summary", ""),
                        "search_info": {
                            "market_type": search_ctx["market_type"],
                            "search_depth": search_ctx["search_depth"],
                            "skipped": search_ctx["skipped"],
                            "skip_reason": search_ctx.get("skip_reason", ""),
                        },
                        "question": r["question"], "yes": r["yes"], "no": r["no"],
                        "volume": r["volume"], "ev_score": int(r["ev_score"]),
                        "end_date": r["end_date"], "url": r["url"],
                    }
                    import time as _time
                    _time.sleep(1.5)
                progress_bar.progress(1.0, text="✅ 深度分析完成!")
                st.session_state.batch_scan_pending = False
                st.rerun()

            # 手动触发按钮
            analyzed_in_batch = len(st.session_state.batch_scan_results)
            b1, b2 = st.columns([2, 1])
            with b1:
                if st.button(
                    "🚀 联网深度扫描 Top 5",
                    type="primary",
                    use_container_width=True,
                    key="pro_deep_batch_btn",
                    disabled=bool(st.session_state.batch_scan_pending),
                    help="对 Top 5 市场执行 DuckDuckGo 实时搜索 + DeepSeek 深度分析",
                ):
                    st.session_state.batch_scan_pending = True
                    st.rerun()
            with b2:
                st.caption(f"已分析: {analyzed_in_batch}/{total_top5 if (total_top5 := len(top5)) else 0}")

            # 展示结果
            if st.session_state.batch_scan_results:
                st.markdown("---")
                st.markdown("### 📊 批量扫描结果")
                results_to_show = top5[top5["id"].isin(st.session_state.batch_scan_results)]
                for i, (_, r) in enumerate(results_to_show.iterrows()):
                    rid = r["id"]
                    ai_data = st.session_state.batch_scan_results[rid]
                    direction = ai_data.get("direction", "hold")
                    conf = ai_data.get("confidence", 0)

                    emoji_map = {"buy_yes": "🟢", "buy_no": "🔴", "hold": "🟡"}
                    dir_label = {"buy_yes": "买 YES", "buy_no": "买 NO", "hold": "观望"}
                    st.markdown(
                        f"**{i+1}. {emoji_map.get(direction, '⚪')} {ai_data['question'][:60]}**"
                    )
                    c1, c2, c3, c4 = st.columns(4)
                    with c1:
                        st.metric("方向", dir_label.get(direction, direction))
                    with c2:
                        st.metric("自信度", f"{'⭐' * conf}" if conf else "—")
                    with c3:
                        st.metric("YES", f"${ai_data['yes']:.4f}")
                    with c4:
                        st.metric("EV", f"{ai_data['ev_score']}分")

                    # 搜索状态
                    si = ai_data.get("search_info", {})
                    depth = si.get("search_depth", 0)
                    if si.get("skipped"):
                        st.caption(f"🔍 搜索: 跳过（{si.get('skip_reason', '')}）| 纯 LLM 分析")
                    else:
                        st.caption(f"🔍 搜索: {'⭐' * depth} | {si.get('market_type', '')} | RAG 增强")

                    with st.expander("📊 查看完整分析"):
                        st.info(ai_data.get("text", ""))
                        st.caption(f"NO ${ai_data['no']:.4f} | 量 ${ai_data['volume']:,.0f} | {ai_data['end_date']}")
                        st.code(ai_data["url"], language=None)
                    st.divider()
        else:
            st.info("暂无足够数据用于 AI 分析。")


    # ========== Tab 5: 自动交易 (仅本地) ==========
    if tab5:
        with tab5:
            st.subheader("⚙️ 自动交易引擎")
            st.caption("本地 daemon 管理（云端不可用）")

            status   = _load_status()
            config   = _load_config()
            history  = _load_history()

            col1, col2, col3, col4 = st.columns(4)
            daemon_status   = status.get("status", "unknown")
            status_emoji_map = {"running": "🟢", "scanning": "🔵", "idle": "🟢", "stopped": "⚫", "error": "🔴"}

            with col1:
                st.metric("状态", f"{status_emoji_map.get(daemon_status, '⚪')} {daemon_status}")
            with col2:
                st.metric("今日交易", status.get("trade_count_today", "—"))
            with col3:
                st.metric("上次扫描", str(status.get("last_scan", "—"))[:16] if status.get("last_scan") else "—")
            with col4:
                st.metric("最强信号", f"EV={status.get('top_score', '—')}" if status.get("top_score") else "—")

            st.markdown("---")
            st.markdown("### ⚙️ 控制面板")

            # 进程检测
            pid_file   = BASE_DIR / "auto_trader.pid"
            is_running = False
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    handle = kernel32.OpenProcess(1, False, pid)
                    if handle:
                        kernel32.CloseHandle(handle)
                        is_running = True
                    else:
                        pid_file.unlink(missing_ok=True)
                except Exception:
                    pid_file.unlink(missing_ok=True)

            ctrl1, ctrl2, ctrl3 = st.columns([1, 1, 2])
            with ctrl1:
                if is_running:
                    if st.button("⏹️ 停止", type="primary", use_container_width=True):
                        try:
                            pid = int(pid_file.read_text().strip())
                            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
                            pid_file.unlink(missing_ok=True)
                            st.success("已停止"); st.rerun()
                        except Exception as e:
                            st.error(f"失败: {e}")
                else:
                    if st.button("▶️ 启动", use_container_width=True):
                        cmd = (
                            f'Start-Process -FilePath "{sys.executable}" '
                            f'-ArgumentList "auto_trader.py" '
                            f'-WorkingDirectory "{BASE_DIR}" -WindowStyle Hidden'
                        )
                        try:
                            subprocess.Popen(["powershell", "-Command", cmd], cwd=str(BASE_DIR))
                            st.success("✅ 已启动"); time.sleep(2); st.rerun()
                        except Exception as e:
                            st.error(f"失败: {e}")

            with ctrl2:
                if st.button("🔍 单次扫描", use_container_width=True):
                    with st.status("🔍 扫描中...", expanded=True) as scan_status:
                        result = subprocess.run(
                            [sys.executable, "auto_trader.py", "--once", "--dry-run"],
                            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=120
                        )
                        st.text(result.stdout[-500:] if result.stdout else "(无输出)")
                        scan_status.update(label="✅ 扫描完成", state="complete")
                        st.rerun()

            with ctrl3:
                if is_running:
                    st.success(f"✅ 运行中 (PID: {pid_file.read_text().strip() if pid_file.exists() else '?'})")
                else:
                    st.info("ℹ️ 未启动")

            # Dry-Run
            st.markdown("---")
            dr1, dr2 = st.columns([2, 1])
            with dr1:
                current_dry = config.get("dry_run", True)
                new_dry = st.toggle("🧪 Dry-Run（不实际下单）", value=current_dry, disabled=is_running)
                if new_dry != current_dry:
                    config["dry_run"] = new_dry; _save_config(config); st.rerun()
            with dr2:
                st.warning("⚠️ 模拟") if current_dry else st.error("🔴 Live")

            # 配置编辑
            st.markdown("---")
            with st.expander("📝 规则配置", expanded=False):
                c1, c2 = st.columns(2)
                with c1:
                    ni = st.number_input("扫描间隔(分)", 1, 120, config.get("scan_interval_min", 5), disabled=is_running)
                    nm = st.slider("最低EV", 1, 8, config.get("min_ev_score", 4), disabled=is_running)
                    nv = st.number_input("最低量($)", 10, 50000, config.get("min_volume", 100), 50, disabled=is_running)
                    na = st.number_input("单笔上限($)", 1.0, 100.0, float(config.get("max_amount_per_trade", 5.0)), 1.0, disabled=is_running)
                    nd = st.number_input("日上限(次)", 1, 50, config.get("max_daily_trades", 10), disabled=is_running)
                    nc = st.number_input("冷却(分)", 5, 240, config.get("cooldown_minutes", 30), disabled=is_running)
                with c2:
                    st.markdown("**紧迫度**")
                    urg_labels = {1: "🔴1h", 2: "🟠6h", 3: "🟡24h", 4: "🟢3d", 5: "🔵1w", 6: "⚪>1w"}
                    nu = []
                    for lvl, label in urg_labels.items():
                        if st.checkbox(label, value=lvl in config.get("urgency_filter", [1,2,3]), key=f"urg_{lvl}", disabled=is_running):
                            nu.append(lvl)
                    st.markdown("**黑名单**")
                    bl = config.get("blacklist", [])
                    blt = st.text_area("每行一个slug", value="\n".join(bl), height=100, disabled=is_running)
                    nbl = [s.strip() for s in blt.split("\n") if s.strip()]

                if st.button("💾 保存", use_container_width=True, disabled=is_running):
                    config.update({
                        "scan_interval_min": ni, "min_ev_score": nm, "min_volume": nv,
                        "max_amount_per_trade": na, "max_daily_trades": nd,
                        "cooldown_minutes": nc, "urgency_filter": nu, "blacklist": nbl,
                        "dry_run": current_dry,
                    })
                    _save_config(config)
                    st.success("✅ 已保存"); st.rerun()

            # 历史
            st.markdown("---")
            st.markdown("### 📋 交易历史")
            if history:
                hist_df = pd.DataFrame(history)
                if not hist_df.empty:
                    dh = hist_df[["timestamp", "title", "side", "amount", "ev_score", "urgency", "status"]].copy()
                    dh.columns = ["时间", "市场", "方向", "金额", "EV", "紧迫", "状态"]
                    dh["时间"] = dh["时间"].apply(lambda x: x[:16] if isinstance(x, str) else x)
                    dh["金额"] = dh["金额"].apply(lambda x: f"${x:.0f}")
                    dh["方向"] = dh["方向"].apply(lambda x: f"🟢 YES" if x=="yes" else f"🔴 NO")
                    dh["状态"] = dh["状态"].apply(
                        lambda x: "✅已执行" if x=="executed" else ("🧪模拟" if x=="dry_run" else ("❌失败" if x=="failed" else x))
                    )
                    dh = dh.sort_values("时间", ascending=False).head(50)
                    st.dataframe(dh, use_container_width=True, hide_index=True, height=300)
            else:
                st.info("暂无交易记录")

            # 日志
            st.markdown("---")
            st.markdown("### 📜 最近日志")
            log_file = BASE_DIR / "auto_trader.log"
            if log_file.exists():
                try:
                    log_lines = log_file.read_text(encoding="utf-8").strip().split("\n")[-20:]
                    st.code("\n".join(log_lines), language="text")
                except Exception:
                    st.caption("无法读取日志")
            else:
                st.caption("暂无日志")


# ============================================================
# 底部
# ============================================================
st.divider()
st.caption(f"最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 数据: Polymarket Gamma API")
if mode == "beginner":
    st.caption("💡 想用完整筛选器？点侧边栏切换到「🔵 专业模式」")
else:
    st.caption("💡 想要简洁界面？点侧边栏切换到「🟢 新手模式」")
