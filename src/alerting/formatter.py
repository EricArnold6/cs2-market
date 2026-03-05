"""DingTalk Markdown message builder for anomaly alerts.

All functions are pure (no network, no file I/O) so they are trivially
unit-testable without mocking.
"""

# Signal-type display metadata
_SIGNAL_META: dict[str, dict] = {
    "ACCUMULATION": {
        "label": "🔴 建仓扫货 (ACCUMULATION)",
        "color": "red",
        "summary": "检测到大量买单堆积，疑似盘主入场吸筹。",
    },
    "DUMP_RISK": {
        "label": "🟠 撤单砸盘风险 (DUMP_RISK)",
        "color": "orange",
        "summary": "买盘大幅萎缩且价差扩大，警惕盘主出货或撤单。",
    },
    "IRREGULAR": {
        "label": "🟡 异常波动 (IRREGULAR)",
        "color": "yellow",
        "summary": "订单簿出现不规则异常，暂未归类为已知模式。",
    },
    "NORMAL": {
        "label": "🟢 正常 (NORMAL)",
        "color": "green",
        "summary": "当前市场微结构未见异常。",
    },
}

_DEFAULT_META: dict = {
    "label": "⚪ 未知信号",
    "color": "gray",
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
        Expected keys: ``signal_type``, ``obi``, ``sdr``, ``spread_ratio``,
        ``price_momentum_dev``, ``anomaly_score``, ``timestamp``.

    Returns
    -------
    dict
        A DingTalk robot message payload ready for ``json.dumps`` and POST::

            {
                "msgtype": "markdown",
                "markdown": {"title": str, "text": str}
            }
    """
    signal_type: str = result.get("signal_type", "UNKNOWN")
    meta = _SIGNAL_META.get(signal_type, _DEFAULT_META)

    title = f"CS2 Market · {meta['label']}"

    obi: float = result.get("obi", float("nan"))
    sdr: float = result.get("sdr", float("nan"))
    spread_ratio: float = result.get("spread_ratio", float("nan"))
    pmd: float = result.get("price_momentum_dev", float("nan"))
    score: float = result.get("anomaly_score", float("nan"))
    timestamp: str = result.get("timestamp", "N/A")

    text = (
        f"## {meta['label']}\n\n"
        f"**饰品：** {item_name}\n\n"
        f"**时间：** {timestamp}\n\n"
        f"**摘要：** <font color=\"{meta['color']}\">{meta['summary']}</font>\n\n"
        "---\n\n"
        "### 📊 订单簿指标\n\n"
        f"| 指标 | 数值 |\n"
        f"|------|------|\n"
        f"| OBI（订单失衡） | `{obi:.4f}` |\n"
        f"| SDR（供应萎缩率） | `{sdr:.4f}` |\n"
        f"| 价差比率 | `{spread_ratio:.4f}` |\n"
        f"| 价格动量偏差 | `{pmd:.4f}` |\n"
        f"| 异常得分 | `{score:.4f}` |\n"
    )

    return {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": text,
        },
    }
