"""System-wide data contract for CSQAQ/BUFF market snapshots."""

from dataclasses import dataclass


@dataclass
class OrderBookSnapshot:
    """A single point-in-time snapshot of the macro order book."""

    item_name: str
    timestamp: float          # Unix timestamp
    lowest_ask_price: float   # BUFF 卖一价 (buff_sell_price)
    highest_bid_price: float  # BUFF 买一价 (buff_buy_price)
    total_sell_orders: int    # BUFF 在售总数 (buff_sell_num)
    total_buy_orders: int     # BUFF 求购总数 (buff_buy_num)
    # --- 新增字段 ---
    yyyp_sell_price: float = 0.0  # YYYP 售价
    yyyp_lease_price: float = 0.0  # YYYP 日租金

    @property
    def spread(self) -> float:
        """Absolute bid-ask spread."""
        if self.lowest_ask_price == 0.0 or self.highest_bid_price == 0.0:
            return 0.0
        return self.lowest_ask_price - self.highest_bid_price

    @property
    def mid_price(self) -> float:
        """Mid-point price between bid and ask."""
        if self.lowest_ask_price == 0.0 or self.highest_bid_price == 0.0:
            return max(self.lowest_ask_price, self.highest_bid_price)
        return (self.lowest_ask_price + self.highest_bid_price) / 2.0

    @property
    def spread_ratio(self) -> float:
        """Relative bid-ask spread."""
        if self.highest_bid_price == 0.0:
            return 0.0
        return (self.lowest_ask_price - self.highest_bid_price) / self.highest_bid_price