"""Data models for CS2 market price records."""

from dataclasses import dataclass, field
from typing import List, Optional

# 向后兼容别名：OrderBook 现已迁移到全局契约 src.schemas.market.OrderBookSnapshot
from src.schemas.market import OrderBookSnapshot as OrderBook  # noqa: F401


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


