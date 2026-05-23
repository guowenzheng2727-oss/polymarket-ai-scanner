"""
Polymarket L2 Registration Script
绕过网页端地区限制，直接通过代码完成 L2 注册
"""
import os, json, time
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
import requests

pk = os.getenv('POLYGON_PRIVATE_KEY')
rpc = os.getenv('POLYGON_RPC', 'https://1rpc.io/matic')
wallet = Account.from_key(pk)
address = wallet.address

print(f'钱包: {address}')
print('=' * 60)

# Step 1: Try py-clob-client approach
print('\n[1] 尝试 py-clob-client 方式...')
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds
    
    client = ClobClient(
        host='https://clob.polymarket.com',
        key=pk,
        chain_id=137,
    )
    ts = client.get_server_time()
    print(f'    ✅ 连接成功, server time: {ts}')
    
    # Try create_api_key
    try:
        api_key = client.create_api_key()
        print(f'    ✅ API Key创建成功: {api_key}')
    except Exception as ke:
        print(f'    ⚠️  create_api_key: {ke}')
    
    # Try deposit
    try:
        bal = client.get_balance_allowance()
        print(f'    Balances: {bal}')
    except Exception as be:
        print(f'    ⚠️ get_balance: {be}')
        
except Exception as e:
    print(f'    ❌ ClobClient 方式失败: {e}')
    print('    -> 尝试直接 HTTP 方式...')

# Step 2: Direct HTTP - try CLOB API
print('\n[2] 直接 HTTP 调用 CLOB API...')
ts_str = str(int(time.time()))

# Try server time
try:
    r = requests.get('https://clob.polymarket.com/time', timeout=10)
    print(f'    GET /time -> {r.status_code}: {r.text[:100]}')
except Exception as e:
    print(f'    GET /time 失败: {e}')

# Try derive API key endpoint
msg_text = f'Login to Polymarket CLOB: {ts_str}'
signed = wallet.sign_message(encode_defunct(text=msg_text))
sig = signed.signature.hex()

# Endpoints to try
endpoints = [
    ('POST', '/auth/derive-api-keys'),
    ('POST', '/auth/derive-api-key'),
    ('POST', '/derive-api-key'),
    ('POST', '/auth/api-key'),
]

for method, ep in endpoints:
    url = f'https://clob.polymarket.com{ep}'
    try:
        r = requests.post(url, json={
            'address': address,
            'timestamp': ts_str,
            'message': msg_text,
            'signature': sig,
        }, timeout=10)
        print(f'    {method} {ep} -> {r.status_code}: {r.text[:150]}')
    except Exception as e:
        print(f'    {method} {ep} -> Error: {e}')

print('\n' + '=' * 60)
print('如果以上都失败，L2 注册可能需要通过 Polymarket 链上合约触发')
print('下一步：通过 CTF Exchange 合约在链上注册')
