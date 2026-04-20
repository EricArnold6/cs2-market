"""庄家筹码追踪预测引擎 (V4.0)"""

import logging

logger = logging.getLogger(__name__)


class WhaleTracker:
    def __init__(self, client, cfg: dict | None = None):
        self._client = client
        cfg = cfg or {}
        # 净流入超过此阈值 → STRONG_PREDICTIVE_BUY（前 10 大户累计净买入件数）
        self._buy_threshold  = cfg.get("net_flow_buy_threshold",  10)
        # 净流出低于此阈值（负数）→ PREDICTIVE_DUMP
        self._sell_threshold = cfg.get("net_flow_sell_threshold", -15)

    def calculate_accumulation_index(self, csqaq_id: int) -> dict:
        """计算指定饰品的巨鲸吸筹指数"""

        # 1. 获取前 10 大持仓庄家
        top_holders = self._client.fetch_whale_ranking(csqaq_id, limit=10)

        if not top_holders:
            return {"status": "UNKNOWN", "net_flow": 0, "confidence": 0.0}

        total_net_flow = 0
        total_active_vol = 0
        whale_count = len(top_holders)

        # 2. 遍历大户，统计他们最近的交易动作
        for holder in top_holders:
            # 接口返回的 id 其实就是获取动态所需的 task_id
            task_id = holder.get("id")
            if not task_id:
                continue

            dynamics = self._client.fetch_user_inventory_dynamics(task_id, csqaq_id)
            total_net_flow += dynamics.get("net_change", 0)
            total_active_vol += dynamics.get("active_volume", 0)

        logger.info(
            "[Whale Tracker] ID: %s | Top 10 Whales Net Flow: %+d | Active Vol: %d",
            csqaq_id, total_net_flow, total_active_vol
        )

        # 3. 预测逻辑判定
        # 如果大户整体极其活跃，且呈现单边净流入
        if total_net_flow >= self._buy_threshold:
            return {
                "status": "STRONG_PREDICTIVE_BUY",
                "net_flow": total_net_flow,
                "msg": f"🎯 [巨鲸觉醒] 监控到前十大户累计净吸筹 {total_net_flow} 件，高度控盘拉升预警！"
            }
        # 如果大户整体呈现明显的净流出（出货）
        elif total_net_flow <= self._sell_threshold:
            return {
                "status": "PREDICTIVE_DUMP",
                "net_flow": total_net_flow,
                "msg": f"🚨 [巨鲸砸盘] 危险！前十大户正在偷偷抛售（净流出 {abs(total_net_flow)} 件），大级别抛压即将来临！"
            }

        return {"status": "NEUTRAL", "net_flow": total_net_flow}