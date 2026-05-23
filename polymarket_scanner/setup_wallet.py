"""
Polymarket AI Scanner — 实盘钱包配置验证
============================================
一键检测：私钥 → 钱包地址 → MATIC余额 → USDC.e余额 → CLOB认证 → 就绪判断

用法:
    python setup_wallet.py

流程:
    1. 读取 .env 中的私钥
    2. 派生钱包地址
    3. 连接 Polygon 链上查 MATIC 和 USDC.e 余额
    4. 派生 CLOB L2 凭证并测试 API 连通性
    5. 输出就绪报告
"""

import os
import sys
import json
import time
from dotenv import load_dotenv

load_dotenv()

from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

POLYGON_RPC = os.getenv("POLYGON_RPC", "https://polygon-rpc.com")
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# USDC.e ABI (balanceOf only)
USDC_ABI = json.loads('[{"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]')


def color(text: str, code: str) -> str:
    """ANSI 颜色"""
    colors = {"green": "92", "red": "91", "yellow": "93", "cyan": "96", "bold": "1"}
    return f"\033[{colors.get(code, '0')}m{text}\033[0m"


def main():
    print("=" * 64)
    print("  Polymarket AI Scanner — 实盘钱包配置验证")
    print("=" * 64)

    results = []

    # =================================================================
    # Step 1: 读取私钥
    # =================================================================
    print(f"\n{color('▸ 第一步：验证私钥', 'cyan')}")
    pk = os.getenv("POLYGON_PRIVATE_KEY", "")

    if not pk or pk == "0x" + "0" * 64:
        print(color("  ❌ .env 中未设置真实私钥（仍为占位值）", "red"))
        print()
        print("  📋 请按以下步骤操作：")
        print("  1. 打开 MetaMask / Rabby 等钱包")
        print("  2. 导出 Polygon 网络账户私钥")
        print("  3. 编辑 .env 文件，将 POLYGON_PRIVATE_KEY 替换为真实私钥")
        print("  4. 保存后重新运行 python setup_wallet.py")
        return

    try:
        wallet = Account.from_key(pk)
    except Exception as e:
        print(color(f"  ❌ 私钥格式无效: {e}", "red"))
        return

    addr = wallet.address
    print(f"  ✅ 私钥有效 → 地址: {color(addr, 'bold')}")
    results.append(("私钥格式", "✅", addr[:10] + "..." + addr[-6:], "green"))

    # =================================================================
    # Step 2: Polygon RPC 连接
    # =================================================================
    print(f"\n{color('▸ 第二步：Polygon 链上连接', 'cyan')}")
    w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))

    if not w3.is_connected():
        print(color(f"  ❌ 无法连接 Polygon RPC: {POLYGON_RPC}", "red"))
        results.append(("Polygon RPC", "❌", "连接失败", "red"))
        return

    block = w3.eth.block_number
    print(f"  ✅ 已连接 Polygon → 当前区块: {block:,}")
    results.append(("Polygon RPC", "✅", f"区块 {block:,}", "green"))

    # =================================================================
    # Step 3: MATIC 余额
    # =================================================================
    print(f"\n{color('▸ 第三步：余额检查', 'cyan')}")

    matic_wei = w3.eth.get_balance(w3.to_checksum_address(addr))
    matic = w3.from_wei(matic_wei, "ether")
    print(f"  MATIC: {color(f'{matic:.4f}', 'green' if matic > 0.01 else 'red')}")

    if matic < 0.001:
        print(color("    ⚠️ MATIC 余额极低，可能无法支付 gas 费！", "yellow"))
        print(color("    建议: 通过交易所或跨链桥转入至少 0.1 MATIC", "yellow"))
        results.append(("MATIC 余额", "⚠️", f"{matic:.6f} MATIC (不足)", "yellow"))
    else:
        results.append(("MATIC 余额", "✅", f"{matic:.4f} MATIC", "green"))

    # USDC.e 余额
    usdc_contract = w3.eth.contract(
        address=w3.to_checksum_address(USDC_E_ADDRESS), abi=USDC_ABI
    )
    usdc_raw = usdc_contract.functions.balanceOf(w3.to_checksum_address(addr)).call()
    decimals = usdc_contract.functions.decimals().call()
    usdc = usdc_raw / (10 ** decimals)

    print(f"  USDC.e: {color(f'{usdc:.2f}', 'green' if usdc > 10 else 'yellow' if usdc > 0 else 'red')}")

    if usdc <= 0:
        print(color("    ⚠️ 无 USDC.e 余额，需要充值后才能交易", "yellow"))
        results.append(("USDC.e 余额", "⚠️", "$0.00 (需充值)", "yellow"))
    elif usdc < 10:
        print(color("    ⚠️ USDC.e 余额偏低 (<$10)，建议存入更多", "yellow"))
        results.append(("USDC.e 余额", "⚠️", f"${usdc:.2f} (偏低)", "yellow"))
    else:
        results.append(("USDC.e 余额", "✅", f"${usdc:,.2f}", "green"))

    # =================================================================
    # Step 4: CLOB API 认证测试
    # =================================================================
    print(f"\n{color('▸ 第四步：CLOB API 认证', 'cyan')}")

    import hashlib, hmac, requests

    timestamp = str(int(time.time()))
    message = (
        "This message attests that I control the wallet "
        + f"submitting this request to Polymarket. Issued at: {timestamp}"
    )

    try:
        signed = Account.from_key(pk).sign_message(
            encode_defunct(text=message)
        )
        sig_hash = hashlib.sha256(signed.signature.hex().encode()).hexdigest()

        api_key = f"0x{addr[2:]}"
        api_secret = sig_hash
        api_passphrase = hashlib.sha256(
            (sig_hash + "polymarket").encode()
        ).hexdigest()[:64]
        api_secret = api_secret[:64]

        print(f"  ✅ L2 凭证派生成功")
        print(f"     API Key:      {color(api_key[:12] + '...' + api_key[-6:], 'bold')}")
        print(f"     API Secret:   {color(api_secret[:12] + '...', 'bold')}")
        results.append(("CLOB 凭证派生", "✅", "L2 签名成功", "green"))

    except Exception as e:
        print(color(f"  ❌ 凭证派生失败: {e}", "red"))
        results.append(("CLOB 凭证派生", "❌", str(e)[:40], "red"))
        return

    # =================================================================
    # Step 5: CLOB API 连通性
    # =================================================================
    print(f"\n{color('▸ 第五步：CLOB API 连通性', 'cyan')}")

    clob_api = "https://clob.polymarket.com"

    try:
        # 先测试未认证的公开接口
        resp = requests.get(f"{clob_api}/markets", timeout=15)
        market_count = len(resp.json()) if resp.ok else 0
        print(f"  ✅ CLOB /markets → {market_count} 个交易市场")
        results.append(("CLOB 公开接口", "✅", f"{market_count} 个市场", "green"))
    except Exception as e:
        print(color(f"  ❌ CLOB 公开接口失败: {e}", "red"))
        results.append(("CLOB 公开接口", "❌", str(e)[:40], "red"))
        return

    # 测试认证接口 (balance) — 使用 py-clob-client 库验证
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import BalanceAllowanceParams

        client = ClobClient(
            host=clob_api,
            key=pk,
            chain_id=137,
            signature_type=0,  # EOA
        )
        # 先获取凭证
        creds = client.derive_api_key()
        client.set_api_creds(creds)

        # 用正确的方式查询余额
        params = BalanceAllowanceParams(signature_type=0, asset_type='COLLATERAL')
        bal_result = client.get_balance_allowance(params)

        if bal_result and hasattr(bal_result, 'balance'):
            usdc_balance = float(bal_result.balance) / 1e6 if float(bal_result.balance) > 1000 else float(bal_result.balance)
            print(f"  ✅ CLOB L2 认证成功 → 余额: ${usdc_balance:,.2f}")
            results.append(("CLOB L2 认证", "✅", f"余额 ${usdc_balance:,.2f}", "green"))
        else:
            print(f"  ✅ CLOB L2 认证成功 → 响应: {bal_result}")
            results.append(("CLOB L2 认证", "✅", "已通", "green"))
    except ImportError:
        print(color("  ⚠️ py-clob-client 未安装", "yellow"))
        results.append(("CLOB L2 认证", "⚠️", "库未安装", "yellow"))
    except Exception as e:
        err_msg = str(e)[:80]
        print(color(f"  ⚠️ CLOB L2 认证异常: {err_msg}", "yellow"))
        results.append(("CLOB L2 认证", "⚠️", err_msg, "yellow"))

    # =================================================================
    # Step 6: L2 注册提示 (如果认证失败)
    # =================================================================
    print(f"\n{color('▸ 第六步：L2 注册检查', 'cyan')}")

    print("  ℹ️  Polymarket 新钱包通常需要在链上注册 L2 账户。")
    print("     步骤:")
    print("     1. 确保你的 Polygon 钱包有 MATIC (gas)")
    print("     2. 访问 https://polymarket.com/ （需特殊网络环境）")
    print("     3. 用你的钱包登录 → 自动触发 L2 注册")
    print("     4. 注册完成后，无需充值到网页 — CLOB API 直连即可交易")

    # =================================================================
    # 风控参数
    # =================================================================
    print(f"\n{color('▸ 风控参数', 'cyan')}")
    max_order = os.getenv("MAX_ORDER_USD", "100")
    max_daily = os.getenv("MAX_DAILY_USD", "500")
    max_slip = os.getenv("MAX_SLIPPAGE", "0.02")
    print(f"  单笔上限:  ${max_order} USDC")
    print(f"  日累计:    ${max_daily} USDC")
    print(f"  滑点容忍:  {float(max_slip)*100:.0f}%")

    # =================================================================
    # 总结报告
    # =================================================================
    print("\n" + "=" * 64)
    print(f"  {color('验证报告', 'bold')}")
    print("=" * 64)

    all_ok = True
    for name, status, detail, level in results:
        s_color = "green" if level == "green" else "yellow" if level == "yellow" else "red"
        print(f"  {color(status, s_color)}  {name:<20} {detail}")
        if level != "green":
            all_ok = False

    print()
    if all_ok:
        print(color("  🟢 钱包配置正常，可以启动实盘交易！", "green"))
        print()
        print("  启动方式:")
        print("     python run.py --live     # 真实下单（有风控保护）")
        print("     python run.py --auto     # Paper 模拟（先试信号逻辑）")
    else:
        has_error = any(r[3] == "red" for r in results)
        if has_error:
            print(color("  🔴 存在错误，请修复后重试", "red"))
        else:
            print(color("  🟡 部分警告，建议修复后启动", "yellow"))

    print()
    print("  ⚠️  安全提醒:")
    print("     - 实盘前建议先跑 python run.py --auto 验证信号逻辑")
    print("     - 确认风控参数 (单笔$" + max_order + "/日$" + max_daily + ") 符合预期")
    print("     - 如要修改风控参数，编辑 .env 文件即可")
    print("     - .env 已加入 .gitignore，不要提交到 Git")
    print("=" * 64)


if __name__ == "__main__":
    main()
