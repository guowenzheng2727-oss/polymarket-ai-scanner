"""
Polymarket L2 存款脚本
将钱包中的 USDC.e 存入 Polymarket 交易合约
"""
import os, json, time
from dotenv import load_dotenv
load_dotenv()

from web3 import Web3
from eth_account import Account

# ── 配置 ──────────────────────────────────────
RPC = os.getenv("POLYGON_RPC", "https://1rpc.io/matic")
PK = os.getenv("POLYGON_PRIVATE_KEY")

# Polymarket CTF Exchange (Polygon)
EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
# USDC.e on Polygon
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# ── 初始化 ──────────────────────────────────────
w3 = Web3(Web3.HTTPProvider(RPC))
wallet = Account.from_key(PK)
addr = w3.to_checksum_address(wallet.address)
exchange = w3.to_checksum_address(EXCHANGE)
usdc_e = w3.to_checksum_address(USDC_E)

print(f"钱包: {addr}")
print(f"交易所: {exchange}")
print()

# ── USDC.e ABI ──────────────────────────────
usdc_abi = json.loads("""[
    {"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":false,"inputs":[{"name":"_spender","type":"address"},{"name":"_value","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":true,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]""")

usdc_contract = w3.eth.contract(address=usdc_e, abi=usdc_abi)

# ── 查询余额和授权 ────────────────────────────
matic_bal = float(w3.from_wei(w3.eth.get_balance(addr), 'ether'))
usdc_bal = usdc_contract.functions.balanceOf(addr).call() / 1e6
allowance = usdc_contract.functions.allowance(addr, exchange).call() / 1e6

print(f"MATIC:    {matic_bal:.4f}")
print(f"USDC.e:   ${usdc_bal:.2f}")
print(f"授权额度: ${allowance:.2f}")
print()

# ── 查询 L2 状态 ──────────────────────────────
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import BalanceAllowanceParams

client = ClobClient(host='https://clob.polymarket.com', key=PK, chain_id=137, signature_type=0)
creds = client.derive_api_key()
client.set_api_creds(creds)

params = BalanceAllowanceParams(signature_type=0, asset_type='COLLATERAL')
try:
    bal = client.get_balance_allowance(params)
    l2_balance = int(bal['balance']) / 1e6 if bal.get('balance') else 0
    print(f"L2 余额:   ${l2_balance:,.2f}")
except Exception as e:
    print(f"L2 查询失败: {e}")
    l2_balance = 0

print()

# ── 第一步: Approve USDC.e ────────────────────
if usdc_bal <= 0:
    print("❌ 钱包中没有 USDC.e，请先兑换！")
    exit(1)

DEPOSIT_AMOUNT = usdc_bal  # 存全部

if allowance < DEPOSIT_AMOUNT:
    print(f"🔓 步骤1: 授权 USDC.e (${DEPOSIT_AMOUNT:.2f}) 给交易所合约...")
    
    raw_amount = int(DEPOSIT_AMOUNT * 1e6)
    tx = usdc_contract.functions.approve(exchange, raw_amount).build_transaction({
        'from': addr,
        'nonce': w3.eth.get_transaction_count(addr),
        'gas': 100000,
        'gasPrice': w3.eth.gas_price,
    })
    
    signed = wallet.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  Approve TX: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print(f"  ✅ 授权成功! (区块 {receipt.blockNumber})")
else:
    print(f"✅ 授权已足够 (${allowance:.2f} >= ${DEPOSIT_AMOUNT:.2f})")

print()

# ── 第二步: 存款到交易所 ────────────────────────
# Polymarket CTF Exchange deposit 函数
# 函数签名: deposit(address token, uint256 amount) 或 depositFor(address user, address token, uint256 amount)

# 先查 Exchange ABI
exchange_abi_json = {
    "abi": [
        {"constant": False, "inputs": [
            {"name": "token", "type": "address"},
            {"name": "amount", "type": "uint256"}
        ], "name": "deposit", "outputs": [], "type": "function"},
        {"constant": False, "inputs": [
            {"name": "user", "type": "address"},
            {"name": "token", "type": "address"},
            {"name": "amount", "type": "uint256"}
        ], "name": "depositFor", "outputs": [], "type": "function"},
    ]
}

print(f"🔗 步骤2: 存入 ${DEPOSIT_AMOUNT:.2f} USDC.e 到 Polymarket...")

exchange_contract = w3.eth.contract(address=exchange, abi=exchange_abi_json['abi'])

try:
    raw_amount = int(DEPOSIT_AMOUNT * 1e6)
    
    # 先尝试 depositFor
    tx = exchange_contract.functions.depositFor(addr, usdc_e, raw_amount).build_transaction({
        'from': addr,
        'nonce': w3.eth.get_transaction_count(addr),
        'gas': 300000,
        'gasPrice': w3.eth.gas_price,
    })
    
    signed = wallet.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  Deposit TX: {tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print(f"  ✅ 存款成功! (区块 {receipt.blockNumber})")
    
except Exception as e1:
    print(f"  depositFor 失败: {e1}")
    try:
        # 尝试 deposit
        tx = exchange_contract.functions.deposit(usdc_e, raw_amount).build_transaction({
            'from': addr,
            'nonce': w3.eth.get_transaction_count(addr),
            'gas': 300000,
            'gasPrice': w3.eth.gas_price,
        })
        signed = wallet.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"  Deposit TX: {tx_hash.hex()}")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        print(f"  ✅ 存款成功! (区块 {receipt.blockNumber})")
    except Exception as e2:
        print(f"  deposit 也失败: {e2}")
        print()
        print("  可能原因：需要查询交易所合约的准确 ABI")
        print("  或者需要先通过 Polymarket 网页注册 L2")
        exit(1)

print()

# ── 第三步: 刷新 L2 余额 ──────────────────────
time.sleep(3)
print("🔄 刷新 L2 余额...")
try:
    client.update_balance_allowance(params)
    bal2 = client.get_balance_allowance(params)
    l2_balance2 = int(bal2['balance']) / 1e6 if bal2.get('balance') else 0
    print(f"  L2 余额:   ${l2_balance2:,.2f}")
    if l2_balance2 > 0:
        print("  ✅ 存款已确认！Polymarket 可以交易了")
except Exception as e:
    print(f"  L2 刷新失败: {e}")

# ── 最终状态 ──────────────────────────────────
final_matic = float(w3.from_wei(w3.eth.get_balance(addr), 'ether'))
final_usdc = usdc_contract.functions.balanceOf(addr).call() / 1e6

print()
print("=" * 60)
print("  最终状态")
print("=" * 60)
print(f"  MATIC:    {final_matic:.4f}")
print(f"  USDC.e:   ${final_usdc:.2f}")
print(f"  交易数:   {w3.eth.get_transaction_count(addr)}")
