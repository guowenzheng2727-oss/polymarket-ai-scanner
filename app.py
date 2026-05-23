"""
Polymarket AI Scanner — Streamlit Cloud 入口
实际代码在 polymarket_scanner/ 子目录
"""
import sys, os

# 确保子目录在 sys.path 中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "polymarket_scanner"))

# 直接执行实际 app
exec(open(os.path.join(os.path.dirname(__file__), "polymarket_scanner", "app.py"), encoding="utf-8").read())
