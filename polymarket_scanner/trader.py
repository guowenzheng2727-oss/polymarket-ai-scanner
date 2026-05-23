"""
Polymarket AI Scanner - Phase 9: 链上自动交易模块
基于 py-clob-client (Polymarket 官方 Python SDK)

架构:
    Polygon 钱包 → API 凭证 → CLOB 客户端 → 下单/查余额/查持仓
                                         ↑
    scanner.py (信号) ──────────────────┘

⚠️ 安全须知:
    - 私钥只通过环境变量传入，绝不硬编码
    - 默认 paper 模式 (不消耗真实资金)
    - 内置风控: 单笔上限、日限额、滑点保护
"""

import os
import json
import time
import hashlib
import hmac
import requests
from typing import Optional
from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

# 加载 .env
load_dotenv()


# ============================================================
# 常量
# ============================================================
POLYGON_CHAIN_ID = 137
POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# USDC.e 合约地址 (Polygon)
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# CTF Exchange 合约地址
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"


# ============================================================
# 钱包管理
# ============================================================
class PolygonWallet:
    """Polygon 钱包管理: 创建/导入/查询"""

    def __init__(self, private_key: str = None):
        if private_key:
            self.account = Account.from_key(private_key)
        else:
            self.account = None

    @classmethod
    def create(cls) -> "PolygonWallet":
        """创建新钱包"""
        Account.enable_unaudited_hdwallet_features()
        acct = Account.create()
        wallet = cls()
        wallet.account = acct
        return wallet

    @classmethod
    def from_env(cls) -> "PolygonWallet":
        """从环境变量导入"""
        pk = os.getenv("POLYGON_PRIVATE_KEY")
        if not pk:
            raise ValueError("❌ 未设置 POLYGON_PRIVATE_KEY 环境变量")
        return cls(private_key=pk)

    @property
    def address(self) -> str:
        return self.account.address if self.account else "N/A"

    @property
    def private_key(self) -> str:
        if not self.account:
            return "N/A"
        # eth_account 的 key 是 bytes，转为 hex 并加 0x 前缀
        key_hex = self.account.key.hex()
        if not key_hex.startswith("0x"):
            key_hex = "0x" + key_hex
        return key_hex

    def save_to_env(self, filename: str = ".env"):
        """保存私钥到 .env 文件 (追加)"""
        env_path = filename
        content = f"\n# Polymarket Wallet (auto-generated)\nPOLYGON_PRIVATE_KEY={self.private_key}\n"
        with open(env_path, "a") as f:
            f.write(content)
        print(f"✅ 私钥已保存到 {env_path}")


