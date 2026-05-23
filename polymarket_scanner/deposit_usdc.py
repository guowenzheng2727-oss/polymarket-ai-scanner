"""
存入 USDC.e 到 Polymarket CTF Exchange
授权已完成 → 现在把资金转入交易所合约
"""
import os
from dotenv import load_dotenv
load_dotenv()

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware
from eth_account import Account

RPC = os.getenv("POLYGON_RPC", "https://1rpc.io/matic")
PK = os.getenv("POLYGON_PRIVATE_KEY")
CHAIN_ID = 137

wallet = Account.from_key(PK)
PUB_KEY = wallet.address

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"

# CTF Exchange deposit ABI
EXCHANGE_ABI = """[
    {
        "inputs": [
            {"internalType": "address", "name": "token", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"}
        ],
        "name": "deposit",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "collateral", "type": "address"}
        ],
        "name": "balanceOf",
        "outputs": [
            {"internalType": "uint256", "name": "", "type": "uint256"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]"""

w3 = Web3(Web3.HTTPProvider(RPC))
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

if not w3.is_connected():
    print("❌ RPC 连接失败")
    exit(1)

exchange = w3.eth.contract(
    address=w3.to_checksum_address(CTF_EXCHANGE),
    abi=EXCHANGE_ABI
)

# 当前状态
matic = float(w3.from_wei(w3.eth.get_balance(PUB_KEY), 'ether'))
print(f"MATIC: {matic:.4f}")
print(f"钱包: {PUB_KEY}")

# 询问存多少（默认全部存入）
import sys
amount_str = sys.argv[1] if len(sys.argv) > 1 else "32"
amount_units = int(float(amount_str) * 1e6)  # USDC 6 decimals

print(f"存入: ${amount_str} USDC.e")
print(f"目标: {CTF_EXCHANGE}\n")

# 存款前交易所余额
try:
    bal_before = exchange.functions.balanceOf(
        w3.to_checksum_address(USDC_E)
    ).call()
    print(f"交易所 USDC 余额 (存入前): ${float(bal_before)/1e6:,.2f}")
except Exception as e:
    print(f"交易所余额查询跳过: {e}")
    bal_before = 0

# 执行 deposit
nonce = w3.eth.get_transaction_count(PUB_KEY)
gas_price = w3.eth.gas_price

raw_tx = exchange.functions.deposit(
    w3.to_checksum_address(USDC_E),
    amount_units
).build_transaction({
    "chainId": CHAIN_ID,
    "from": PUB_KEY,
    "nonce": nonce,
    "gas": 200000,
    "gasPrice": gas_price,
})

print(f"发送: deposit(${amount_str} USDC.e)")
signed = w3.eth.account.sign_transaction(raw_tx, private_key=PK)
tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
print(f"Tx: {tx_hash.hex()}")
print("等待确认...")

receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)

if receipt.status == 1:
    print(f"✅ 存款成功! (区块 {receipt.blockNumber}, gas: {receipt.gasUsed})")
    
    # 验证
    bal_after = exchange.functions.balanceOf(
        w3.to_checksum_address(USDC_E)
    ).call()
    print(f"交易所 USDC 余额 (存入后): ${float(bal_after)/1e6:,.2f}")
    
    matic_after = float(w3.from_wei(w3.eth.get_balance(PUB_KEY), 'ether'))
    print(f"剩余 MATIC: {matic_after:.4f}")
else:
    print(f"❌ 存款失败: status={receipt.status}")
