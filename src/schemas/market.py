"""全系统共享的盘口数据契约（single source of truth）。

字段名与 order_book_snapshots 数据库列名一一对应，消灭
acquisition ↔ storage ↔ analysis 三层之间的字段名漂移。
"""

from dataclasses import dataclass


@dataclass
class OrderBookSnapshot:
    """全系统共享的盘口快照——唯一数据契约（single source of truth）。

    字段名与 order_book_snapshots 数据库列名一一对应，
    从而消灭 acquisition ↔ storage ↔ analysis 三层之间的字段名漂移。
    """

    item_name: str          # 人类可读的 market_hash_name
    timestamp: int          # UTC Unix 时间戳（整秒）
    lowest_ask_price: float # 最低卖价；无卖单时为 0.0
    highest_bid_price: float# 最高买价；无买单时为 0.0
    ask_volume_top5: int    # 卖方前5档累计挂单量（对齐 DB 列名）
    bid_volume_top5: int    # 买方前5档累计求购量（对齐 DB 列名）
    total_sell_orders: int  # 全部卖单总数
    total_buy_orders: int   # 全部买单总数

    # ── 内聚计算属性 ─────────────────────────────────────────────────────────

    @property
    def spread(self) -> float:
        """买卖价差（绝对值）。任一侧为空时返回 0.0。"""
        if self.lowest_ask_price == 0.0 or self.highest_bid_price == 0.0:
            return 0.0
        return round(self.lowest_ask_price - self.highest_bid_price, 4)

    @property
    def mid_price(self) -> float:
        """买卖中间价。任一侧为空时返回 0.0。"""
        if self.lowest_ask_price == 0.0 or self.highest_bid_price == 0.0:
            return 0.0
        return round((self.lowest_ask_price + self.highest_bid_price) / 2.0, 4)

    @property
    def spread_ratio(self) -> float:
        """价差率 = (ask − bid) / bid。bid 为 0 时返回 0.0。"""
        if self.highest_bid_price == 0.0:
            return 0.0
        return (self.lowest_ask_price - self.highest_bid_price) / self.highest_bid_price
