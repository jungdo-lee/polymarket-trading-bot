from dataclasses import dataclass

from src.data.market_store import MarketStore
from src.data.price_history import PriceHistory
from src.strategy.base import Signal, Strategy
from src.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class ArbitrageSignal:
    """Special signal for buying both YES and NO tokens."""

    condition_id: str
    yes_token_id: str
    no_token_id: str
    yes_price: float
    no_price: float
    total_cost: float
    guaranteed_profit: float
    profit_pct: float


class ArbitrageStrategy(Strategy):
    """
    Strategy 3: YES + NO arbitrage.

    When YES_price + NO_price < 1.0 (minus fees),
    buying both guarantees a risk-free profit.
    """

    name = "arbitrage"

    def __init__(
        self,
        min_profit_pct: float = 0.02,
        fee_rate: float = 0.0,
    ) -> None:
        self.min_profit_pct = min_profit_pct
        self.fee_rate = fee_rate

    def evaluate(
        self,
        token_id: str,
        store: MarketStore,
        history: PriceHistory,
    ) -> Signal | None:
        # This strategy doesn't return a standard Signal.
        # Use find_arbitrage() instead.
        return None

    def find_arbitrage(self, store: MarketStore) -> list[ArbitrageSignal]:
        """Scan all registered markets for YES+NO arbitrage opportunities."""
        opportunities: list[ArbitrageSignal] = []

        for condition_id, token_map in store._token_map.items():
            if len(token_map) != 2:
                continue

            outcomes = list(token_map.keys())
            yes_key = next((k for k in outcomes if k.lower() == "yes"), outcomes[0])
            no_key = next((k for k in outcomes if k.lower() == "no"), outcomes[1])

            yes_data = store.get(token_map[yes_key])
            no_data = store.get(token_map[no_key])

            if not yes_data or not no_data:
                continue

            yes_price = yes_data.price
            no_price = no_data.price

            if yes_price <= 0 or no_price <= 0:
                continue

            total_cost = yes_price + no_price
            fee_adjusted = total_cost * (1 + self.fee_rate)

            if fee_adjusted < (1.0 - self.min_profit_pct):
                profit = 1.0 - fee_adjusted
                profit_pct = profit / fee_adjusted

                opportunities.append(
                    ArbitrageSignal(
                        condition_id=condition_id,
                        yes_token_id=token_map[yes_key],
                        no_token_id=token_map[no_key],
                        yes_price=yes_price,
                        no_price=no_price,
                        total_cost=fee_adjusted,
                        guaranteed_profit=profit,
                        profit_pct=profit_pct,
                    )
                )

        return sorted(opportunities, key=lambda x: x.profit_pct, reverse=True)
