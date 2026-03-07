from abc import ABC, abstractmethod
from dataclasses import dataclass

from src.data.market_store import MarketStore
from src.data.price_history import PriceHistory


@dataclass
class Signal:
    token_id: str
    side: str  # "BUY" or "SELL"
    strength: float  # 0.0 to 1.0
    strategy: str  # strategy name
    estimated_prob: float  # p̂
    market_price: float  # p
    ev: float  # p̂ - p

    @property
    def is_buy(self) -> bool:
        return self.side == "BUY"


class Strategy(ABC):
    """Base class for trading strategies."""

    name: str = "base"

    @abstractmethod
    def evaluate(
        self,
        token_id: str,
        store: MarketStore,
        history: PriceHistory,
    ) -> Signal | None:
        """Evaluate a market and return a Signal if there's an opportunity."""
