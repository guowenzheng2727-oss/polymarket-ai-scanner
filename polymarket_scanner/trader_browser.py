"""
Polymarket Browser Trader — Playwright 网页自动化下单
替代 CLOB API 方案，通过操控 Polymarket 网页完成交易

用法:
    # 启动持久化 Chrome 窗口（默认 — 自动管理，无需手动启动 Chrome）
    python trader_browser.py --url "https://polymarket.com/event/xxx" --side yes --amount 5

    # 连接已有 Chrome（需先手动启动 --remote-debugging-port=9222）
    python trader_browser.py --url "..." --side no --amount 10 --connect

前置条件:
    默认模式: 无需任何前置操作，自动启动独立 Chrome 窗口
    连接模式: Chrome 需以远程调试模式启动
        chrome.exe --remote-debugging-port=9222
"""

import asyncio
import sys
import os
import json
import argparse
from datetime import datetime
from typing import Optional

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page
except ImportError:
    print("❌ 需要 playwright: pip install playwright && playwright install chromium")
    sys.exit(1)


# ============================================================
# 配置
# ============================================================
CDP_PORT = 9222
POLYMARKET_URL = "https://polymarket.com"
DEFAULT_TIMEOUT = 30000  # 30s

# 交易专用 Chrome 配置目录 — 持久化 MetaMask / 登录状态
# 默认使用系统 Chrome 用户数据（已有 MetaMask + 登录态）
# 如需隔离，可改为 ~/.polymarket_trader_chrome
TRADE_PROFILE_DIR = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "Google", "Chrome", "User Data"
)


# ============================================================
# 浏览器管理
# ============================================================
class BrowserManager:
    """管理 Playwright 浏览器连接

    三种模式（按优先级）:
    1. 连接已有 Chrome CDP (localhost:9222)
    2. 重用持久化交易窗口 (persistent context, 之前启动过的)
    3. 创建新的持久化窗口 (首次使用)
    """

    def __init__(self, connect_existing: bool = False):
        # 默认改为 False — 直接用持久化窗口，不依赖 CDP
        self.connect_existing = connect_existing
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._own_process = False  # 是否自己启动了浏览器进程

    async def start(self):
        self._playwright = await async_playwright().start()

        # 策略 1 (可选): 连接已有 Chrome CDP（用户手动启动的调试模式）
        # 默认跳过 — CDP 在 Windows 上经常绑定失败
        if self.connect_existing:
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    f"http://localhost:{CDP_PORT}"
                )
                contexts = self._browser.contexts
                self._context = contexts[0] if contexts else await self._browser.new_context()
                print(f"✅ 已连接现有 Chrome (CDP 端口 {CDP_PORT})")
                return self._browser, self._context
            except Exception as e:
                print(f"⚠️  CDP 连接失败 ({e})，回退到持久化窗口...")

        # 策略 2+3: 用持久化上下文启动专用交易 Chrome
        #     - 首次: 创建新窗口 + 空配置 → 用户登录 MetaMask
        #     - 后续: 重用同一配置 → MetaMask 已登录，直接可用
        os.makedirs(TRADE_PROFILE_DIR, exist_ok=True)

        print(f"🟢 启动交易专用 Chrome 窗口...")
        print(f"   配置目录: {TRADE_PROFILE_DIR}")
        print(f"   首次使用需在此窗口登录 MetaMask + Polymarket")
        print(f"   后续自动重用，无需重复登录\n")

        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=TRADE_PROFILE_DIR,
            channel="chrome",
            headless=False,
            args=[
                f"--remote-debugging-port={CDP_PORT}",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            # 不设置 viewport → 使用窗口实际大小
            no_viewport=True,
        )
        self._own_process = True

        # 通过 CDP 连接获取 browser 对象（用于管理）
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(
                f"http://localhost:{CDP_PORT}"
            )
            print(f"✅ 交易窗口已就绪 (CDP 端口 {CDP_PORT})")
        except Exception:
            self._browser = None  # persistent context 不需要 browser 对象

        return self._browser, self._context

    async def stop(self):
        # 不关闭 Chrome 窗口 — 保留给下次交易复用
        # 只清理 Python 侧引用，Chrome 进程独立存活
        self._browser = None
        self._context = None
        self._playwright = None


