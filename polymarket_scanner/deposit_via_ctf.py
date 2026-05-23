"""
Polymarket 充值脚本 — 通过 CTF Core 合约 splitPosition
========================================================
绕过网页端充值限制，直接调用链上合约将 USDC.e 拆分为条件代币。

流程:
    1. 授权 CTF Core 合约使用 USDC.e
    2. 获取一个活跃市场的 conditionId
    3. 调用 splitPosition 拆分 USDC → YES + NO 代币
    4. 检查 CLOB 余额是否更新

用法:
    python deposit_via_ctf.py [amount]    # 默认 $10
"""

import os
import sys
import json
import time
from dotenv import load_dotenv

# 先尝试从 .env 文件加载
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_path):
    load_dotenv(env_path)
else:
    # 尝试父目录
    parent_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(parent_env):
        load_dotenv(parent_env)
    else:
        load_dotenv()  # fallback

from web3 import Web3
from eth_account import Account

# ============================================================
# 配置
# ============================================================
POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
PRIVATE_KEY = os.getenv("POLYGON_PRIVATE_KEY", os.getenv("PRIVATE_KEY", "")).strip()

# 合约地址
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_CORE = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# ABI
USDC_ABI = json.loads('''[
    {"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}
]''')

CTF_ABI = json.loads('''[
    {"name":"splitPosition","type":"function","inputs":[
        {"name":"collateralToken","type":"address"},
        {"name":"parentCollectionId","type":"bytes32"},
        {"name":"conditionId","type":"bytes32"},
        {"name":"partition","type":"uint256[]"},
        {"name":"amount","type":"uint256"}
    ]},
    {"name":"balanceOf","type":"function","inputs":[
        {"name":"owner","type":"address"},
        {"name":"positionId","type":"uint256"}
    ],"outputs":[{"name":"balance","type":"uint256"}]},
    {"name":"mergePositions","type":"function","inputs":[
        {"name":"collateralToken","type":"address"},
        {"name":"parentCollectionId","type":"bytes32"},
        {"name":"conditionId","type":"bytes32"},
        {"name":"partition","type":"uint256[]"},
        {"name":"amount","type":"uint256"}
    ]}
]''')


def get_position_id(collateral_token: str, condition_id: str, index_set: int) -> str:
    """计算 ERC-1155 position ID"""
    parent_collection_id = "0x" + "0" * 64
    index_bytes = index_set.to_bytes(32, 'big')

    collection_id = Web3.keccak(
        bytes.fromhex(parent_collection_id[2:]) +
        bytes.fromhex(condition_id[2:]) +
        index_bytes
    )

    # collateral token address padded to 32 bytes
    collateral_padded = bytes.fromhex(collateral_token[2:].lower().zfill(64))

    position_id = Web3.keccak(collateral_padded + collection_id)
    return "0x" + position_id.hex()


