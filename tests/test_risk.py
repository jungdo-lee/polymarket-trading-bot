import time

import pytest

from src.execution.risk import RiskManager
from src.strategy.base import Signal


@pytest.fixture
def risk():
    rm = RiskManager()
    rm.bankroll = 1000.0
    rm.max_position_pct = 0.10
    rm.kelly_multiplier = 0.25
    rm.min_ev = 0.03
    rm.max_daily_loss = 100.0
    rm.max_open_positions = 5
    return rm


def _make_signal(ev=0.05, market_price=0.50, estimated_prob=0.55, token_id="tok1"):
    return Signal(
        token_id=token_id,
        side="BUY",
        strength=0.8,
        strategy="test",
        estimated_prob=estimated_prob,
        market_price=market_price,
        ev=ev,
    )


class TestKelly:
    def test_positive_edge(self, risk):
        f = risk.kelly_fraction(market_price=0.50, estimated_prob=0.60)
        assert f > 0

    def test_no_edge(self, risk):
        f = risk.kelly_fraction(market_price=0.50, estimated_prob=0.50)
        assert f == 0.0

    def test_negative_edge(self, risk):
        f = risk.kelly_fraction(market_price=0.60, estimated_prob=0.50)
        assert f == 0.0

    def test_fractional_kelly(self, risk):
        full = risk.kelly_fraction(0.40, 0.60)
        risk.kelly_multiplier = 0.5
        half = risk.kelly_fraction(0.40, 0.60)
        assert half == pytest.approx(full * 2, rel=0.01)  # multiplier was 0.25, now 0.5


class TestRiskLimits:
    def test_rejects_low_ev(self, risk):
        signal = _make_signal(ev=0.01)
        allowed, reason = risk.can_trade(signal)
        assert not allowed
        assert "EV too low" in reason

    def test_rejects_max_positions(self, risk):
        for i in range(5):
            risk.open_position(f"tok{i}", "BUY", 10, 0.5)
        signal = _make_signal(token_id="tok99")
        allowed, reason = risk.can_trade(signal)
        assert not allowed
        assert "Max positions" in reason

    def test_rejects_duplicate_position(self, risk):
        risk.open_position("tok1", "BUY", 10, 0.5)
        signal = _make_signal(token_id="tok1")
        allowed, reason = risk.can_trade(signal)
        assert not allowed
        assert "Already have" in reason

    def test_allows_valid_trade(self, risk):
        signal = _make_signal()
        allowed, reason = risk.can_trade(signal)
        assert allowed
        assert reason == "ok"

    def test_position_pnl(self, risk):
        risk.open_position("tok1", "BUY", 100, 0.50)
        pnl = risk.close_position("tok1", 0.60)
        assert pnl == pytest.approx(10.0)

    def test_daily_loss_limit(self, risk):
        risk._daily_pnl = -100.0
        signal = _make_signal(token_id="tok2")
        allowed, reason = risk.can_trade(signal)
        assert not allowed
        assert "Daily loss" in reason


class TestExitLogic:
    def test_stop_loss(self, risk):
        risk.open_position("tok1", "BUY", 100, 0.50)
        # Price drops 6% → triggers stop_loss (threshold 5%)
        risk.update_position_price("tok1", 0.47)
        exits = risk.check_exits()
        assert len(exits) == 1
        assert "stop_loss" in exits[0][1]

    def test_take_profit(self, risk):
        risk.open_position("tok1", "BUY", 100, 0.50)
        # Price rises 10% → triggers take_profit (threshold 8%)
        risk.update_position_price("tok1", 0.55)
        exits = risk.check_exits()
        assert len(exits) == 1
        assert "take_profit" in exits[0][1]

    def test_trailing_stop(self, risk):
        risk.open_position("tok1", "BUY", 100, 0.50)
        # Price rises to activate trailing (+4%)
        risk.update_position_price("tok1", 0.52)
        pos = risk.get_positions()["tok1"]
        assert pos.trailing_active is True

        # Price drops from peak (0.52) by more than 3%
        risk.update_position_price("tok1", 0.504)
        exits = risk.check_exits()
        assert len(exits) == 1
        assert "trailing_stop" in exits[0][1]

    def test_no_trailing_before_activation(self, risk):
        risk.open_position("tok1", "BUY", 100, 0.50)
        # Small profit, trailing not activated yet
        risk.update_position_price("tok1", 0.51)
        pos = risk.get_positions()["tok1"]
        assert pos.trailing_active is False
        exits = risk.check_exits()
        assert len(exits) == 0

    def test_max_hold_time(self, risk):
        risk.open_position("tok1", "BUY", 100, 0.50)
        # Simulate 61 minutes elapsed
        pos = risk._positions["tok1"]
        pos.entry_time = time.time() - 3660
        exits = risk.check_exits()
        assert len(exits) == 1
        assert "max_hold_time" in exits[0][1]

    def test_stale_position(self, risk):
        risk.open_position("tok1", "BUY", 100, 0.50)
        # Simulate 31 minutes since last price move
        pos = risk._positions["tok1"]
        pos.last_move_time = time.time() - 1860
        exits = risk.check_exits()
        assert len(exits) == 1
        assert "stale_position" in exits[0][1]

    def test_no_exit_when_normal(self, risk):
        risk.open_position("tok1", "BUY", 100, 0.50)
        risk.update_position_price("tok1", 0.51)  # small gain, no triggers
        exits = risk.check_exits()
        assert len(exits) == 0

    def test_arbitrage_skips_exit_checks(self, risk):
        """Arbitrage positions should not trigger directional exit conditions."""
        risk.open_position("arb_yes", "BUY", 100, 0.45, is_arbitrage=True)
        risk.open_position("arb_no", "BUY", 100, 0.48, is_arbitrage=True)
        # Even with stop-loss level drop, arbitrage positions are excluded
        risk.update_position_price("arb_yes", 0.30)
        exits = risk.check_exits()
        assert len(exits) == 0

    def test_arbitrage_counts_against_max_positions(self, risk):
        """Arbitrage positions should still count against max_open_positions."""
        for i in range(5):
            risk.open_position(f"arb{i}", "BUY", 10, 0.5, is_arbitrage=True)
        signal = _make_signal(token_id="tok99")
        allowed, reason = risk.can_trade(signal)
        assert not allowed
        assert "Max positions" in reason