# ============================================================
# Polymarket 页面操作
# ============================================================
class PolymarketTrader:
    """Polymarket 网页自动化交易"""

    def __init__(self, page: Page):
        self.page = page

    async def navigate_to_market(self, market_slug_or_url: str):
        """导航到指定市场页面"""
        if market_slug_or_url.startswith("http"):
            url = market_slug_or_url
        elif market_slug_or_url.startswith("polymarket.com"):
            url = f"https://{market_slug_or_url}"
        else:
            # 假设是 slug
            url = f"https://polymarket.com/event/{market_slug_or_url}"

        print(f"🌐 正在打开: {url}")
        await self.page.goto(url, wait_until="domcontentloaded", timeout=DEFAULT_TIMEOUT)

        # 等待页面加载完成（市场标题出现）
        try:
            await self.page.wait_for_selector("h1", timeout=15000)
            title = await self.page.title()
            print(f"📋 页面标题: {title}")
        except Exception:
            print("⚠️  页面加载超时，继续尝试...")

        return url

    async def wait_for_trading_ready(self, timeout: int = 30000):
        """等待交易界面加载完成"""
        # Polymarket 交易界面核心元素
        selectors = [
            "button:has-text('Buy')",
            "button:has-text('BUY')",
            "[data-testid*='trade']",
            "[data-testid*='outcome']",
        ]
        for sel in selectors:
            try:
                await self.page.wait_for_selector(sel, timeout=5000)
                print(f"✅ 交易界面就绪: {sel}")
                return True
            except Exception:
                continue

        # 宽松模式：等 3 秒让 JS 渲染
        print("⏳ 等待页面 JS 渲染...")
        await asyncio.sleep(3)
        return True

    async def click_buy_button(self, side: str = "yes"):
        """点击 BUY YES 或 BUY NO 按钮"""
        side_upper = side.upper()

        # Polymarket 页面上的购买按钮有多种可能的定位方式
        candidates = [
            f"button:has-text('Buy {side_upper}')",
            f"button:has-text('BUY {side_upper}')",
            f"button:has-text('{side_upper}') >> .. >> button:has-text('Buy')",
            f"[data-testid*='{side.lower()}-buy']",
            f"button >> text=Buy",
        ]

        for selector in candidates:
            try:
                btn = await self.page.wait_for_selector(selector, timeout=3000)
                if btn:
                    text = await btn.inner_text()
                    print(f"🔘 找到按钮: '{text}' → 点击")
                    await btn.click()
                    await asyncio.sleep(1)
                    return True
            except Exception:
                continue

        print(f"❌ 未找到 '{side_upper}' 购买按钮")
        print("   请手动在浏览器中点击购买按钮，然后按 Enter 继续...")
        input()
        return True

    async def fill_amount(self, amount: float):
        """填写交易金额"""
        # 金额输入框
        input_selectors = [
            "input[type='number']",
            "input[placeholder*='Amount']",
            "input[placeholder*='amount']",
            "input[placeholder*='Enter']",
            "[data-testid*='amount'] input",
            "[data-testid*='trade'] input[type='text']",
        ]

        for selector in input_selectors:
            try:
                inp = await self.page.wait_for_selector(selector, timeout=3000)
                if inp:
                    await inp.click()
                    await inp.fill("")  # 清空
                    await inp.fill(str(amount))
                    print(f"💰 已填入金额: ${amount}")
                    await asyncio.sleep(0.5)
                    return True
            except Exception:
                continue

        print(f"⚠️  未自动找到金额输入框，请手动输入 ${amount}")
        return False

    async def click_confirm(self):
        """点击确认/下单按钮"""
        confirm_selectors = [
            "button:has-text('Confirm')",
            "button:has-text('Place Order')",
            "button:has-text('Submit')",
            "button:has-text('Buy')",
            "button:has-text('Trade')",
            "[data-testid*='confirm']",
            "[data-testid*='submit']",
        ]

        for selector in confirm_selectors:
            try:
                btn = await self.page.wait_for_selector(selector, timeout=3000)
                if btn:
                    text = await btn.inner_text()
                    print(f"🔘 找到确认按钮: '{text}'")
                    # 不自动点击 — 用户需要先在 MetaMask 确认
                    return True
            except Exception:
                continue

        return False

    async def take_screenshot(self, name: str = "polymarket"):
        """截图保存"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{name}_{timestamp}.png"
        await self.page.screenshot(path=filename, full_page=True)
        print(f"📸 截图已保存: {filename}")
        return filename

    async def get_market_info(self) -> dict:
        """获取当前页面市场信息"""
        info = {"title": "", "yes_price": None, "no_price": None, "volume": None}

        try:
            info["title"] = await self.page.title()
        except Exception:
            pass

        # 尝试获取价格（从页面文本中提取）
        try:
            page_text = await self.page.inner_text("body")
            # 简单搜索价格模式
            import re
            yes_match = re.search(r'(?:Yes|YES)[^\d]*\$?(\d+\.\d+)', page_text)
            no_match = re.search(r'(?:No|NO)[^\d]*\$?(\d+\.\d+)', page_text)
            if yes_match:
                info["yes_price"] = float(yes_match.group(1))
            if no_match:
                info["no_price"] = float(no_match.group(1))
        except Exception:
            pass

        return info


# ============================================================
# 高级 — 通过 URL 参数直达下单
# ============================================================
def build_trade_url(market_slug: str, side: str = "yes", amount: float = None) -> str:
    """
    尝试构建直达交易界面的 URL
    Polymarket 的交易通常需要在页面内交互，这里构建最优 URL
    """
    url = f"https://polymarket.com/event/{market_slug}"
    # Polymarket 不支持 URL 参数预填金额，需要 Playwright 操作
    return url


# ============================================================
# 主流程
# ============================================================
async def execute_trade(
    market_url: str,
    side: str = "yes",
    amount: float = 5.0,
    connect_existing: bool = True,
    dry_run: bool = False,
):
    """
    执行一笔 Polymarket 交易

    Args:
        market_url: 市场 URL 或 slug
        side: 'yes' 或 'no'
        amount: 交易金额 (USD)
        connect_existing: True=连接现有Chrome, False=启动新浏览器
        dry_run: True=只导航不点击
    """
    print(f"\n{'='*60}")
    print(f"  🎯 Polymarket Browser Trader")
    print(f"  市场: {market_url}")
    print(f"  方向: {side.upper()}")
    print(f"  金额: ${amount}")
    print(f"  模式: {'Dry Run (仅预览)' if dry_run else '实盘'}")
    print(f"{'='*60}\n")

    manager = BrowserManager(connect_existing=connect_existing)
    trader = None

    try:
        browser, context = await manager.start()
        page = await context.new_page()
        trader = PolymarketTrader(page)

        # 1. 导航到市场
        await trader.navigate_to_market(market_url)
        await asyncio.sleep(2)

        # 2. 等待交易界面
        await trader.wait_for_trading_ready()

        # 3. 截图（操作前）
        await trader.take_screenshot("before_trade")

        if not dry_run:
            # 4. 点击买入按钮
            print(f"\n🔘 点击 BUY {side.upper()}...")
            await trader.click_buy_button(side)

            # 5. 填写金额
            print(f"\n💰 填入金额 ${amount}...")
            await trader.fill_amount(amount)

            # 6. 截图（操作后）
            await trader.take_screenshot("after_input")

            # 7. 提示用户
            print(f"\n{'='*60}")
            print(f"  ⚠️  请在浏览器中完成以下操作:")
            print(f"  1. 确认交易详情")
            print(f"  2. MetaMask 弹窗中点击「确认/签名」")
            print(f"  3. 等待交易确认")
            print(f"{'='*60}")

            # 等待一定时间让用户操作
            print(f"\n⏳ 等待 30 秒供你确认...")
            await asyncio.sleep(30)

            # 8. 最终截图
            await trader.take_screenshot("after_trade")

        # 打印市场信息
        info = await trader.get_market_info()
        print(f"\n📊 市场信息: {json.dumps(info, indent=2, ensure_ascii=False)}")

        print(f"\n✅ 交易流程完成!")

        return {
            "success": True,
            "market_url": market_url,
            "side": side,
            "amount": amount,
            "market_info": info,
            "dry_run": dry_run,
        }

    except Exception as e:
        print(f"\n❌ 交易失败: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}

    finally:
        if not connect_existing:
            await manager.stop()
        # 如果是连接模式，保持浏览器打开


# ============================================================
# CLI 入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Polymarket Browser Trader")
    parser.add_argument("--url", required=True, help="市场 URL 或 slug")
    parser.add_argument("--side", default="yes", choices=["yes", "no"], help="买入方向")
    parser.add_argument("--amount", type=float, default=5.0, help="交易金额 (USD)")
    parser.add_argument("--launch", action="store_true", default=True, help="启动持久化 Chrome 窗口 (默认)")
    parser.add_argument("--connect", action="store_true", help="连接已有 Chrome (需先启动 --remote-debugging-port=9222)")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际点击")
    parser.add_argument("--screenshot-only", action="store_true", help="仅截图市场页面")

    args = parser.parse_args()

    if args.screenshot_only:
        # 仅截图模式
        async def screenshot_mode():
            manager = BrowserManager(connect_existing=args.connect)
            try:
                _, ctx = await manager.start()
                page = await ctx.new_page()
                trader = PolymarketTrader(page)
                await trader.navigate_to_market(args.url)
                await asyncio.sleep(3)
                await trader.take_screenshot()
                info = await trader.get_market_info()
                print(json.dumps(info, indent=2, ensure_ascii=False))
            finally:
                await manager.stop()

        asyncio.run(screenshot_mode())
    else:
        asyncio.run(execute_trade(
            market_url=args.url,
            side=args.side,
            amount=args.amount,
            connect_existing=args.connect,
            dry_run=args.dry_run,
        ))


if __name__ == "__main__":
    main()
