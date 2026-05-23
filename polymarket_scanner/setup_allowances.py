"""
Polymarket 合约授权设置
一次性操作：授权 Polymarket 合约使用你的 USDC.e 和 CTF 代币
执行后永久生效，无需重复操作

基于: https://gist.github.com/poly-rodr/44313920481de58d5a3f6d1f8226bd5e
"""
import os, time
from dotenv import load_dotenv
load_dotenv()

from web3 import Web3
from web3.constants import MAX_INT
from web3.middleware import ExtraDataToPOAMiddleware

# ── 配置 ──────────────────────────────────────
RPC = os.getenv("POLYGON_RPC", "https://1rpc.io/matic")
PK = os.getenv("POLYGON_PRIVATE_KEY")
CHAIN_ID = 137

# 从私钥派生地址
from eth_account import Account
wallet = Account.from_key(PK)
PUB_KEY = wallet.address

# ── 合约地址 ──────────────────────────────────
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF    = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

CTF_EXCHANGE    = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"  # 主交易所
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"  # Neg Risk 交易所
NEG_RISK_ADAPTER  = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"  # Neg Risk 适配器

TARGETS = [
    ("CTF Exchange", CTF_EXCHANGE),
    ("Neg Risk Exchange", NEG_RISK_EXCHANGE),
    ("Neg Risk Adapter", NEG_RISK_ADAPTER),
]

# ── ABI ──────────────────────────────────────
ERC20_APPROVE_ABI = """[{"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"}]"""
ERC1155_APPROVAL_ABI = """[{"inputs":[{"internalType":"address","name":"operator","type":"address"},{"internalType":"bool","name":"approved","type":"bool"}],"name":"setApprovalForAll","outputs":[],"stateMutability":"nonpayable","type":"function"}]"""

# ── Web3 ─────────────────────────────────────
w3 = Web3(Web3.HTTPProvider(RPC))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

if not w3.is_connected():
    print("❌ 无法连接 Polygon RPC")
    exit(1)

print("=" * 60)
print("  Polymarket 合约授权设置")
print("=" * 60)
print(f"  钱包:   {PUB_KEY}")
print(f"  MATIC:  {float(w3.from_wei(w3.eth.get_balance(PUB_KEY), 'ether')):.4f}")
print(f"  区块:   {w3.eth.block_number:,}")
print()

# ── 合约实例 ──────────────────────────────────
usdc = w3.eth.contract(address=w3.to_checksum_address(USDC_E), abi=ERC20_APPROVE_ABI)
ctf  = w3.eth.contract(address=w3.to_checksum_address(CTF), abi=ERC1155_APPROVAL_ABI)

def send_tx(raw_tx, desc):
    """发送交易并等待确认"""
    try:
        signed = w3.eth.account.sign_transaction(raw_tx, private_key=PK)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"    发送: {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        if receipt.status == 1:
            print(f"    ✅ 成功 (区块 {receipt.blockNumber}, gas: {receipt.gasUsed})")
            return True
        else:
            print(f"    ❌ 失败 (状态: {receipt.status})")
            return False
    except Exception as e:
        print(f"    ❌ 异常: {e}")
        return False

# ── 执行 6 笔授权 ──────────────────────────────
nonce = w3.eth.get_transaction_count(PUB_KEY)
total = len(TARGETS) * 2
current = 0

for name, target in TARGETS:
    checksum_target = w3.to_checksum_address(target)
    
    # ── USDC approve ──
    current += 1
    print(f"[{current}/{total}] USDC → {name}")
    raw = usdc.functions.approve(checksum_target, int(MAX_INT, 0)).build_transaction({
        "chainId": CHAIN_ID,
        "from": PUB_KEY,
        "nonce": nonce,
        "gas": 100000,
        "gasPrice": w3.eth.gas_price,
    })
    if send_tx(raw, f"USDC {name}"):
        nonce += 1
        time.sleep(2)
    else:
        print("    ⚠️ 跳过后续，检查 gas/网络状态")
        exit(1)
    
    # ── CTF setApprovalForAll ──
    current += 1
    print(f"[{current}/{total}] CTF → {name}")
    raw = ctf.functions.setApprovalForAll(checksum_target, True).build_transaction({
        "chainId": CHAIN_ID,
        "from": PUB_KEY,
        "nonce": nonce,
        "gas": 100000,
        "gasPrice": w3.eth.gas_price,
    })
    if send_tx(raw, f"CTF {name}"):
        nonce += 1
        time.sleep(2)
    else:
        print("    ⚠️ 跳过后续")
        exit(1)

print()
print("=" * 60)
print("  🎉 全部授权完成！钱包已就绪")
print("=" * 60)
print(f"  执行: {total} 笔交易全部成功")
print(f"  MATIC: {float(w3.from_wei(w3.eth.get_balance(PUB_KEY), 'ether')):.4f}")
print(f"  下一步: python run.py --auto   (paper 信号先跑一遍)")
print(f"         python run.py --live    (实盘交易)")
