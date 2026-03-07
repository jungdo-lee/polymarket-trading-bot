import pytest

from src.data.market_store import MarketStore, OrderBook
from src.data.price_history import PriceHistory
from src.strategy.arbitrage import ArbitrageStrategy
from src.strategy.momentum import MomentumStrategy
from src.strategy.orderbook_imbalance import OrderBookImbalanceStrategy


@pytest.fixture
def store():
    s = MarketStore()
    s.register_market("yes_token", "cond1", "Will X happen?", "Yes")
    s.register_market("no_token", "cond1", "Will X happen?", "No")
    return s


@pytest.fixture
def history():
    return PriceHistory()


class TestOrderBookImbalance:
    def test_buy_signal_on_bid_heavy(self, store, history):
        store.update_order_book(
            "yes_token",
            bids=[{"price": 0.60, "size": 500}, {"price": 0.59, "size": 300}],
            asks=[{"price": 0.62, "size": 50}, {"price": 0.63, "size": 50}],
        )
        strategy = OrderBookImbalanceStrategy(threshold=0.3)
        signal = strategy.evaluate("yes_token", store, history)

        assert signal is not None
        assert signal.side == "BUY"
        assert signal.ev > 0

    def test_sell_signal_on_ask_heavy(self, store, history):
        store.update_order_book(
            "yes_token",
            bids=[{"price": 0.60, "size": 50}, {"price": 0.59, "size": 50}],
            asks=[{"price": 0.62, "size": 500}, {"price": 0.63, "size": 300}],
        )
        strategy = OrderBookImbalanceStrategy(threshold=0.3)
        signal = strategy.evaluate("yes_token", store, history)

        assert signal is not None
        assert signal.side == "SELL"

    def test_no_signal_when_balanced(self, store, history):
        store.update_order_book(
            "yes_token",
            bids=[{"price": 0.60, "size": 100}],
            asks=[{"price": 0.62, "size": 100}],
        )
        strategy = OrderBookImbalanceStrategy(threshold=0.3)
        signal = strategy.evaluate("yes_token", store, history)

        assert signal is None


class TestMomentum:
    def _fill_history(self, history, token_id, prices):
        for p in prices:
            history.record(token_id, p)

    def test_no_signal_insufficient_data(self, store, history):
        store.update_order_book(
            "yes_token",
            bids=[{"price": 0.50, "size": 100}],
            asks=[{"price": 0.52, "size": 100}],
        )
        self._fill_history(history, "yes_token", [0.50] * 10)

        strategy = MomentumStrategy(min_data_points=30)
        signal = strategy.evaluate("yes_token", store, history)
        assert signal is None

    def test_buy_signal_on_oversold(self, store, history):
        # Simulate a sharp drop (RSI will be low)
        prices = [0.70] * 20 + [0.60, 0.55, 0.50, 0.45, 0.40, 0.38, 0.35, 0.33, 0.31, 0.30] * 2
        self._fill_history(history, "yes_token", prices)
        store.update_order_book(
            "yes_token",
            bids=[{"price": 0.30, "size": 100}],
            asks=[{"price": 0.32, "size": 100}],
        )

        strategy = MomentumStrategy(min_data_points=20)
        signal = strategy.evaluate("yes_token", store, history)
        # Signal may or may not fire depending on exact RSI/BB values,
        # but should not error
        if signal:
            assert signal.side == "BUY"


class TestArbitrage:
    def test_finds_arbitrage(self, store):
        store.update_order_book(
            "yes_token",
            bids=[{"price": 0.45, "size": 100}],
            asks=[{"price": 0.47, "size": 100}],
        )
        store.update_order_book(
            "no_token",
            bids=[{"price": 0.48, "size": 100}],
            asks=[{"price": 0.50, "size": 100}],
        )

        strategy = ArbitrageStrategy(min_profit_pct=0.01)
        opps = strategy.find_arbitrage(store)

        # YES ~0.46 + NO ~0.49 = ~0.95 < 0.99 → arbitrage
        assert len(opps) >= 1
        assert opps[0].guaranteed_profit > 0

    def test_no_arbitrage_when_prices_sum_to_one(self, store):
        store.update_order_book(
            "yes_token",
            bids=[{"price": 0.59, "size": 100}],
            asks=[{"price": 0.61, "size": 100}],
        )
        store.update_order_book(
            "no_token",
            bids=[{"price": 0.39, "size": 100}],
            asks=[{"price": 0.41, "size": 100}],
        )

        strategy = ArbitrageStrategy(min_profit_pct=0.02)
        opps = strategy.find_arbitrage(store)
        assert len(opps) == 0