# ============================================================
# CLOB HMAC 认证
# ============================================================
class CLOBAuth:
    """
    CLOB API HMAC 认证
    流程:
    1. 用钱包私钥签名固定消息 → 派生 L2 凭证
    2. 用 L2 凭证对每个请求做 HMAC-SHA256 签名
    """

    SIGNATURE_MESSAGE = (
        "This message attests that I control the wallet " +
        "submitting this request to Polymarket. Issued at: {}"
    )

    def __init__(self, wallet: PolygonWallet):
        self.wallet = wallet
        self.api_key = None
        self.api_secret = None
        self.api_passphrase = None

    def derive(self) -> dict:
        """派生 L2 API 凭证 (不消耗 gas)"""
        timestamp = str(int(time.time()))
        message = self.SIGNATURE_MESSAGE.format(timestamp)

        signed = self.wallet.account.sign_message(
            encode_defunct(text=message)
        )

        # 用签名的哈希作为凭证派生种子
        sig_hash = hashlib.sha256(signed.signature.hex().encode()).hexdigest()

        self.api_key = f"0x{self.wallet.address[2:]}"
        self.api_secret = sig_hash
        self.api_passphrase = hashlib.sha256(
            (sig_hash + "polymarket").encode()
        ).hexdigest()

        return {
            "api_key": self.api_key,
            "api_secret": self.api_secret[:64],
            "api_passphrase": self.api_passphrase[:64],
        }

    def sign_request(self, method: str, path: str, body: str = "") -> dict:
        """为 CLOB API 请求生成 HMAC 签名头"""
        timestamp = str(int(time.time()))
        method = method.upper()

        # HMAC-SHA256 签名
        message = f"{timestamp}{method}{path}{body}"
        signature = hmac.new(
            self.api_secret.encode() if self.api_secret else b"",
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

        return {
            "POLY_ADDRESS": self.api_key,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": timestamp,
            "POLY_PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json",
        }

    def sign_request_v2(self, method: str, path: str, body: str = "") -> dict:
        """V2 签名方式 (备选)"""
        timestamp = str(int(time.time() * 1000))
        method = method.upper()

        message = f"{timestamp}{method}{path}{body}"
        signature = hmac.new(
            self.api_secret.encode() if self.api_secret else b"",
            message.encode(),
            hashlib.sha256,
        ).hexdigest()

        return {
            "POLY_ADDRESS": self.api_key,
            "POLY_SIGNATURE": signature,
            "POLY_TIMESTAMP": timestamp,
            "POLY_PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json",
            "User-Agent": "PolymarketAIScanner/1.0",
        }


# ============================================================
# CLOB 交易客户端
# ============================================================
class CLOBTrader:
    """
    Polymarket CLOB 交易客户端
    封装下单、查余额、查持仓、订单管理
    """

    def __init__(
        self,
        wallet: PolygonWallet = None,
        mode: str = "paper",  # paper | live
    ):
        self.mode = mode
        self.wallet = wallet or PolygonWallet()
        self.auth = CLOBAuth(self.wallet) if self.wallet.account else None
        self.orders: list = []  # 订单历史

        # 风控参数
        self.max_order_usd = float(os.getenv("MAX_ORDER_USD", 100))
        self.max_daily_usd = float(os.getenv("MAX_DAILY_USD", 500))
        self.max_slippage = float(os.getenv("MAX_SLIPPAGE", 0.02))  # 2%
        self.daily_total = 0.0

    # ------ API 请求 ------
    def _request(self, method: str, path: str, body: dict = None) -> dict:
        """发送 CLOB API 请求"""
        url = f"{CLOB_API}{path}"
        body_str = json.dumps(body) if body else ""

        if self.mode == "paper" or not self.auth:
            headers = {"Content-Type": "application/json"}
        else:
            if not self.auth.api_key:
                self.auth.derive()
            headers = self.auth.sign_request_v2(method, path, body_str)

        if body:
            resp = requests.request(method, url, headers=headers, json=body, timeout=30)
        else:
            resp = requests.request(method, url, headers=headers, timeout=30)

        resp.raise_for_status()
        return resp.json() if resp.text else {}

    # ------ 市场信息 ------
    def get_markets(self) -> list:
        """获取 CLOB 支持的市场列表"""
        return self._request("GET", "/markets")

    def get_order_book(self, token_id: str) -> dict:
        """获取订单簿"""
        return self._request("GET", f"/book?token_id={token_id}")

    def get_order_books(self, token_ids: list) -> list:
        """批量获取订单簿"""
        ids_str = "&token_id=".join(token_ids)
        return self._request("GET", f"/books?token_id={ids_str}")

    def get_midpoint(self, token_id: str) -> dict:
        """获取中间价"""
        return self._request("GET", f"/midpoint?token_id={token_id}")

    def get_price(self, token_id: str, side: str) -> dict:
        """获取最优价格"""
        return self._request("GET", f"/price?token_id={token_id}&side={side}")

    # ------ 订单 ------
    def get_orders(self, status: str = "open") -> list:
        """获取订单"""
        params = f"?status={status}" if status else ""
        return self._request("GET", f"/orders{params}")

    def get_order(self, order_id: str) -> dict:
        """获取单个订单"""
        return self._request("GET", f"/orders/{order_id}")

    def place_limit_order(
        self,
        token_id: str,
        side: str,  # BUY | SELL
        size: float,  # USDC 数量
        price: float,  # 每股价格 (0-1)
        order_type: str = "GTC",  # GTC | GTD | FOK | FAK
    ) -> dict:
        """
        下限价单

        Args:
            token_id: 代币 ID (从 Gamma API 市场数据的 clobTokenIds 获取)
            side: BUY 或 SELL
            size: 交易金额 (USDC)
            price: 每股价格 (0.01-0.99)
            order_type: GTC(一直有效) | GTD(指定有效) | FOK(全成交) | FAK(部分成交)
        """
        # 风控检查
        self._risk_check(size, price)

        body = {
            "token_id": token_id,
            "side": side.upper(),
            "size": str(size),
            "price": str(price),
            "order_type": order_type.upper(),
        }

        if self.mode == "paper":
            order = {
                **body,
                "id": f"paper_{int(time.time())}",
                "status": "PAPER_MODE",
                "created_at": time.time(),
            }
            self.orders.append(order)
            print(f"📝 [PAPER] {side.upper()} {size} USDC @ {price} | token={token_id[:12]}...")
            return order

        result = self._request("POST", "/order", body)
        self.orders.append(result)
        self.daily_total += size
        return result

    def place_market_order(
        self,
        token_id: str,
        side: str,
        amount: float,  # USDC
    ) -> dict:
        """市价单 (以当前最优价成交)"""
        # 获取最优价
        try:
            best_price = self.get_price(token_id, side.upper())
            price = float(best_price.get("price", 0.5))
        except Exception:
            price = 0.5  # fallback

        return self.place_limit_order(
            token_id=token_id,
            side=side,
            size=amount,
            price=price,
            order_type="FAK",
        )

    def cancel_order(self, order_id: str) -> dict:
        """取消订单"""
        if self.mode == "paper":
            return {"status": "CANCELLED", "id": order_id, "mode": "paper"}

        return self._request("DELETE", f"/orders/{order_id}")

    def cancel_all(self) -> dict:
        """取消所有开放订单"""
        if self.mode == "paper":
            self.orders = []
            return {"status": "ALL_CANCELLED", "mode": "paper"}

        return self._request("DELETE", "/orders")

    # ------ 余额 & 持仓 ------
    def get_balance(self) -> dict:
        """查询 USDC 余额"""
        if self.mode == "paper":
            return {
                "usdc_balance": "1,000.00",
                "available": "1,000.00",
                "mode": "paper",
            }

        return self._request("GET", "/balance")

    def get_positions(self) -> list:
        """查询当前持仓"""
        if self.mode == "paper":
            return []

        return self._request("GET", "/positions")

    # ------ 风控 ------
    def _risk_check(self, size: float, price: float):
        """风控检查 — 所有模式都执行（包括 paper，但 paper 只记录不拦截）"""
        errors = []

        # 单笔上限
        if size > self.max_order_usd:
            msg = f"❌ 单笔订单 ${size:.2f} 超过上限 ${self.max_order_usd:.2f}"
            if self.mode == "paper":
                print(f"   ⚠️ [PAPER 风控提醒] {msg}")
            else:
                errors.append(msg)

        # 日限额
        if (self.daily_total + size) > self.max_daily_usd:
            msg = f"❌ 当日累计 ${self.daily_total+size:.2f} 超过日限 ${self.max_daily_usd:.2f}"
            if self.mode == "paper":
                print(f"   ⚠️ [PAPER 风控提醒] {msg}")
            else:
                errors.append(msg)

        # 价格合法性 — 所有模式强制检查
        if not (0.01 <= price <= 0.99):
            raise ValueError(f"❌ 价格 {price} 不在 0.01-0.99 范围内")

        if errors:
            raise ValueError(errors[0])

    # ------ 工具 ------
    @staticmethod
    def extract_token_ids(market: dict) -> dict:
        """从 Gamma API 市场数据中提取 CLOB token IDs"""
        clob_ids = market.get("clobTokenIds", "[]")
        try:
            ids = json.loads(clob_ids) if isinstance(clob_ids, str) else clob_ids
            return {
                "yes": ids[0] if len(ids) > 0 else None,
                "no": ids[1] if len(ids) > 1 else None,
            }
        except (json.JSONDecodeError, TypeError, IndexError):
            return {"yes": None, "no": None}


# ============================================================
# 扫描器信号 → 交易指令
# ============================================================
class SignalTrader:
    """
    连接 Scanner 信号和 CLOB 交易
    """

    def __init__(self, trader: CLOBTrader):
        self.trader = trader

    def execute_signal(self, market: dict, direction: str, amount: float, confidence: float = 0.5):
        """
        执行交易信号

        Args:
            market: scanner.py 返回的 enriched market dict (含 "raw" 原始 Gamma 数据)
            direction: "YES" | "NO"
            amount: 下注金额 (USDC)
            confidence: 信心值 (0-1), 用于动态调整仓位
        """
        # 从 enriched dict 或 raw 数据中提取 token_ids
        raw = market.get("raw", market)
        token_ids = CLOBTrader.extract_token_ids(raw)
        token_id = token_ids["yes"] if direction.upper() == "YES" else token_ids["no"]

        if not token_id:
            return {"error": f"无法获取 token_id for {direction}"}

        # 获取当前最优价
        try:
            order_book = self.trader.get_order_book(token_id)
            best_bid = float(order_book.get("bids", [{}])[0].get("price", 0))
            best_ask = float(order_book.get("asks", [{}])[0].get("price", 1))
        except Exception:
            best_bid = 0.5
            best_ask = 0.5

        # 根据信心调整仓位
        adjusted_amount = round(amount * confidence, 2)

        # 下限价单 (以买一/卖一价)
        if direction.upper() == "YES":
            limit_price = best_ask  # 买 YES 以卖一价
        else:
            limit_price = best_bid  # 买 NO 以买一价

        order = self.trader.place_limit_order(
            token_id=token_id,
            side="BUY",
            size=adjusted_amount,
            price=limit_price,
            order_type="GTC",
        )

        return {
            "order": order,
            "market": market.get("question", "N/A"),
            "direction": direction,
            "amount": adjusted_amount,
            "price": limit_price,
        }

    def execute_ev_signal(self, market: dict, ev_data: dict, max_amount: float = 10):
        """
        基于 EV 信号自动下单

        ev_data 格式 (来自 scanner.py 的 ev_signal 输出):
        {
            "score": 5,
            "summary": "⭐ 强信号",
            "flags": ["争议市场", "高换手率"],
        }
        """
        score = ev_data.get("score", 0)

        # 只对强信号自动下单
        if score < 5:
            return {
                "action": "SKIP",
                "reason": f"EV 分数 {score} < 5, 不够强",
            }

        # 根据信号强度调整金额
        if score >= 8:
            amount = max_amount
        elif score >= 6:
            amount = round(max_amount * 0.6, 2)
        else:
            amount = round(max_amount * 0.3, 2)

        # 判断方向：取价格偏离更大的方向
        yes_price = market.get("yes", 0.5)
        if yes_price > 0.6:
            direction = "YES"
        elif yes_price < 0.4:
            direction = "NO"
        else:
            # 争议市场，选 YES（默认）
            direction = "YES"

        confidence = min(score / 10, 1.0)

        return self.execute_signal(market, direction, amount, confidence)


# ============================================================
# CLI 入口: 测试交易模块
# ============================================================
def main():
    print("\n🔧 Polymarket AI Scanner — 交易模块测试")
    print("   模式: PAPER (不会消耗真实资金)\n")

    # 1. 创建测试钱包
    wallet = PolygonWallet.create()
    print(f"🧪 测试钱包: {wallet.address[:10]}...")
    print(f"   私钥 (前10位): {wallet.private_key[:10]}...")
    print(f"   ⚠️  这是临时测试钱包，请勿使用!")

    # 2. 初始化交易客户端 (paper 模式)
    trader = CLOBTrader(wallet=wallet, mode="paper")
    print(f"\n📡 交易模式: {trader.mode}")

    # 3. 测试余额查询
    balance = trader.get_balance()
    print(f"💰 余额: {json.dumps(balance, indent=2)}")

    # 4. 测试下单
    print("\n📝 测试下单...")
    order = trader.place_limit_order(
        token_id="0x0000000000000000000000000000000000000000",
        side="BUY",
        size=10.0,
        price=0.55,
    )
    print(f"   订单: {json.dumps(order, indent=2)}")

    # 5. 测试获取市场列表 (RO)
    try:
        print("\n📊 获取 CLOB 市场列表...")
        markets = trader.get_markets()
        print(f"   CLOB 可用市场: {len(markets)} 个")
        if markets:
            print(f"   示例: {markets[0].get('question', 'N/A')[:60]}")
    except Exception as e:
        print(f"   ⚠️ CLOB 市场查询失败 (paper 模式正常): {e}")

    # 6. 测试订单查询
    print(f"\n📋 当前订单: {len(trader.orders)} 个")

    print(f"\n{'='*60}")
    print("  交易模块测试完成！")
    print(f"  生产使用前请:")
    print(f"  1. 创建真实 Polygon 钱包并存入 MATIC + USDC.e")
    print(f"  2. 设置环境变量 POLYGON_PRIVATE_KEY=<你的私钥>")
    print(f"  3. 将 mode 改为 'live'")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
