"""
Polymarket AI Scanner — 集成运行器
扫描器信号 → EV 筛选 → 交易建议 → (可选)自动下单

用法:
    python run.py                  # 扫描 + 显示建议 (只读)
    python run.py --auto           # 扫描 + paper 模式交易
    python run.py --live           # ⚠️ 真实交易 (需设置 POLYGON_PRIVATE_KEY)
"""

import sys
import json
import os
from datetime import datetime, timezone

from scanner import fetch_markets, parse_prices, parse_end_time, time_urgency, ev_signal
from trader import CLOBTrader, PolygonWallet, SignalTrader


def scan_markets(limit: int = 200) -> list:
    """运行扫描器，返回增强后的市场数据"""
    all_markets = []
    for offset in [0, 100]:
        batch = fetch_markets(limit=100, offset=offset)
        all_markets.extend(batch)
        if len(batch) < 100:
            break

    now = datetime.now(timezone.utc)
    enriched = []
    for m in all_markets:
        prices = parse_prices(m)
        end_dt = parse_end_time(m)
        urgency_level, urgency_label, urgency_emoji = time_urgency(end_dt, now=now)
        signal = ev_signal(m, prices)

        enriched.append({
            "id": m.get("id", ""),
            "question": m.get("question", "N/A"),
            "slug": m.get("slug", ""),
            "yes": prices["yes"],
            "no": prices["no"],
            "volume": float(m.get("volume", 0) or 0),
            "liquidity": float(m.get("liquidity", 0) or 0),
            "end_date": end_dt.strftime("%m-%d %H:%M") if end_dt else "未知",
            "urgency_emoji": urgency_emoji,
            "urgency_label": urgency_label,
            "ev_score": signal["score"],
            "ev_flags": signal["flags"],
            "ev_summary": signal["summary"],
            "raw": m,  # 保留原始数据供交易使用
        })

    return enriched


def get_trade_signals(enriched: list, min_ev_score: int = 3) -> list:
    """从扫描结果中提取交易信号"""
    signals = [m for m in enriched if m["ev_score"] >= min_ev_score]
    signals.sort(key=lambda x: -x["ev_score"])
    return signals


def suggest_trades(signals: list, max_per_signal: float = 10.0):
    """基于信号生成交易建议"""
    print(f"\n{'='*60}")
    print(f"  💡 交易建议 (基于 EV 信号)")
    print(f"{'='*60}")
    print(f"  信号总数: {len(signals)}")
    print()

    suggestions = []
    for i, s in enumerate(signals[:10]):
        yes = s["yes"]
        no = s["no"]

        # 决定方向和仓位
        if yes > 0.60:
            direction, confidence = "YES", min(s["ev_score"] / 10, 1.0)
        elif no > 0.60:
            direction, confidence = "NO", min(s["ev_score"] / 10, 1.0)
        else:
            direction, confidence = "YES", min(s["ev_score"] / 10 * 0.5, 1.0)

        amount = max_per_signal * confidence

        suggestion = {
            "rank": i + 1,
            "market": s["question"],
            "ev_score": s["ev_score"],
            "ev_summary": s["ev_summary"],
            "direction": direction,
            "amount": round(amount, 2),
            "current_price": yes if direction == "YES" else no,
            "confidence": round(confidence, 2),
            "urgency": s["urgency_emoji"],
        }
        suggestions.append(suggestion)

        print(f"  #{i+1} {s['urgency_emoji']} {s['question'][:60]}")
        print(f"     方向: {direction} @ {suggestion['current_price']:.4f}")
        print(f"     金额: ${amount:.2f} | 信心: {confidence:.2f} | EV分: {s['ev_score']}")
        print(f"     信号: {', '.join(s['ev_flags'][:2])}")
        print()

    return suggestions


def execute_trades(signals: list, mode: str = "paper"):
    """执行交易信号"""
    wallet = None
    if mode == "live":
        try:
            wallet = PolygonWallet.from_env()
        except ValueError as e:
            print(f"\n❌ {e}")
            print("   请设置 POLYGON_PRIVATE_KEY 环境变量或创建 .env 文件")
            return

    trader = CLOBTrader(wallet=wallet, mode=mode)
    signal_trader = SignalTrader(trader)

    print(f"\n{'='*60}")
    print(f"  🚀 执行交易 (模式: {mode.upper()})")
    print(f"{'='*60}")

    results = []
    for s in signals[:5]:  # 只执行前 5 个信号
        try:
            # 传递 enriched market dict（含 raw 原始数据）
            result = signal_trader.execute_ev_signal(
                market=s,
                ev_data={"score": s["ev_score"], "summary": s["ev_summary"], "flags": s["ev_flags"]},
                max_amount=10.0,
            )

            if result.get("action") == "SKIP":
                print(f"  ⏭️ 跳过: {s['question'][:50]} ({result.get('reason', '未知')})")
            elif "error" in result:
                print(f"  ⚠️ 异常: {s['question'][:40]} → {result['error']}")
            else:
                print(f"  ✅ {result.get('direction', '?')} ${result.get('amount', 0):.2f} @ {result.get('price', 0):.4f} → {s['question'][:40]}")

            results.append(result)
        except Exception as e:
            print(f"  ❌ 失败: {s['question'][:50]} → {e}")

        print()

    print(f"  执行完毕: {len(results)} 笔")


def main():
    args = sys.argv[1:]
    auto_exec = "--auto" in args
    live_mode = "--live" in args

    if live_mode:
        print("\n⚠️  ⚠️  ⚠️  真实交易模式 ⚠️  ⚠️  ⚠️")
        print("   确认已设置 POLYGON_PRIVATE_KEY 环境变量")
        print("   确认钱包中有足够的 MATIC (gas) + USDC.e (资金)")
        confirm = input("\n   输入 'YES' 确认: ")
        if confirm != "YES":
            print("   已取消")
            return

    mode = "live" if live_mode else "paper"

    print("\n🔍 正在扫描 Polymarket...")
    enriched = scan_markets(limit=200)

    total = len(enriched)
    active = [m for m in enriched if m["urgency_label"] != "已结束"]
    print(f"   扫描完成: {len(active)}/{total} 个活跃市场")

    signals = get_trade_signals(enriched, min_ev_score=3)
    print(f"   EV 信号: {len(signals)} 个")

    if not signals:
        print("\n   当前无符合条件的交易信号")
        return

    suggestions = suggest_trades(signals)

    if auto_exec or live_mode:
        execute_trades(signals, mode=mode)
    else:
        print(f"\n💡 使用 'python run.py --auto' 执行 paper 交易模拟")
        print(f"⚠️  使用 'python run.py --live' 执行真实交易 (需先配置钱包)")

    # 导出建议
    os.makedirs("output", exist_ok=True)
    with open("output/trade_signals.json", "w", encoding="utf-8") as f:
        json.dump(suggestions, f, ensure_ascii=False, indent=2)
    print(f"\n📁 交易建议已导出: output/trade_signals.json")


if __name__ == "__main__":
    main()
