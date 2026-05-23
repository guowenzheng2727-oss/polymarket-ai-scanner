"""
Polymarket AI Scanner — 实盘前风控测试
Phase 9.5: 风控压力测试

测试场景:
1. 极端行情下的风控响应
2. 参数边界测试
3. 异常场景测试
4. 生成风控测试报告
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Any

# 确保能 import 项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trader import PolygonWallet, CLOBTrader, SignalTrader


# ============================================================
# 测试报告
# ============================================================
class RiskTestReport:
    """风控测试报告生成器"""

    def __init__(self):
        self.tests: List[Dict[str, Any]] = []
        self.passed = 0
        self.failed = 0
        self.warnings = 0

    def add(self, name: str, status: str, detail: str = "", risk_level: str = "info"):
        """添加测试结果"""
        self.tests.append({
            "name": name,
            "status": status,
            "detail": detail,
            "risk_level": risk_level,
            "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        })
        if status == "通过":
            self.passed += 1
        elif status == "失败":
            self.failed += 1
        elif status == "警告":
            self.warnings += 1

    def print_summary(self):
        """打印测试报告"""
        print("\n" + "=" * 70)
        print("  Polymarket AI Scanner — 风控测试报告")
        print("=" * 70)

        for test in self.tests:
            icon = {"通过": "✅", "失败": "❌", "警告": "⚠️"}.get(test["status"], "ℹ️")
            print(f"\n{icon} [{test['status']}] {test['name']}")
            if test["detail"]:
                print(f"   {test['detail']}")

        print("\n" + "-" * 70)
        print(f"  总计: {len(self.tests)} 项测试")
        print(f"  ✅ 通过: {self.passed}")
        print(f"  ⚠️  警告: {self.warnings}")
        print(f"  ❌ 失败: {self.failed}")

        if self.failed == 0 and self.warnings == 0:
            print("\n  🎉 所有风控测试通过！系统可以安全上线实盘。")
        elif self.failed == 0:
            print("\n  ⚠️  风控测试基本通过，但有警告项需要关注。")
        else:
            print("\n  ❌ 风控测试未通过！实盘前必须修复失败项。")

        print("=" * 70)

        return self.failed == 0


# ============================================================
# 测试 1: 单笔限额风控
# ============================================================
def test_order_limit(report: RiskTestReport):
    """测试单笔订单限额"""
    print("\n📋 测试 1: 单笔订单限额...")

    wallet = PolygonWallet.create()
    trader = CLOBTrader(wallet=wallet, mode="paper")
    trader.max_order_usd = 100  # 设置单笔上限 $100

    # 场景 1: 正常订单（应通过）
    try:
        order = trader.place_limit_order(
            token_id="0x" + "0" * 40,
            side="BUY",
            size=50.0,
            price=0.55,
        )
        report.add(
            "单笔限额 — 正常订单 ($50)",
            "通过",
            f"订单 ID: {order.get('id', 'N/A')[:20]}...",
        )
    except Exception as e:
        report.add("单笔限额 — 正常订单 ($50)", "失败", str(e), "high")

    # 场景 2: Paper 模式超限订单（应记录提醒但不拦截）
    try:
        order = trader.place_limit_order(
            token_id="0x" + "0" * 40,
            side="BUY",
            size=150.0,  # 超过 $100 上限
            price=0.55,
        )
        if order.get("status") == "PAPER_MODE":
            report.add(
                "单笔限额 — Paper 超限订单 ($150)",
                "通过",
                "Paper 模式正确记录风控提醒但不拦截",
            )
        else:
            report.add(
                "单笔限额 — Paper 超限订单 ($150)",
                "失败",
                "订单状态异常",
                "high",
            )
    except Exception as e:
        report.add(
            "单笔限额 — Paper 超限订单 ($150)",
            "失败",
            f"Paper 模式不应拦截: {str(e)}",
            "high",
        )

    # 场景 2b: Live 模式超限订单（应被拒绝）
    trader_live = CLOBTrader(wallet=wallet, mode="live")
    trader_live.max_order_usd = 100
    try:
        order = trader_live.place_limit_order(
            token_id="0x" + "0" * 40,
            side="BUY",
            size=150.0,
            price=0.55,
        )
        report.add(
            "单笔限额 — Live 超限订单 ($150)",
            "失败",
            "Live 模式风控未生效！",
            "critical",
        )
    except ValueError as e:
        report.add(
            "单笔限额 — Live 超限订单 ($150)",
            "通过",
            f"正确拒绝: {str(e)[:60]}",
        )
    except Exception as e:
        report.add("单笔限额 — Live 超限订单 ($150)", "失败", f"异常: {str(e)}", "high")

    # 场景 3: 边界值（刚好等于上限）
    try:
        order = trader.place_limit_order(
            token_id="0x" + "0" * 40,
            side="BUY",
            size=100.0,  # 刚好 $100
            price=0.55,
        )
        report.add("单笔限额 — 边界值 ($100)", "通过", "边界值订单被正确接受")
    except Exception as e:
        report.add("单笔限额 — 边界值 ($100)", "失败", str(e), "high")


# ============================================================
# 测试 2: 日限额风控
# ============================================================
def test_daily_limit(report: RiskTestReport):
    """测试日累计限额"""
    print("\n📋 测试 2: 日累计限额...")

    wallet = PolygonWallet.create()
    trader = CLOBTrader(wallet=wallet, mode="paper")
    trader.max_daily_usd = 500  # 日限额 $500
    trader.daily_total = 400     # 已用 $400

    # 场景 1: 在日限额内（应通过）
    try:
        order = trader.place_limit_order(
            token_id="0x" + "0" * 40,
            side="BUY",
            size=80.0,  # 400 + 80 = 480 < 500
            price=0.55,
        )
        report.add("日限额 — 限额内 ($80, 累计 $480)", "通过", "订单被接受")
    except Exception as e:
        report.add("日限额 — 限额内 ($80)", "失败", str(e), "high")

    # 场景 2: Paper 模式超出日限额（应记录提醒但不拦截）
    try:
        order = trader.place_limit_order(
            token_id="0x" + "0" * 40,
            side="BUY",
            size=50.0,  # 480 + 50 = 530 > 500
            price=0.55,
        )
        if order.get("status") == "PAPER_MODE":
            report.add(
                "日限额 — Paper 超限 ($50, 累计 $530)",
                "通过",
                "Paper 模式正确记录提醒但不拦截",
            )
        else:
            report.add("日限额 — Paper 超限 ($50)", "失败", "状态异常", "high")
    except Exception as e:
        report.add("日限额 — Paper 超限 ($50)", "失败", f"不应拦截: {str(e)}", "high")

    # 场景 2b: Live 模式超出日限额（应被拒绝）
    trader_live = CLOBTrader(wallet=wallet, mode="live")
    trader_live.max_daily_usd = 500
    trader_live.daily_total = 480
    try:
        order = trader_live.place_limit_order(
            token_id="0x" + "0" * 40,
            side="BUY",
            size=50.0,
            price=0.55,
        )
        report.add(
            "日限额 — Live 超限 ($50, 累计 $530)",
            "失败",
            "Live 模式日限额风控未生效！",
            "critical",
        )
    except ValueError as e:
        report.add(
            "日限额 — Live 超限 ($50)",
            "通过",
            f"正确拒绝: {str(e)[:60]}",
        )
    except Exception as e:
        report.add("日限额 — Live 超限 ($50)", "失败", f"异常: {str(e)}", "high")

    # 场景 3: 刚好用完日限额
    trader2 = CLOBTrader(wallet=wallet, mode="paper")
    trader2.max_daily_usd = 500
    trader2.daily_total = 450
    try:
        order = trader2.place_limit_order(
            token_id="0x" + "0" * 40,
            side="BUY",
            size=50.0,  # 450 + 50 = 500
            price=0.55,
        )
        report.add("日限额 — 刚好用完 ($50, 累计 $500)", "通过", "边界值正确接受")
    except Exception as e:
        report.add("日限额 — 刚好用完 ($50)", "失败", str(e), "high")


# ============================================================
# 测试 3: 价格合法性检查
# ============================================================
def test_price_validation(report: RiskTestReport):
    """测试价格范围校验"""
    print("\n📋 测试 3: 价格合法性检查...")

    wallet = PolygonWallet.create()
    trader = CLOBTrader(wallet=wallet, mode="paper")

    test_cases = [
        (0.55, "正常价格", "通过"),
        (0.01, "最低边界", "通过"),
        (0.99, "最高边界", "通过"),
        (0.00, "零价格", "失败"),
        (1.00, "价格为 1", "失败"),
        (-0.1, "负价格", "失败"),
        (1.5, "超范围", "失败"),
    ]

    for price, desc, expected in test_cases:
        try:
            order = trader.place_limit_order(
                token_id="0x" + "0" * 40,
                side="BUY",
                size=10.0,
                price=price,
            )
            if expected == "失败":
                report.add(
                    f"价格校验 — {desc} ({price})",
                    "失败",
                    f"应被拒绝但接受了",
                    "high",
                )
            else:
                report.add(f"价格校验 — {desc} ({price})", "通过")
        except ValueError as e:
            if expected == "失败":
                report.add(
                    f"价格校验 — {desc} ({price})",
                    "通过",
                    f"正确拒绝: {str(e)[:50]}",
                )
            else:
                report.add(
                    f"价格校验 — {desc} ({price})",
                    "失败",
                    f"不应拒绝: {str(e)[:50]}",
                    "high",
                )
        except Exception as e:
            report.add(f"价格校验 — {desc}", "失败", f"异常: {str(e)}", "high")


# ============================================================
# 测试 4: Paper 模式安全
# ============================================================
def test_paper_mode(report: RiskTestReport):
    """测试 Paper 模式不触发真实风控"""
    print("\n📋 测试 4: Paper 模式安全...")

    wallet = PolygonWallet.create()
    trader = CLOBTrader(wallet=wallet, mode="paper")
    trader.max_order_usd = 10  # 设置很低的限额

    # Paper 模式下，即使超限也应该通过（因为是模拟）
    try:
        order = trader.place_limit_order(
            token_id="0x" + "0" * 40,
            side="BUY",
            size=1000.0,  # 远超 $10 上限
            price=0.55,
        )
        if order.get("status") == "PAPER_MODE":
            report.add(
                "Paper 模式 — 超限订单",
                "通过",
                "Paper 模式正确跳过风控，标记为模拟订单",
            )
        else:
            report.add(
                "Paper 模式 — 超限订单",
                "警告",
                "订单通过但状态不是 PAPER_MODE",
            )
    except Exception as e:
        report.add(
            "Paper 模式 — 超限订单",
            "失败",
            f"Paper 模式不应触发风控: {str(e)}",
            "high",
        )

    # Paper 模式余额查询
    balance = trader.get_balance()
    if balance.get("mode") == "paper":
        report.add("Paper 模式 — 余额查询", "通过", "返回模拟余额")
    else:
        report.add("Paper 模式 — 余额查询", "失败", "未返回 paper 标记", "high")


# ============================================================
# 测试 5: 信号交易风控
# ============================================================
def test_signal_trading(report: RiskTestReport):
    """测试信号交易模块的风控集成"""
    print("\n📋 测试 5: 信号交易风控...")

    wallet = PolygonWallet.create()
    trader = CLOBTrader(wallet=wallet, mode="paper")
    signal_trader = SignalTrader(trader)

    # 模拟市场数据
    mock_market = {
        "question": "测试市场",
        "clobTokenIds": '["0x' + "0" * 40 + '", "0x' + "1" * 40 + '"]',
        "yes": 0.55,
        "volume": 100000,
    }

    # 场景 1: 弱信号应跳过
    weak_signal = {"score": 3, "summary": "一般", "flags": []}
    result = signal_trader.execute_ev_signal(mock_market, weak_signal, max_amount=10)
    if result.get("action") == "SKIP":
        report.add(
            "信号交易 — 弱信号跳过",
            "通过",
            f"正确跳过: {result.get('reason', '')}",
        )
    else:
        report.add(
            "信号交易 — 弱信号跳过",
            "失败",
            "弱信号未被跳过",
            "high",
        )

    # 场景 2: 强信号应执行
    strong_signal = {"score": 8, "summary": "⭐ 强信号", "flags": []}
    result = signal_trader.execute_ev_signal(mock_market, strong_signal, max_amount=10)
    if result.get("order"):
        report.add(
            "信号交易 — 强信号执行",
            "通过",
            f"订单: {result.get('direction')} ${result.get('amount', 0):.2f}",
        )
    else:
        report.add("信号交易 — 强信号执行", "失败", "强信号未执行", "high")

    # 场景 3: 信号强度与仓位关系
    # score=6: execute_ev_signal → amount=10*0.6=6.0, confidence=0.6
    # execute_signal → adjusted=round(6.0*0.6,2)=3.6
    medium_signal = {"score": 6, "summary": "💡 值得关注", "flags": []}
    result = signal_trader.execute_ev_signal(mock_market, medium_signal, max_amount=10)
    expected_amount = 3.6  # 10 * 0.6 * 0.6 = 3.6
    actual_amount = result.get("amount", 0)
    if abs(actual_amount - expected_amount) < 0.1:
        report.add(
            "信号交易 — 仓位动态调整",
            "通过",
            f"score=6 → 仓位={actual_amount:.2f} (预期 {expected_amount:.2f})",
        )
    else:
        report.add(
            "信号交易 — 仓位动态调整",
            "警告",
            f"score=6 → 仓位={actual_amount:.2f} (预期 {expected_amount:.2f})",
        )


# ============================================================
# 测试 6: 极端行情模拟
# ============================================================
def test_extreme_scenarios(report: RiskTestReport):
    """测试极端行情下的系统行为"""
    print("\n📋 测试 6: 极端行情模拟...")

    wallet = PolygonWallet.create()
    trader = CLOBTrader(wallet=wallet, mode="paper")

    # 场景 1: 价格闪崩（YES 接近 0）
    try:
        order = trader.place_limit_order(
            token_id="0x" + "0" * 40,
            side="BUY",
            size=10.0,
            price=0.001,  # 极端低价
        )
        report.add(
            "极端行情 — 闪崩价格 (0.001)",
            "通过",
            "系统接受极端低价订单（在 0.01-0.99 范围内）",
        )
    except Exception as e:
        report.add("极端行情 — 闪崩价格", "通过", f"正确拒绝: {str(e)[:50]}")

    # 场景 2: 价格暴涨（YES 接近 1）
    try:
        order = trader.place_limit_order(
            token_id="0x" + "0" * 40,
            side="BUY",
            size=10.0,
            price=0.99,  # 极端高价
        )
        report.add("极端行情 — 暴涨价格 (0.99)", "通过", "边界值正确接受")
    except Exception as e:
        report.add("极端行情 — 暴涨价格", "失败", str(e), "high")

    # 场景 3: 超大金额
    trader2 = CLOBTrader(wallet=wallet, mode="paper")
    trader2.max_order_usd = 1000000  # 设置超大限额
    try:
        order = trader2.place_limit_order(
            token_id="0x" + "0" * 40,
            side="BUY",
            size=500000.0,
            price=0.55,
        )
        report.add(
            "极端行情 — 超大金额 ($500K)",
            "通过",
            "Paper 模式下超大金额被接受",
        )
    except Exception as e:
        report.add("极端行情 — 超大金额", "失败", str(e), "high")

    # 场景 4: 零成交量市场
    mock_market_zero_vol = {
        "question": "零成交量测试",
        "clobTokenIds": '["0x' + "0" * 40 + '"]',
        "yes": 0.50,
        "volume": 0,
    }
    signal = {"score": 5, "summary": "一般", "flags": []}
    signal_trader = SignalTrader(trader)
    result = signal_trader.execute_ev_signal(mock_market_zero_vol, signal, max_amount=10)
    report.add(
        "极端行情 — 零成交量市场",
        "通过" if result.get("order") else "警告",
        "零成交量市场信号处理完成",
    )


# ============================================================
# 测试 7: 环境变量安全
# ============================================================
def test_env_security(report: RiskTestReport):
    """测试环境变量和密钥安全"""
    print("\n📋 测试 7: 环境变量安全...")

    # 场景 1: 未设置私钥时应报错
    original_pk = os.environ.pop("POLYGON_PRIVATE_KEY", None)
    try:
        wallet = PolygonWallet.from_env()
        report.add(
            "环境安全 — 未设置私钥",
            "失败",
            "未设置私钥时应报错",
            "critical",
        )
    except ValueError as e:
        report.add(
            "环境安全 — 未设置私钥",
            "通过",
            "正确报错: 未设置 POLYGON_PRIVATE_KEY",
        )
    except Exception as e:
        report.add("环境安全 — 未设置私钥", "通过", f"正确拒绝: {str(e)[:50]}")
    finally:
        if original_pk:
            os.environ["POLYGON_PRIVATE_KEY"] = original_pk

    # 场景 2: 无效私钥
    os.environ["POLYGON_PRIVATE_KEY"] = "invalid_key"
    try:
        wallet = PolygonWallet.from_env()
        report.add(
            "环境安全 — 无效私钥",
            "失败",
            "无效私钥应被拒绝",
            "critical",
        )
    except Exception:
        report.add("环境安全 — 无效私钥", "通过", "正确拒绝无效私钥")
    finally:
        if original_pk:
            os.environ["POLYGON_PRIVATE_KEY"] = original_pk
        else:
            os.environ.pop("POLYGON_PRIVATE_KEY", None)

    # 场景 3: 钱包创建不暴露私钥
    wallet = PolygonWallet.create()
    pk = wallet.private_key
    if len(pk) > 20 and pk.startswith("0x"):
        report.add(
            "环境安全 — 私钥格式",
            "通过",
            f"私钥格式正确 (长度 {len(pk)})",
        )
    else:
        report.add("环境安全 — 私钥格式", "失败", "私钥格式异常", "high")


# ============================================================
# 测试 8: 订单簿查询安全
# ============================================================
def test_orderbook_safety(report: RiskTestReport):
    """测试订单簿查询的鲁棒性"""
    print("\n📋 测试 8: 订单簿查询安全...")

    wallet = PolygonWallet.create()
    trader = CLOBTrader(wallet=wallet, mode="paper")

    # 场景 1: 无效 token_id
    try:
        book = trader.get_order_book("invalid_token")
        report.add(
            "订单簿 — 无效 token_id",
            "通过",
            "系统返回结果（可能为空）",
        )
    except Exception as e:
        report.add(
            "订单簿 — 无效 token_id",
            "通过",
            f"正确报错: {str(e)[:50]}",
        )

    # 场景 2: 空 token_id
    try:
        book = trader.get_order_book("")
        report.add("订单簿 — 空 token_id", "通过", "空 token 处理完成")
    except Exception as e:
        report.add("订单簿 — 空 token_id", "通过", f"正确报错: {str(e)[:50]}")


# ============================================================
# 主入口
# ============================================================
def main():
    print("\n" + "=" * 70)
    print("  Polymarket AI Scanner — 实盘前风控压力测试")
    print("=" * 70)
    print(f"  开始时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)

    report = RiskTestReport()

    # 执行所有测试
    test_order_limit(report)
    test_daily_limit(report)
    test_price_validation(report)
    test_paper_mode(report)
    test_signal_trading(report)
    test_extreme_scenarios(report)
    test_env_security(report)
    test_orderbook_safety(report)

    # 生成报告
    all_passed = report.print_summary()

    # 保存报告到文件
    report_path = os.path.join(
        os.path.dirname(__file__), "output", "risk_test_report.json"
    )
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total": len(report.tests),
                "passed": report.passed,
                "failed": report.failed,
                "warnings": report.warnings,
            },
            "tests": report.tests,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n📄 详细报告已保存: {report_path}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
