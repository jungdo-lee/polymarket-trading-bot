from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
from ta.volatility import BollingerBands

from src.data.market_store import MarketStore
from src.data.price_history import PriceHistory
from src.strategy.base import Signal, Strategy


class MomentumStrategy(Strategy):
    """
    Strategy 2: Technical indicator momentum.

    Uses RSI, Bollinger Bands, and EMA crossover.
    Requires consensus (2/3 indicators agree) for a signal.
    """

    name = "momentum"

    def __init__(
        self,
        rsi_window: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
        bb_window: int = 20,
        bb_dev: int = 2,
        ema_fast: int = 12,
        ema_slow: int = 26,
        max_adjustment: float = 0.06,
        min_data_points: int = 30,
    ) -> None:
        self.rsi_window = rsi_window
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.bb_window = bb_window
        self.bb_dev = bb_dev
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.max_adjustment = max_adjustment
        self.min_data_points = min_data_points

    def evaluate(
        self,
        token_id: str,
        store: MarketStore,
        history: PriceHistory,
    ) -> Signal | None:
        if not history.has_enough_data(token_id, self.min_data_points):
            return None

        prices = history.get_prices(token_id)
        data = store.get(token_id)
        if not data or data.price <= 0 or data.price >= 1:
            return None

        market_price = data.price
        buy_votes = 0
        sell_votes = 0

        # RSI
        rsi_val = RSIIndicator(close=prices, window=self.rsi_window).rsi().iloc[-1]
        if rsi_val < self.rsi_oversold:
            buy_votes += 1
        elif rsi_val > self.rsi_overbought:
            sell_votes += 1

        # Bollinger Bands
        bb = BollingerBands(close=prices, window=self.bb_window, window_dev=self.bb_dev)
        current_price = prices.iloc[-1]
        if current_price < bb.bollinger_lband().iloc[-1]:
            buy_votes += 1
        elif current_price > bb.bollinger_hband().iloc[-1]:
            sell_votes += 1

        # EMA crossover
        if len(prices) >= self.ema_slow:
            ema_fast_val = EMAIndicator(close=prices, window=self.ema_fast).ema_indicator().iloc[-1]
            ema_slow_val = EMAIndicator(close=prices, window=self.ema_slow).ema_indicator().iloc[-1]
            if ema_fast_val > ema_slow_val:
                buy_votes += 1
            elif ema_fast_val < ema_slow_val:
                sell_votes += 1

        # Consensus: 2/3 agree
        if buy_votes >= 2:
            strength = buy_votes / 3.0
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
        elif sell_votes >= 2:
            strength = sell_votes / 3.0
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
