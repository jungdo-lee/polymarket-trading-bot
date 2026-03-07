import pytest

from src.execution.paper import PaperTrader


@pytest.fixture
def paper():
    return PaperTrader(initial_bankroll=1000.0)


class TestPaperTrader:
    def test_buy_reduces_bankroll(self, paper):
        paper.execute_buy("tok1", price=0.50, size=100, strategy="test", ev=0.05)
        assert paper.bankroll == pytest.approx(950.0)

    def test_sell_calculates_pnl(self, paper):
        paper.execute_buy("tok1", price=0.50, size=100, strategy="test", ev=0.05)
        pnl = paper.execute_sell("tok1", price=0.60)
        assert pnl == pytest.approx(10.0)
        assert paper.bankroll == pytest.approx(1010.0)

    def test_sell_loss(self, paper):
        paper.execute_buy("tok1", price=0.50, size=100, strategy="test", ev=0.05)
        pnl = paper.execute_sell("tok1", price=0.40)
        assert pnl == pytest.approx(-10.0)
        assert paper.bankroll == pytest.approx(990.0)

    def test_sell_nonexistent_position(self, paper):
        pnl = paper.execute_sell("nonexistent", price=0.50)
        assert pnl == 0.0

    def test_summary(self, paper):
        paper.execute_buy("tok1", price=0.50, size=100, strategy="test", ev=0.05)
        paper.execute_sell("tok1", price=0.60)
        paper.execute_buy("tok2", price=0.40, size=50, strategy="test", ev=0.03)

        summary = paper.get_summary()
        assert summary["total_trades"] == 2  # sell closes existing trade, doesn't create new one
        assert summary["closed_trades"] == 1
        assert summary["open_positions"] == 1
        assert summary["wins"] == 1

    def test_total_pnl(self, paper):
        paper.execute_buy("tok1", price=0.50, size=100, strategy="test", ev=0.05)
        paper.execute_sell("tok1", price=0.60)
        assert paper.get_total_pnl() == pytest.approx(10.0)

    def test_unrealized_pnl_included(self, paper):
        """Open positions should contribute unrealized PnL, not show as losses."""
        paper.execute_buy("tok1", price=0.50, size=100, strategy="test", ev=0.05)
        # bankroll dropped by 50, but we hold 100 shares worth 0.50 each
        assert paper.bankroll == pytest.approx(950.0)
        # With current_price == entry_price, unrealized PnL = 0
        assert paper.get_total_pnl() == pytest.approx(0.0)

        # Price goes up → unrealized gain
        paper.update_position_price("tok1", 0.55)
        assert paper.get_unrealized_pnl() == pytest.approx(5.0)
        assert paper.get_total_pnl() == pytest.approx(5.0)

    def test_summary_shows_realized_unrealized(self, paper):
        paper.execute_buy("tok1", price=0.50, size=100, strategy="test", ev=0.05)
        paper.execute_sell("tok1", price=0.60)  # realized +10
        paper.execute_buy("tok2", price=0.40, size=50, strategy="test", ev=0.03)
        paper.update_position_price("tok2", 0.45)  # unrealized +2.5

        summary = paper.get_summary()
        assert summary["realized_pnl"] == pytest.approx(10.0)
        assert summary["unrealized_pnl"] == pytest.approx(2.5)
        assert summary["total_pnl"] == pytest.approx(12.5)
