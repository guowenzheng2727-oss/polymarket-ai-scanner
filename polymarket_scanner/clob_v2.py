"""
Polymarket CLOB 下单模块 (V1 对齐 TS SDK 5.8.1)
==================================================
TS SDK v5.8.1 使用 V1 格式 (PROTOCOL_VERSION="1", Exchange 0x4bFb41...)。
绕过 py-clob-client SDK 的潜在 bug，直接构建 V1 订单签名提交。
"""

import os
import json
import time
import math
from typing import Optional, Literal
from dotenv import load_dotenv

import requests
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

from py_clob_client.signing.hmac import build_hmac_signature

load_dotenv()

# ============================================================
# 常量 — 对齐 TS SDK config.js
# ============================================================
CHAIN_ID = 137
CLOB_API = "https://clob.polymarket.com"

# V1 合约地址 (TS SDK 5.8.1 MATIC_CONTRACTS)
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

TOKEN_DECIMALS = 1_000_000

# ============================================================
# EIP-712 类型定义 — 对齐 TS SDK exchange.order.const.js
# ============================================================
EIP712_DOMAIN_TYPES = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]

ORDER_STRUCTURE = [
    {"name": "salt", "type": "uint256"},
    {"name": "maker", "type": "address"},
    {"name": "signer", "type": "address"},
    {"name": "taker", "type": "address"},
    {"name": "tokenId", "type": "uint256"},
    {"name": "makerAmount", "type": "uint256"},
    {"name": "takerAmount", "type": "uint256"},
    {"name": "expiration", "type": "uint256"},
    {"name": "nonce", "type": "uint256"},
    {"name": "feeRateBps", "type": "uint256"},
    {"name": "side", "type": "uint8"},
    {"name": "signatureType", "type": "uint8"},
]

EIP712_ORDER_TYPES = {
    "EIP712Domain": EIP712_DOMAIN_TYPES,
    "Order": ORDER_STRUCTURE,
}

# ============================================================
# 金额计算 — 对齐 TS SDK helpers.js ROUNDING_CONFIG
# ============================================================
ROUNDING_CONFIG = {
    "0.1":    {"price": 1, "size": 2, "amount": 3},
    "0.01":   {"price": 2, "size": 2, "amount": 4},
    "0.001":  {"price": 3, "size": 2, "amount": 5},
    "0.0001": {"price": 4, "size": 2, "amount": 6},
}


def _round_normal(x: float, decimals: int) -> float:
    return round(x, decimals)


