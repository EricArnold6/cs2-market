"""DingTalk Markdown message builder for anomaly alerts.

All functions are pure (no network, no file I/O) so they are trivially
unit-testable without mocking. Includes V4.0 Whale Tracking & Volatility metrics.
"""

# Signal-type display metadata (DingTalk Markdown supports hex colors)
_SIGNAL_META: dict[str, dict] = {
    "WHALE_CONFIRMED_BUY": {
        "label": "🐳🚀 巨鲸绝杀建仓 (WHALE_CONFIRMED_BUY)",
        "color": "#E62A10",  # 深红/警示红
        "summary": "散户K线与庄家筹码同时指向暴涨，高度控盘拉升预警！",
    },
    "ACCUMULATION": {
        "label": "🚀 放量突破建仓 (ACCUMULATION)",
        "color": "#FF5722",  # 橙红
        "summary": "检测到供应萎缩且K线伴随放量，疑似主力入场吸筹。",
    },
    "DUMP_RISK": {
        "label": "🚨 砸盘预警 (DUMP_RISK)",
        "color": "#FF9800",  # 橘色
        "summary": "抛压瞬间激增且价格实质性下挫，警惕主力出货或撤单。",
    },
    "ARBITRAGE_OPPORTUNITY": {
        "label": "💎 跨平台套利 (ARBITRAGE_OPPORTUNITY)",
        "color": "#2196F3",  # 科技蓝
        "summary": "检测到显著的跨平台价差，存在无风险搬砖机会。",
    },
    "STRONG_PREDICTIVE_BUY": {
        "label": "🔥 深度预测建仓 (STRONG_PREDICTIVE_BUY)",
        "color": "#9C27B0",  # 量化紫
        "summary": "AI 多因子模型预测出极高爆发概率，建议立刻关注！",
    },
    "IRREGULAR": {
        "label": "⚠️ 不明异动 (IRREGULAR)",
        "color": "#9E9E9E",  # 灰色
        "summary": "订单簿出现异动，但被量价或巨鲸模型判定为疑似洗盘/诱多。",
    },
    "NORMAL": {
        "label": "🟢 正常 (NORMAL)",
        "color": "#4CAF50",  # 绿色
        "summary": "当前市场微结构未见异常。",
    },
}

_DEFAULT_META: dict = {
    "label": "❓ 未知信号",
    "color": "#9E9E9E",
    "summary": "未知信号类型。",
}


def format_anomaly_alert(item_name: str, result: dict) -> dict:
    """Build a DingTalk Markdown payload dict from a detector result dict.

    Parameters
    ----------
    item_name : str
        Human-readable market hash name, e.g. ``"AK-47 | Redline (Field-Tested)"``.
    result : dict
        Dict returned by ``MarketAnomalyDetector.detect_anomalies()``.

    Returns
    -------
    dict
        A DingTalk robot message payload ready for ``json.dumps`` and POST.
    """
    signal_type: str = result.get("signal_type", "UNKNOWN")
    meta = _SIGNAL_META.get(signal_type, _DEFAULT_META)

    title = f"CS2 Market · {meta['label'].split(' ')[0]}"  # 提取 Emoji 作为通知栏标题首字符

    # 提取所有量化指标 (将其转为百分比展示，提升可读性)
    obi: float = result.get("obi", 0.0) * 100
    sdr: float = result.get("sdr", 0.0) * 100
    platform_spread: float = result.get("platform_spread", 0.0) * 100
    spread_ratio: float = result.get("spread_ratio", 0.0) * 100
    volatility: float = result.get("price_volatility", 0.0) * 100
    score: float = result.get("anomaly_score", float("nan"))
    timestamp: str = result.get("timestamp", "N/A")

    # 基础文案构建
    text = (
        f"## {meta['label']}\n\n"
        f"**饰品：** {item_name}\n\n"
        f"**时间：** {timestamp}\n\n"
        f"**摘要：** <font color=\"{meta['color']}\">{meta['summary']}</font>\n\n"
        "---\n\n"
        "### 📊 核心量化指标\n\n"
        f"| 指标 (Metrics) | 实时变动 |\n"
        f"|------|------|\n"
        f"| 供需突变 (OBI) | `{obi:+.2f}%` |\n"
        f"| 供应萎缩 (SDR) | `{sdr:+.2f}%` |\n"
        f"| 价格波动 (Spread) | `{spread_ratio:+.2f}%` |\n"
        f"| 价格变异度 (Volatility) | `{volatility:.2f}%` |\n"
        f"| 跨平台价差 (BUFF/YYYP)| `{platform_spread:+.2f}%` |\n"
        f"| 孤立森林 AI 异常分 | `{score:.3f}` |\n"
    )

    # V5.0 多因子预测专属面板
    if signal_type == "STRONG_PREDICTIVE_BUY" and "prediction" in result:
        pred = result["prediction"]
        factors = pred["factors"]
        prob = pred["probability"] * 100
        text += (
            "\n---\n\n"
            "### 🤖 AI 多因子预测面板\n\n"
            f"> **综合爆发胜率：<font color='#E62A10' size='4'>{prob:.1f}%</font>**\n>\n"
            f"> **AI 洞察：** {pred['insight_msg']}\n\n"
            f"| 预测因子 | 实时读数 |\n"
            f"|------|------|\n"
            f"| 🐋 巨鲸净流入 (Net Flow) | `{factors['whale_net_flow']:+d} 件` |\n"
            f"| 🔒 筹码锁死度 (Lock Rate) | `{factors['lock_rate'] * 100:.1f}%` |\n"
            f"| 📈 成交量倍率 (Vol Ratio) | `{factors['vol_ratio']:.2f}x` |\n"
            f"| ⚠️ 盘口异动值 (OBI Z-Score) | `{factors['obi_z']:.2f}σ` |\n"
        )

    # V4.0 巨鲸追踪专属情报拼接
    if signal_type == "WHALE_CONFIRMED_BUY" and "whale_msg" in result:
        text += (
            "\n---\n\n"
            "### 🐋 巨鲸微观追踪雷达\n\n"
            f"> **<font color=\"{meta['color']}\">{result['whale_msg']}</font>**\n"
        )

    return {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": text,
        },
    }