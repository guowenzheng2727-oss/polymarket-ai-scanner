"""
Polymarket Trade CLI — 纯命令行交易工具
不依赖 Streamlit，直接终端操作，稳定不受代理影响

用法:
    # 交互模式（推荐）
    python trade_cli.py

    # 快捷模式（指定市场slg）
    python trade_cli.py --slug will-wti-hit-90 --side yes --amount 5

前置条件:
    Chrome 远程调试模式: chrome.exe --remote-debugging-port=9222
    VPN 全局模式（韩国）
"""

import os
import sys
import json
import subprocess
import argparse
from datetime import datetime, timezone

# 确保当前目录在 path 里
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scanner import fetch_markets, parse_prices, parse_end_time, time_urgency, ev_signal


# ============================================================
# 数据加载
# ============================================================
def load_ev_signals(limit: int = 300):
    """加载市场数据并返回 EV 信号列表"""
    print("📡 正在连接 Polymarket Gamma API...")

    all_markets = []
    for offset in [0, 100, 200]:
        batch = fetch_markets(limit=100, offset=offset)
        all_markets.extend(batch)
        if len(batch) < 100:
            break

    print(f"   拉取 {len(all_markets)} 个市场")

    now = datetime.now(timezone.utc)
    signals = []

    for m in all_markets:
        prices = parse_prices(m)
        end_dt = parse_end_time(m)
        urgency_level, urgency_label, urgency_emoji = time_urgency(end_dt, now=now)
        signal = ev_signal(m, prices)
        vol = float(m.get("volume", 0) or 0)

        if signal["score"] >= 2:  # 只保留有信号的
            signals.append({
                "question": m.get("question", "N/A"),
                "slug": m.get("slug", ""),
                "yes": prices["yes"],
                "no": prices["no"],
                "volume": vol,
                "end_date": end_dt.strftime("%m-%d %H:%M") if end_dt else "未知",
                "urgency_emoji": urgency_emoji,
                "urgency_label": urgency_label,
                "ev_score": signal["score"],
                "ev_flags": ", ".join(signal["flags"]),
                "ev_summary": signal["summary"],
            })

    # 按 EV 分数降序
    signals.sort(key=lambda x: -x["ev_score"])

    # 取前 20 个
    return signals[:20]


def show_signals(signals: list):
    """终端展示 EV 信号表格"""
    print(f"\n{'='*80}")
    print(f"  🎯 Polymarket EV 信号 TOP 20")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}")

    print(f"  {'#':>3} {'市场':<45} {'YES':>7} {'量':>8} {'EV':>3} {'结束':>12}")
    print(f"  {'-'*3} {'-'*45} {'-'*7} {'-'*8} {'-'*3} {'-'*12}")

    for i, s in enumerate(signals):
        print(f"  {i+1:>3} {s['question'][:43]:<45} {s['yes']:>7.4f} ${s['volume']:>6,.0f} {s['ev_score']:>3} {s['urgency_emoji']} {s['end_date']:>10}")

    print(f"\n  共 {len(signals)} 个信号市场")


# ============================================================
# 交易执行
# ============================================================
def run_trade(slug: str, side: str, amount: float):
    """调用 trader_browser.py 执行交易"""
    url = f"https://polymarket.com/event/{slug}"
    script_dir = os.path.dirname(os.path.abspath(__file__))

    cmd = [
        sys.executable, os.path.join(script_dir, "trader_browser.py"),
        "--url", url,
        "--side", side,
        "--amount", str(amount),
    ]

    print(f"\n🚀 执行交易: {side.upper()} ${amount} → {slug}")
    print(f"   URL: {url}")

    try:
        result = subprocess.run(cmd, cwd=script_dir, timeout=120)
        if result.returncode == 0:
            print("\n✅ 交易流程完成!")
        else:
            print(f"\n⚠️  交易返回码: {result.returncode}")
    except subprocess.TimeoutExpired:
        print("\n⏰ 交易超时（120s），请在浏览器中手动完成")
    except Exception as e:
        print(f"\n❌ 下单失败: {e}")


# ============================================================
# 交互模式
# ============================================================
def interactive_mode():
    """交互式选择市场 + 下单"""
    signals = load_ev_signals()

    if not signals:
        print("\n⚠️  当前无 EV 信号，请检查 VPN 连接")
        return

    show_signals(signals)

    while True:
        print(f"\n{'='*80}")
        choice = input("  选择市场编号 (1-20), r=刷新, q=退出: ").strip().lower()

        if choice == "q":
            print("👋 退出")
            break

        if choice == "r":
            signals = load_ev_signals()
            show_signals(signals)
            continue

        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(signals):
                print(f"❌ 请输入 1-{len(signals)} 之间的数字")
                continue
        except ValueError:
            print("❌ 请输入数字")
            continue

        market = signals[idx]
        print(f"\n  市场: {market['question']}")
        print(f"  YES: {market['yes']:.4f} | NO: {market['no']:.4f}")
        print(f"  成交量: ${market['volume']:,.0f} | EV 分数: {market['ev_score']}")
        print(f"  信号: {market['ev_flags']}")
        print(f"  Slug: {market['slug']}")

        # 选方向
        side = input("\n  方向 (yes/no, 默认 yes): ").strip().lower() or "yes"
        if side not in ("yes", "no"):
            print("❌ 请输入 yes 或 no")
            continue

        # 金额
        amount_str = input("  金额 $ (默认 5): ").strip() or "5"
        try:
            amount = float(amount_str)
            if amount <= 0:
                print("❌ 金额须大于 0")
                continue
        except ValueError:
            print("❌ 请输入有效数字")
            continue

        # 确认
        confirm = input(f"\n  确认买入 {side.upper()} ${amount} [Y/n]: ").strip().lower()
        if confirm and confirm != "y":
            print("已取消")
            continue

        run_trade(market["slug"], side, amount)

        # 问是否继续
        again = input("\n  继续交易? [Y/n]: ").strip().lower()
        if again and again != "y":
            print("👋 退出")
            break


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Polymarket Trade CLI")
    parser.add_argument("--slug", help="市场 slug（快捷模式）")
    parser.add_argument("--side", default="yes", choices=["yes", "no"], help="买入方向")
    parser.add_argument("--amount", type=float, default=5.0, help="交易金额 (USD)")
    parser.add_argument("--list", action="store_true", help="仅列出信号，不交易")

    args = parser.parse_args()

    if args.list:
        signals = load_ev_signals()
        show_signals(signals)
        return

    if args.slug:
        # 快捷模式
        run_trade(args.slug, args.side, args.amount)
    else:
        # 交互模式
        interactive_mode()


if __name__ == "__main__":
    main()