def _round_down(x: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.floor(x * factor) / factor


def _round_up(x: float, decimals: int) -> float:
    factor = 10 ** decimals
    return math.ceil(x * factor) / factor


def _decimal_places(x: float) -> int:
    s = f"{x:.18f}".rstrip("0")
    if "." in s:
        return len(s.split(".")[1])
    return 0


def calc_order_amounts(
    side: Literal["BUY", "SELL"],
    size: float,
    price: float,
    tick_size: str,
) -> tuple[int, int]:
    """
    Returns (maker_amount, taker_amount) in 1e6 units.
    对齐 TS SDK getOrderRawAmounts + parseUnits(COLLATERAL_TOKEN_DECIMALS=6)
    """
    cfg = ROUNDING_CONFIG[tick_size]
    raw_price = _round_normal(price, cfg["price"])

    if side == "BUY":
        raw_taker = _round_down(size, cfg["size"])
        raw_maker = raw_taker * raw_price
        if _decimal_places(raw_maker) > cfg["amount"]:
            raw_maker = _round_up(raw_maker, cfg["amount"] + 4)
            if _decimal_places(raw_maker) > cfg["amount"]:
                raw_maker = _round_down(raw_maker, cfg["amount"])
        return int(raw_maker * TOKEN_DECIMALS), int(raw_taker * TOKEN_DECIMALS)
    else:  # SELL
        raw_maker = _round_down(size, cfg["size"])
        raw_taker = raw_maker * raw_price
        if _decimal_places(raw_taker) > cfg["amount"]:
            raw_taker = _round_up(raw_taker, cfg["amount"] + 4)
            if _decimal_places(raw_taker) > cfg["amount"]:
                raw_taker = _round_down(raw_taker, cfg["amount"])
        return int(raw_maker * TOKEN_DECIMALS), int(raw_taker * TOKEN_DECIMALS)


# ============================================================
# V1 订单 EIP-712 签名 — 对齐 TS SDK ExchangeOrderBuilder
# ============================================================
def sign_order_v1(
    salt: int,
    maker: str,
    token_id: str,
    maker_amount: int,
    taker_amount: int,
    side: int,
    private_key: str,
    taker: str = ZERO_ADDRESS,
    expiration: int = 0,
    nonce: int = 0,
    fee_rate_bps: int = 0,
    signature_type: int = 0,
    neg_risk: bool = False,
) -> tuple[dict, str]:
    """
    构建 V1 订单并通过 EIP-712 签名

    对齐 TS SDK:
      - ExchangeOrderBuilder.buildOrder() → buildSignedOrder()
      - domain: PROTOCOL_NAME="Polymarket CTF Exchange", version="1"

    Returns:
        (order_payload_for_api, signature_hex)
    """
    address = Account.from_key(private_key).address

    exchange = NEG_RISK_EXCHANGE if neg_risk else CTF_EXCHANGE
    exchange_name = ("Polymarket Neg Risk CTF Exchange"
                     if neg_risk else "Polymarket CTF Exchange")

    domain = {
        "name": exchange_name,
        "version": "1",
        "chainId": CHAIN_ID,
        "verifyingContract": exchange,
    }

    message = {
        "salt": salt,
        "maker": address,
        "signer": address,
        "taker": taker,
        "tokenId": int(token_id),
        "makerAmount": maker_amount,
        "takerAmount": taker_amount,
        "expiration": expiration,
        "nonce": nonce,
        "feeRateBps": fee_rate_bps,
        "side": side,
        "signatureType": signature_type,
    }

    typed_data = {
        "types": EIP712_ORDER_TYPES,
        "primaryType": "Order",
        "domain": domain,
        "message": message,
    }

    signable = encode_typed_data(full_message=typed_data)
    signed = Account.sign_message(signable, private_key)
    signature = signed.signature.hex()

    # API payload — 对齐 TS SDK orderToJson (V1)
    # TS SDK 中 salt 是 number, 金额/ID 是 string, side 是 string
    payload = {
        "salt": salt,
        "maker": address,
        "signer": address,
        "taker": taker,
        "tokenId": str(token_id),
        "makerAmount": str(maker_amount),
        "takerAmount": str(taker_amount),
        "expiration": str(expiration),
        "nonce": str(nonce),
        "feeRateBps": str(fee_rate_bps),
        "side": "BUY" if side == 0 else "SELL",
        "signatureType": signature_type,
        "signature": signature,
    }

    return payload, signature


# ============================================================
# CLOB API 客户端
# ============================================================
class CLOBClient:
    """
    Polymarket CLOB 客户端 (V1 对齐 TS SDK)
    - L1 Auth: 复用 SDK derive_api_key()
    - L2 Auth: 手动 HMAC
    - 下单: 手动 V1 EIP-712 签名
    """

    def __init__(self, host: str = CLOB_API):
        self.host = host
        private_key = os.getenv("POLYGON_PRIVATE_KEY",
                                os.getenv("PRIVATE_KEY", "")).strip()
        if not private_key:
            raise ValueError("Missing private key in env")

        self._pk = private_key
        self.account = Account.from_key(private_key)
        self.address = Web3.to_checksum_address(self.account.address)

        # L1: 从 SDK 获取 API 凭证
        from py_clob_client.client import ClobClient as SdkClobClient

        print("[CLOB] 获取 API 凭证...")
        sdk = SdkClobClient(host=host, key=private_key, chain_id=CHAIN_ID, signature_type=0)
        creds = sdk.derive_api_key()

        self.api_key = creds.api_key
        self.api_secret = creds.api_secret
        self.api_passphrase = creds.api_passphrase

        print(f"  API Key: {self.api_key[:16]}...")
        print(f"  地址: {self.address}")

        # 保存到 .env
        env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                content = f.read()
            for key in ["CLOB_API_KEY", "CLOB_API_SECRET", "CLOB_API_PASSPHRASE"]:
                if key in content:
                    content = "\n".join(
                        [l for l in content.split("\n") if not l.startswith(key + "=")]
                    )
            with open(env_file, "w", encoding="utf-8") as f:
                f.write(content.rstrip("\n") + "\n")
                f.write(f"CLOB_API_KEY={self.api_key}\n")
                f.write(f"CLOB_API_SECRET={self.api_secret}\n")
                f.write(f"CLOB_API_PASSPHRASE={self.api_passphrase}\n")
            print("  凭证已保存到 .env")

    def _l2_headers(self, method: str, path: str, body: str) -> dict:
        """L2 HMAC 认证头部"""
        timestamp = int(time.time())
        sig = build_hmac_signature(
            self.api_secret, timestamp, method, path, body
        )
        return {
            "POLY_ADDRESS": self.address,
            "POLY_SIGNATURE": sig,
            "POLY_TIMESTAMP": str(timestamp),
            "POLY_API_KEY": self.api_key,
            "POLY_PASSPHRASE": self.api_passphrase,
            "Content-Type": "application/json",
        }

    def post_order(self, order_payload: dict,
                   order_type: str = "GTC",
                   post_only: bool = False,
                   defer_exec: bool = False) -> dict:
        """提交订单到 CLOB API"""
        path = "/order"
        body = json.dumps(
            {
                "order": order_payload,
                "owner": self.api_key,
                "orderType": order_type,
                "postOnly": post_only,
                "deferExec": defer_exec,
            },
            separators=(",", ":"),
        )

        headers = self._l2_headers("POST", path, body)
        resp = requests.post(f"{self.host}{path}", headers=headers, data=body, timeout=30)

        if resp.status_code in (200, 201):
            return resp.json()
        else:
            raise Exception(f"下单失败 [{resp.status_code}]: {resp.text[:500]}")

    def get_balance(self) -> dict:
        """查询 CLOB 余额"""
        path = "/balance-allowance"
        params = "?asset_type=COLLATERAL"
        body = ""

        headers = self._l2_headers("GET", path + params, body)
        resp = requests.get(f"{self.host}{path}{params}", headers=headers, timeout=15)

        if resp.status_code == 200:
            return resp.json()
        else:
            raise Exception(f"查余额失败 [{resp.status_code}]: {resp.text[:200]}")

    def get_orderbook(self, token_id: str) -> dict:
        """查询订单簿"""
        path = "/book"
        params = f"?token_id={token_id}"
        body = ""

        headers = self._l2_headers("GET", path + params, body)
        resp = requests.get(f"{self.host}{path}{params}", headers=headers, timeout=15)

        if resp.status_code == 200:
            return resp.json()
        else:
            raise Exception(f"查订单簿失败 [{resp.status_code}]: {resp.text[:200]}")


# ============================================================
# 高层 API
# ============================================================
def place_order(
    token_id: str,
    side: Literal["BUY", "SELL"],
    size: float,
    price: float,
    tick_size: str = "0.01",
    neg_risk: bool = False,
    client: Optional[CLOBClient] = None,
    taker: str = ZERO_ADDRESS,
    expiration: int = 0,
    nonce: int = 0,
    fee_rate_bps: int = 0,
) -> dict:
    """一键下单"""
    if client is None:
        client = CLOBClient()

    side_int = 0 if side.upper() == "BUY" else 1
    maker_amount, taker_amount = calc_order_amounts(side.upper(), size, price, tick_size)
    salt = int(time.time() * 1_000_000) % (2**64)

    print(f"  salt={salt}, makerAmount={maker_amount}, takerAmount={taker_amount}")
    print(f"  side={side}, price={price}, size={size}")

    order_payload, sig = sign_order_v1(
        salt=salt,
        maker=client.address,
        token_id=token_id,
        maker_amount=maker_amount,
        taker_amount=taker_amount,
        side=side_int,
        private_key=client._pk,
        taker=taker,
        expiration=expiration,
        nonce=nonce,
        fee_rate_bps=fee_rate_bps,
        neg_risk=neg_risk,
    )

    print(f"  signature: {sig[:32]}...")
    return client.post_order(order_payload)


# ============================================================
# 自检
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("CLOB 下单模块自检 (V1)")
    print("=" * 60)

    try:
        client = CLOBClient()
        print(f"认证 OK: {client.address}")

        # 查余额
        bal = client.get_balance()
        print(f"L2 余额: {json.dumps(bal, indent=2)}")

        # 获取市场 (用 SDK)
        from py_clob_client.client import ClobClient
        sdk = ClobClient(CLOB_API, key=client._pk, chain_id=CHAIN_ID, signature_type=0)
        sdk.set_api_creds(sdk.derive_api_key())

        markets = sdk.get_simplified_markets()
        if isinstance(markets, dict) and "data" in markets:
            markets = markets["data"]

        print(f"可用市场: {len(markets)} 个")

        # 找一个可交易的市场
        target = None
        for m in markets:
            ts = m.get("tick_size", "1")
            if ts in ROUNDING_CONFIG:
                tokens = m.get("tokens", [])
                if tokens:
                    target = m
                    break

        if not target:
            print("没有找到可交易的市场")
            exit(1)

        token = target["tokens"][0]
        token_id = token["token_id"]
        tick_size = target.get("tick_size", "0.01")
        neg_risk = target.get("neg_risk", False)
        question = target.get("question", "?")[:60]
        outcome = token.get("outcome", "?")

        # 取 orderbook 最佳价格
        try:
            ob = client.get_orderbook(token_id)
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            best_bid = float(bids[0]["price"]) if bids else 0.50
            best_ask = float(asks[0]["price"]) if asks else 0.50
            print(f"Orderbook: bestBid={best_bid:.4f}, bestAsk={best_ask:.4f}")
        except:
            best_bid = float(token.get("price", 0.50))
            best_ask = best_bid

        print(f"\n市场: {question}")
        print(f"  outcome: {outcome}, tokenId: {token_id[:24]}...")
        print(f"  tick: {tick_size}, negRisk: {neg_risk}")

        # 测试: BUY $1 @ best_ask 略低 (避免吃单，做 maker)
        order_price = _round_normal(best_ask - 0.01, 2) if best_ask > 0.02 else best_ask

        print(f"\n下单: BUY $1 @ {order_price:.4f}")
        result = place_order(
            token_id=token_id,
            side="BUY",
            size=1.0,
            price=order_price,
            tick_size=tick_size,
            neg_risk=neg_risk,
            client=client,
        )
        print(f"\n下单成功!")
        print(json.dumps(result, indent=2))

    except Exception as e:
        print(f"\n失败: {e}")
        import traceback
        traceback.print_exc()
