"""
Polymarket AI Scanner — Streamlit Cloud 入口
实际代码在 polymarket_scanner/ 子目录
"""
import sys, os, subprocess

# 切换到 polymarket_scanner 目录，用 streamlit 直接启动内层 app.py
subdir = os.path.join(os.path.dirname(__file__), "polymarket_scanner")
sys.path.insert(0, subdir)
os.chdir(subdir)

# 用 runpy 执行，保证 __file__ 指向正确的 app.py
import runpy
runpy.run_path("app.py", run_name="__main__")