def main():
    amount_str = sys.argv[1] if len(sys.argv) > 1 else "10"
    amount_usdc = float(amount_str)
    amount_raw = int(amount_usdc * 10**6)

    print("=" * 64)
    print("  Polymarket CTF splitPosition 充值")
    print("=" * 64)
    print(f"  金额: ${amount_usdc:.2f} USDC.e")

    # ============================================================
    # 初始化
    # ============================================================
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
    if not w3.is_connected():
        print("❌ Polygon RPC 连接失败")
        return

    account = Account.from_key(PRIVATE_KEY)
    addr = account.address
    print(f"\n📋 钱包: {addr}")
    print(f"   MATIC: {w3.eth.get_balance(addr) / 1e18:.4f}")

    # ============================================================
    # Step 1: 检查 USDC.e 余额
    # ============================================================
    usdc = w3.eth.contract(address=USDC_E, abi=USDC_ABI)
    usdc_balance = usdc.functions.balanceOf(addr).call()
    print(f"   USDC.e: ${usdc_balance / 1e6:.2f}")

    if usdc_balance < amount_raw:
        print(f"❌ USDC.e 余额不足 (需要 ${amount_usdc:.2f}, 当前 ${usdc_balance / 1e6:.2f})")
        return

    # ============================================================
    # Step 2: 检查 & 授权 CTF Core
    # ============================================================
    print(f"\n📋 Step 1: 检查 CTF Core 授权")
    allowance = usdc.functions.allowance(addr, CTF_CORE).call()
    print(f"   当前授权: ${allowance / 1e6:.2f}")

    if allowance < amount_raw:
        print(f"   🔐 需要授权 ${amount_usdc:.2f} ...")
        nonce = w3.eth.get_transaction_count(addr)
        tx = usdc.functions.approve(CTF_CORE, amount_raw).build_transaction({
            'from': addr,
            'nonce': nonce,
            'gas': 100000,
            'gasPrice': w3.eth.gas_price,
            'chainId': 137,
        })
        signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"   ✅ 授权交易已发送: {tx_hash.hex()[:16]}...")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        print(f"   ✅ 授权已确认 (block {receipt['blockNumber']})")
    else:
        print(f"   ✅ 授权额度充足")

    # ============================================================
    # Step 3: 获取 conditionId（从 Gamma API）
    # ============================================================
    print(f"\n📋 Step 2: 获取活跃市场 conditionId")
    import requests
    try:
        resp = requests.get(
            "https://gamma-api.polymarket.com/markets",
            params={"limit": 5, "closed": "false", "order": "volume24hr", "ascending": "false"},
            timeout=15
        )
        markets = resp.json()
        market = markets[0]
        condition_id = market['conditionId']  # Gamma API uses camelCase
        print(f"   市场: {market.get('question', '?')[:60]}")
        print(f"   conditionId: {condition_id[:16]}...")
    except Exception as e:
        print(f"❌ 无法获取市场: {e}")
        return

    # ============================================================
    # Step 4: 调用 splitPosition
    # ============================================================
    print(f"\n📋 Step 3: 调用 splitPosition")
    ctf = w3.eth.contract(address=CTF_CORE, abi=CTF_ABI)

    nonce = w3.eth.get_transaction_count(addr)
    tx = ctf.functions.splitPosition(
        w3.to_checksum_address(USDC_E),    # collateralToken
        "0x" + "0" * 64,                    # parentCollectionId (全零)
        condition_id,                        # conditionId
        [1, 2],                              # partition [Yes, No]
        amount_raw                           # amount
    ).build_transaction({
        'from': addr,
        'nonce': nonce,
        'gas': 250000,
        'gasPrice': w3.eth.gas_price,
        'chainId': 137,
    })
    signed = w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"   ✅ splitPosition 已发送: {tx_hash.hex()[:16]}...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print(f"   ✅ 已确认 (block {receipt['blockNumber']}, gas used: {receipt.get('gasUsed', '?')})")

    # ============================================================
    # Step 5: 验证拆分结果
    # ============================================================
    print(f"\n📋 Step 4: 验证条件代币余额")
    yes_id = get_position_id(USDC_E, condition_id, 1)
    no_id = get_position_id(USDC_E, condition_id, 2)

    yes_bal = ctf.functions.balanceOf(addr, int(yes_id, 16)).call()
    no_bal = ctf.functions.balanceOf(addr, int(no_id, 16)).call()

    print(f"   YES 代币: {yes_bal / 1e6:.6f}")
    print(f"   NO  代币: {no_bal / 1e6:.6f}")

    # ============================================================
    # Step 6: 检查 CLOB 余额
    # ============================================================
    print(f"\n📋 Step 5: 检查 CLOB 余额")
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import BalanceAllowanceParams

        client = ClobClient(
            host="https://clob.polymarket.com",
            key=PRIVATE_KEY,
            chain_id=137,
            signature_type=0,
        )
        creds = client.derive_api_key()
        client.set_api_creds(creds)

        params = BalanceAllowanceParams(signature_type=0, asset_type='COLLATERAL')
        bal = client.get_balance_allowance(params)

        print(f"   CLOB 余额: {bal.balance if hasattr(bal, 'balance') else bal}")
    except Exception as e:
        print(f"   ⚠️ CLOB 查询失败: {e}")

    # ============================================================
    # 总结
    # ============================================================
    print(f"\n{'=' * 64}")
    print(f"  ✅ splitPosition 完成!")
    print(f"  拆分了 ${amount_usdc:.2f} → YES+NO 代币")
    print(f"  交易哈希: {tx_hash.hex()}")
    print(f"  Polygonscan: https://polygonscan.com/tx/{tx_hash.hex()}")
    print(f"{'=' * 64}")


if __name__ == "__main__":
    main()
