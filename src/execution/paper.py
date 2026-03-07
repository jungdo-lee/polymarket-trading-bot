import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.utils.logger import get_logger

log = get_logger(__name__)

LOGS_DIR = Path("logs")


@dataclass
class PaperTrade:
    timestamp: float
    token_id: str
    side: str
    price: float
    size: float
    strategy: str
    ev: float
    pnl: float = 0.0
    current_price: float = 0.0
    status: str = "open"  # open, closed


class PaperTrader:
    """Simulated trading engine for paper trading mode."""

    def __init__(self, initial_bankroll: float = 1000.0) -> None:
        self.initial_bankroll = initial_bankroll
        self.bankroll = initial_bankroll
        self._trades: list[PaperTrade] = []
        self._open_positions: dict[str, PaperTrade] = {}
        LOGS_DIR.mkdir(exist_ok=True)

    def execute_buy(
        self,
        token_id: str,
        price: float,
        size: float,
        strategy: str,
        ev: float,
    ) -> PaperTrade:
        cost = price * size
        self.bankroll -= cost

        trade = PaperTrade(
            timestamp=time.time(),
            token_id=token_id,
            side="BUY",
            price=price,
            size=size,
            strategy=strategy,
            ev=ev,
            current_price=price,
        )
        self._trades.append(trade)
        self._open_positions[token_id] = trade
        log.info(
            "paper_buy",
            token=token_id[:16],
            price=f"{price:.4f}",
            size=size,
            cost=f"${cost:.2f}",
            bankroll=f"${self.bankroll:.2f}",
        )
        return trade

    def execute_sell(self, token_id: str, price: float) -> float:
        trade = self._open_positions.pop(token_id, None)
        if not trade:
            return 0.0

        proceeds = price * trade.size
        pnl = (price - trade.price) * trade.size
        self.bankroll += proceeds

        trade.pnl = pnl
        trade.status = "closed"

        log.info(
            "paper_sell",
            token=token_id[:16],
            entry=f"{trade.price:.4f}",
            exit=f"{price:.4f}",
            pnl=f"${pnl:.2f}",
            bankroll=f"${self.bankroll:.2f}",
        )
        return pnl

    def get_open_positions(self) -> dict[str, PaperTrade]:
        return dict(self._open_positions)

    def update_position_price(self, token_id: str, price: float) -> None:
        """Update current price for an open position (for unrealized PnL)."""
        if token_id in self._open_positions:
            self._open_positions[token_id].current_price = price

    def get_unrealized_pnl(self) -> float:
        """Sum of unrealized PnL from all open positions."""
        return sum(
            (pos.current_price - pos.price) * pos.size
            for pos in self._open_positions.values()
            if pos.current_price > 0
        )

    def get_realized_pnl(self) -> float:
        """Sum of realized PnL from closed trades."""
        return sum(t.pnl for t in self._trades if t.status == "closed")

    def get_total_pnl(self) -> float:
        """Realized + unrealized PnL."""
        return self.get_realized_pnl() + self.get_unrealized_pnl()

    def get_summary(self) -> dict:
        closed = [t for t in self._trades if t.status == "closed"]
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        return {
            "initial_bankroll": self.initial_bankroll,
            "current_bankroll": round(self.bankroll, 2),
            "total_pnl": round(self.get_total_pnl(), 2),
            "realized_pnl": round(self.get_realized_pnl(), 2),
            "unrealized_pnl": round(self.get_unrealized_pnl(), 2),
            "total_trades": len(self._trades),
            "open_positions": len(self._open_positions),
            "closed_trades": len(closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(closed), 4) if closed else 0.0,
        }

    def save_history(self) -> Path:
        path = LOGS_DIR / f"paper_trades_{int(time.time())}.json"
        data = {
            "summary": self.get_summary(),
            "trades": [asdict(t) for t in self._trades],
        }
        path.write_text(json.dumps(data, indent=2))
        log.info("history_saved", path=str(path))
        return path
