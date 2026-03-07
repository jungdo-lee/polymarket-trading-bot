import time
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
        max_price_age_sec: float = 60.0,
        min_sum_threshold: float = 0.85,
        max_spread: float = 0.10,
    ) -> None:
        self.min_profit_pct = min_profit_pct
        self.fee_rate = fee_rate
        self.max_price_age_sec = max_price_age_sec
        self.min_sum_threshold = min_sum_threshold
        self.max_spread = max_spread

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

            # Validation 1: 가격 갱신 여부 — 한번도 업데이트 안 된 토큰 제외
            if yes_data.price_updated_at == 0 or no_data.price_updated_at == 0:
                continue

            # Validation 2: 가격 신선도 — 오래된 가격은 의미 없음
            now = time.time()
            if (now - yes_data.price_updated_at > self.max_price_age_sec
                    or now - no_data.price_updated_at > self.max_price_age_sec):
                continue

            # 실제 매수 가격 = best_ask (midpoint가 아닌 실행 가능 가격)
            yes_ask = yes_data.order_book.best_ask
            no_ask = no_data.order_book.best_ask

            # best_ask가 없으면 midpoint 폴백 (REST API 초기 시딩 경우)
            yes_price = yes_ask if yes_ask > 0 else yes_data.price
            no_price = no_ask if no_ask > 0 else no_data.price

            if yes_price <= 0.01 or no_price <= 0.01:
                continue

            # Validation 3: 스프레드 과다 — 넓은 스프레드 시장은 실행 불가
            if (yes_data.order_book.spread > self.max_spread
                    or no_data.order_book.spread > self.max_spread):
                log.debug(
                    "arb_skip_wide_spread",
                    condition_id=condition_id[:16],
                    yes_spread=f"{yes_data.order_book.spread:.4f}",
                    no_spread=f"{no_data.order_book.spread:.4f}",
                )
                continue

            total_cost = yes_price + no_price

            # Validation 4: 합계 하한선 — YES+NO가 0.85 미만이면 데이터 이상
            if total_cost < self.min_sum_threshold:
                log.warning(
                    "arb_rejected_stale_data",
                    condition_id=condition_id[:16],
                    yes_price=f"{yes_price:.4f}",
                    no_price=f"{no_price:.4f}",
                    total=f"{total_cost:.4f}",
                )
                continue

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
