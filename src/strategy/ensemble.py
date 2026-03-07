from src.data.market_store import MarketStore
from src.data.price_history import PriceHistory
from src.strategy.arbitrage import ArbitrageSignal, ArbitrageStrategy
from src.strategy.base import Signal, Strategy
from src.strategy.momentum import MomentumStrategy
from src.strategy.orderbook_imbalance import OrderBookImbalanceStrategy
from src.utils.logger import get_logger

log = get_logger(__name__)


class EnsembleStrategy:
    """
    Combines signals from multiple strategies.

    - Orderbook imbalance and momentum generate directional signals.
    - Arbitrage generates separate buy-both signals.
    """

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        min_strength: float = 0.3,
    ) -> None:
        self.orderbook = OrderBookImbalanceStrategy()
        self.momentum = MomentumStrategy()
        self.arbitrage = ArbitrageStrategy()

        self.weights = weights or {
            "orderbook_imbalance": 0.5,
            "momentum": 0.5,
        }
        self.min_strength = min_strength

    def evaluate_directional(
        self,
        token_id: str,
        store: MarketStore,
        history: PriceHistory,
    ) -> Signal | None:
        """Combine orderbook + momentum for directional trading."""
        signals: list[Signal] = []

        for strategy in [self.orderbook, self.momentum]:
            sig = strategy.evaluate(token_id, store, history)
            if sig:
                signals.append(sig)

        if not signals:
            return None

        # All signals must agree on direction
        sides = {s.side for s in signals}
        if len(sides) > 1:
            return None

        side = signals[0].side
        weighted_strength = sum(
            s.strength * self.weights.get(s.strategy, 0.5) for s in signals
        )
        avg_ev = sum(s.ev for s in signals) / len(signals)
        avg_p_hat = sum(s.estimated_prob for s in signals) / len(signals)

        if weighted_strength < self.min_strength:
            return None

        return Signal(
            token_id=token_id,
            side=side,
            strength=min(weighted_strength, 1.0),
            strategy="ensemble",
            estimated_prob=avg_p_hat,
            market_price=signals[0].market_price,
            ev=avg_ev,
        )

    def find_arbitrage(self, store: MarketStore) -> list[ArbitrageSignal]:
        """Scan for arbitrage opportunities."""
        return self.arbitrage.find_arbitrage(store)
