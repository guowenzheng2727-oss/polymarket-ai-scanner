"""
Polymarket AI Scanner - DeepSeek AI 分析模块
提供基于大语言模型的市场深度分析
"""

import os


def get_deepseek_client():
    """获取 DeepSeek 客户端（OpenAI 兼容接口）
    返回 (client, error_message)
    """
    try:
        from openai import OpenAI
    except ImportError:
        return None, "❌ 需要安装 openai 库: pip install openai"

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        # 尝试 Streamlit Cloud secrets
        try:
            import streamlit as st
            api_key = st.secrets.get("DEEPSEEK_API_KEY", "")
        except Exception:
            pass

    if not api_key:
        return None, "⚠️ 未配置 DEEPSEEK_API_KEY（请在 .env 或 Streamlit secrets 中设置）"

    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    return client, None


def analyze_market(
    question: str,
    yes_price: float,
    no_price: float,
    volume: float,
    end_date: str,
    ev_score: int = 0,
    ev_summary: str = "",
    urgency_label: str = "",
    tags: str = "",
) -> str:
    """调用 DeepSeek 对预测市场做深度分析

    Args:
        question: 市场问题
        yes_price: YES 当前价格
        no_price: NO 当前价格
        volume: 24h 成交量
        end_date: 结束时间字符串
        ev_score: EV 量化评分
        ev_summary: EV 信号摘要
        urgency_label: 时间紧迫度标签
        tags: 标签

    Returns:
        AI 分析报告（中文）
    """
    client, error = get_deepseek_client()
    if error:
        return error

    price_pct = yes_price * 100

    prompt = f"""你是预测市场分析专家。分析以下 Polymarket 市场：

【问题】{question}
【标签】{tags if tags else "无"}
【YES价格】${yes_price:.4f}（市场定价概率 ~{price_pct:.0f}%）
【NO价格】${no_price:.4f}
【24h成交量】${volume:,.0f}
【结束时间】{end_date}
【量化EV评分】{ev_score}/12分（{ev_summary}）
【时间紧迫度】{urgency_label}

请用简洁中文给出：
1. **你的真实概率判断**：这件事实际发生的概率大概多少？为什么？
2. **与市场定价的偏差**：市场是高估还是低估了？
3. **操作建议**：买YES / 买NO / 观望，简要理由
4. **一句话风险提示**
5. **自信度**（1-5星，5=非常确定）

控制在200字以内，直接给结论。"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=600,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"❌ AI 分析失败: {str(e)}"


def quick_scan(question: str, yes_price: float, end_date: str) -> str:
    """快速扫一眼市场，只给一句话判断

    用于批量快速评估，不提供详细分析。
    """
    client, error = get_deepseek_client()
    if error:
        return error

    prompt = f"""快速判断这个预测市场是否值得交易，一句话回答：

市场: {question}
当前YES价格: ${yes_price:.4f} (概率 ~{yes_price*100:.0f}%)
结束: {end_date}

只回答以下格式之一：
- 🟢 值得 [理由,15字内]
- 🟡 谨慎 [理由,15字内]
- 🔴 避开 [理由,15字内]"""

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=80,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"分析失败: {str(e)}"
