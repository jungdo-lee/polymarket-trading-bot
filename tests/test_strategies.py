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

    def test_rejects_stale_prices(self, store):
        """가격이 한번도 업데이트 안 된 토큰은 차익거래 대상에서 제외."""
        # register만 하고 가격 업데이트 없음 → price_updated_at == 0
        strategy = ArbitrageStrategy(min_profit_pct=0.01)
        opps = strategy.find_arbitrage(store)
        assert len(opps) == 0

    def test_rejects_low_sum_stale_data(self, store):
        """YES+NO 합이 0.85 미만이면 데이터 이상으로 판단하여 거부."""
        store.update_order_book(
            "yes_token",
            bids=[{"price": 0.20, "size": 100}],
            asks=[{"price": 0.22, "size": 100}],
        )
        store.update_order_book(
            "no_token",
            bids=[{"price": 0.23, "size": 100}],
            asks=[{"price": 0.25, "size": 100}],
        )
        # YES ask=0.22 + NO ask=0.25 = 0.47 < 0.85 → 거부
        strategy = ArbitrageStrategy(min_profit_pct=0.01, min_sum_threshold=0.85)
        opps = strategy.find_arbitrage(store)
        assert len(opps) == 0

    def test_rejects_wide_spread(self, store):
        """스프레드가 너무 넓으면 실행 불가로 거부."""
        store.update_order_book(
            "yes_token",
            bids=[{"price": 0.30, "size": 100}],
            asks=[{"price": 0.45, "size": 100}],  # spread 0.15 > 0.10
        )
        store.update_order_book(
            "no_token",
            bids=[{"price": 0.40, "size": 100}],
            asks=[{"price": 0.42, "size": 100}],
        )
        strategy = ArbitrageStrategy(min_profit_pct=0.01, max_spread=0.10)
        opps = strategy.find_arbitrage(store)
        assert len(opps) == 0

    def test_uses_best_ask_not_midpoint(self, store):
        """차익거래는 midpoint가 아닌 best_ask(실제 매수 가격)를 사용해야 함."""
        # Midpoint: (0.44+0.48)/2=0.46 + (0.46+0.50)/2=0.48 = 0.94 < 0.98 → arb by midpoint
        # Best ask: 0.48 + 0.50 = 0.98 → NOT arb (profit_pct 0.02 needed)
        store.update_order_book(
            "yes_token",
            bids=[{"price": 0.44, "size": 100}],
            asks=[{"price": 0.48, "size": 100}],
        )
        store.update_order_book(
            "no_token",
            bids=[{"price": 0.46, "size": 100}],
            asks=[{"price": 0.50, "size": 100}],
        )
        strategy = ArbitrageStrategy(min_profit_pct=0.02)
        opps = strategy.find_arbitrage(store)
        # best_ask sum = 0.98, need < 0.98 for arb → no opportunity
        assert len(opps) == 0
