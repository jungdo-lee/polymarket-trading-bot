from src.data.market_store import MarketStore
from src.data.price_history import PriceHistory
from src.strategy.base import Signal, Strategy


class OrderBookImbalanceStrategy(Strategy):
    """
    Strategy 1: Order book imbalance.

    Measures bid/ask volume ratio at top N levels.
    Large imbalance suggests directional pressure.
    """

    name = "orderbook_imbalance"

    def __init__(
        self,
        depth: int = 5,
        threshold: float = 0.3,
        max_adjustment: float = 0.08,
    ) -> None:
        self.depth = depth
        self.threshold = threshold
        self.max_adjustment = max_adjustment

    def evaluate(
        self,
        token_id: str,
        store: MarketStore,
        history: PriceHistory,
    ) -> Signal | None:
        data = store.get(token_id)
        if not data:
            return None

        book = data.order_book
        if not book.bids or not book.asks:
            return None

        bid_vol = sum(b["size"] for b in book.bids[: self.depth])
        ask_vol = sum(a["size"] for a in book.asks[: self.depth])
        total = bid_vol + ask_vol

        if total == 0:
            return None

        imbalance = (bid_vol - ask_vol) / total
        market_price = data.price

        if market_price <= 0 or market_price >= 1:
            return None

        if imbalance > self.threshold:
            strength = min(abs(imbalance), 1.0)
            scaled_adj = self.max_adjustment * strength
            p_hat = min(market_price + scaled_adj, 0.99)
            ev = p_hat - market_price
            return Signal(
                token_id=token_id,
                side="BUY",
                strength=strength,
                strategy=self.name,
                estimated_prob=p_hat,
                market_price=market_price,
                ev=ev,
            )
        elif imbalance < -self.threshold:
            strength = min(abs(imbalance), 1.0)
            scaled_adj = self.max_adjustment * strength
            p_hat = max(market_price - scaled_adj, 0.01)
            ev = market_price - p_hat
            return Signal(
                token_id=token_id,
                side="SELL",
                strength=strength,
                strategy=self.name,
                estimated_prob=p_hat,
                market_price=market_price,
                ev=ev,
            )
        return None
