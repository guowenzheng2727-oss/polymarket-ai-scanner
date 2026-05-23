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
)
from ai_analyzer import analyze_market, quick_scan


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
/* 紧凑卡片 */
.compact-card {
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 8px;
    background: #fafafa;
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
            "tags":            extract_tags(m),
            "heat_score":      calc_volume_rank(m),
            "ev_score":        signal["score"],
            "ev_flags":        ", ".join(signal["flags"]),
            "ev_summary":      signal["summary"],
            "hours_remaining": (end_dt - now).total_seconds() / 3600 if end_dt else None,
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

    recommend = df[
        (df["yes"].between(0.35, 0.65)) &
        (df["ev_score"] >= 5) &
        (df["volume"] > 5000) &
        (df["urgency_level"].isin([1, 2, 3]))
    ].sort_values("ev_score", ascending=False).head(8)

    if not recommend.empty:
        for pos, (idx, row) in enumerate(recommend.iterrows()):
            ev = int(row["ev_score"])
            yes = row["yes"]
            vol = row["volume"]
            q = row["question"]
            url = row["url"]

            if ev >= 10:
                star = "🔥"
                level = "强烈推荐"
            elif ev >= 7:
                star = "⭐"
                level = "优质信号"
            else:
                star = "💡"
                level = "值得关注"

            with st.container():
                c1, c2, c3, c4, c5, c6 = st.columns([3, 1, 1, 1, 2, 1.5])
                with c1:
                    st.markdown(f"### {star} #{pos+1}  {q[:70]}")
                    st.caption(
                        f"{row['urgency_emoji']} {row['end_date']} | "
                        f"评分: {row['ev_summary']} | 标签: {row['tags']}"
                    )
                with c2:
                    st.metric("YES", f"${yes:.3f}")
                    st.caption(f"NO ${row['no']:.3f}")
                with c3:
                    st.metric("EV", f"{ev}分", delta=level)
                with c4:
                    st.metric("成交量", f"${vol/1000:.0f}K")
                with c5:
                    # 建议方向：YES 价格在博弈区间内，判断买哪边
                    # Polymarket 页面：买 YES = 认为事件会发生；买 NO = 认为不会
                    if yes <= 0.40:
                        st.success("✅ 买 YES")
                        st.caption("价格偏低，值博率高")
                    elif yes >= 0.60:
                        st.error("✅ 买 NO")
                        st.caption("YES 偏高，NO 更划算")
                    elif yes < 0.50:
                        st.success("👉 倾向买 YES")
                        st.caption("YES 略偏低")
                    elif yes > 0.50:
                        st.error("👉 倾向买 NO")
                        st.caption("NO 略划算")
                    else:
                        st.info("⚖️ 两边均可")
                        st.caption("价格完全均衡")
                with c6:
                    # 优先显示直接链接，备用搜索链接
                    direct = row["direct_url"]
                    search = row["search_url"]
                    if direct:
                        st.markdown(f"[{row['question'][:30]}...]({direct})")
                        st.caption("📎 直接链接（点击跳转）")
                    st.code(search, language=None)
                    st.caption("🔍 搜索链接（备用）")
                st.divider()

        st.caption(f"🔍 {len(recommend)} 个博弈区间市场 | 💡 优先看前 3 个")
    else:
        st.info("当前无博弈区间推荐。可能市场比较平静，过几分钟再刷新看看。")

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
            # 让用户选市场
            ai_candidates = df[
                (df["ev_score"] >= 3) & (df["volume"] > 5000)
            ].nlargest(30, "ev_score")

            if not ai_candidates.empty:
                ai_options = ai_candidates["question"].tolist()
                selected_question = st.selectbox(
                    "选择一个市场进行 AI 深度分析",
                    options=ai_options,
                    index=None,
                    placeholder="点击选择...",
                    key="ai_select",
                )

                if selected_question:
                    row = ai_candidates[ai_candidates["question"] == selected_question].iloc[0]

                    with ai_col2:
                        st.markdown("<br>", unsafe_allow_html=True)  # 对齐
                        if st.button("🔍 开始 AI 分析", type="primary", use_container_width=True):
                            with st.spinner("🤔 AI 正在分析市场..."):
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
                                )
                                st.session_state.ai_result = result
                                st.session_state.ai_market = row["question"]

                    # 显示分析结果
                    if st.session_state.get("ai_result") and st.session_state.get("ai_market") == row["question"]:
                        st.markdown("---")
                        st.markdown(f"### 📊 AI 分析结果: {row['question'][:60]}")
                        st.info(st.session_state.ai_result)

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
        else:
            st.info("无符合筛选条件的市场。")


    # ========== Tab 3: 信号分析 ==========
    with tab3:
        st.subheader("📈 EV 信号分析")

        # 傻瓜推荐
        st.markdown("### 🎯 推荐交易（博弈区间 + 高EV）")
        st.caption("YES $0.35-$0.65 + EV≥5 + 量>$5K + 即将到期")

        recommend = df[
            (df["yes"].between(0.35, 0.65)) &
            (df["ev_score"] >= 5) &
            (df["volume"] > 5000) &
            (df["urgency_level"].isin([1, 2, 3]))
        ].sort_values("ev_score", ascending=False).head(10)

        if not recommend.empty:
            for pos, (idx, row) in enumerate(recommend.iterrows()):
                with st.container():
                    rc1, rc2, rc3, rc4, rc5 = st.columns([3, 1, 1, 1, 2])
                    with rc1:
                        st.markdown(f"**{row['urgency_emoji']} {row['question'][:60]}**")
                        st.caption(f"✅ {row['ev_summary']}")
                    with rc2:
                        st.metric("YES", f"{row['yes']:.3f}")
                    with rc3:
                        st.metric("EV", f"{int(row['ev_score'])}分")
                    with rc4:
                        st.metric("量", f"${row['volume']/1000:.0f}K")
                    with rc5:
                        st.code(row["url"], language=None)
                        st.caption("⬆️ 复制→浏览器下单")
                    st.divider()
            st.caption(f"🔍 {len(recommend)} 个推荐市场")
        else:
            st.info("当前无推荐，调整筛选条件再试。")

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

        # 选市场分析
        ai_candidates = df[(df["ev_score"] >= 3) & (df["volume"] > 5000)].nlargest(30, "ev_score")

        if not ai_candidates.empty:
            ai_options = ai_candidates["question"].tolist()
            ai_sel = st.selectbox(
                "选择市场进行 AI 深度分析",
                options=ai_options, index=None, placeholder="点击选择...",
                key="pro_ai_select",
            )

            if ai_sel:
                row = ai_candidates[ai_candidates["question"] == ai_sel].iloc[0]

                if st.button("🔍 开始 AI 分析", type="primary", key="pro_ai_btn"):
                    with st.spinner("🤔 AI 分析中..."):
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
                        )
                        st.session_state.ai_result_pro = result
                        st.session_state.ai_market_pro = row["question"]

                if st.session_state.get("ai_result_pro") and st.session_state.get("ai_market_pro") == row["question"]:
                    st.markdown("---")
                    st.markdown("### 📊 AI 分析结果")
                    st.info(st.session_state.ai_result_pro)
                    st.caption(
                        f"YES ${row['yes']:.4f} | NO ${row['no']:.4f} | "
                        f"成交量 ${row['volume']:,.0f} | EV {int(row['ev_score'])}分 | {row['end_date']}"
                    )
                    st.markdown("#### 📋 去交易")
                    st.code(row["url"], language=None)

            # 批量快速扫描
            st.markdown("---")
            st.markdown("### ⚡ 批量快速扫描 (Top 5)")
            if st.button("🚀 一键扫描 Top 5", key="pro_batch_scan"):
                top5 = ai_candidates.head(5)
                results = []
                for _, r in top5.iterrows():
                    with st.spinner(f"分析: {r['question'][:40]}..."):
                        res = quick_scan(r["question"], r["yes"], r["end_date"])
                        results.append({
                            "market": r["question"][:50],
                            "yes": f"${r['yes']:.4f}",
                            "ev": int(r["ev_score"]),
                            "ai": res,
                        })
                for i, r in enumerate(results):
                    st.markdown(f"**{i+1}. {r['market']}** | YES {r['yes']} | EV {r['ev']}分")
                    st.caption(r["ai"])
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
                    with st.spinner("扫描中..."):
                        result = subprocess.run(
                            [sys.executable, "auto_trader.py", "--once", "--dry-run"],
                            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=120
                        )
                        st.text(result.stdout[-500:] if result.stdout else "(无输出)")
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
