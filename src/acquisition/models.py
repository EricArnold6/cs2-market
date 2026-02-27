"""Data models for CS2 market price records."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PriceRecord:
    """A single price data point for a CS2 item."""

    timestamp: float  # Unix timestamp
    price: float  # Price in CNY (or USD)
    volume: int  # Number of items sold at this price


@dataclass
class ItemHistory:
    """Historical price and volume data for a CS2 item."""

    item_name: str
    records: List[PriceRecord] = field(default_factory=list)

    @property
    def prices(self) -> List[float]:
        return [r.price for r in self.records]

    @property
    def volumes(self) -> List[int]:
        return [r.volume for r in self.records]

    @property
    def timestamps(self) -> List[float]:
        return [r.timestamp for r in self.records]


@dataclass
class TradeSignal:
    """A trading recommendation produced by the strategy."""

    item_name: str
    timestamp: float
    action: str  # "BUY", "SELL", or "HOLD"
    confidence: float  # 0.0 – 1.0
    reason: str
    price: Optional[float] = None


@dataclass
class OrderBook:
    """Steam 市场订单簿的单次清洗快照。"""
    item_name: str
    timestamp: int                      # UTC Unix 时间戳（整秒）
    lowest_ask_price: float             # 最低卖价（卖一）；无卖单时为 0.0
    highest_bid_price: float            # 最高买价（买一）；无买单时为 0.0
    ask_volume_top5_cumulative: int     # 卖方前5档累计挂单量
    bid_volume_top5_cumulative: int     # 买方前5档累计求购量
    total_buy_orders: int               # 全部买单总数
    total_sell_orders: int              # 全部卖单总数

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
