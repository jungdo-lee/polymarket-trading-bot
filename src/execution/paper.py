import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from config.settings import settings
from src.utils.logger import get_logger

log = get_logger(__name__)


def _apply_slippage(price: float, side: str) -> float:
    """Apply slippage: worse price for both buy and sell."""
    slip = settings.slippage_pct
    if side == "BUY":
        return min(price * (1 + slip), 0.99)  # 매수: 더 비싸게 체결
    return max(price * (1 - slip), 0.01)       # 매도: 더 싸게 체결


def _fee_cost(amount: float) -> float:
    """Calculate trading fee on a given dollar amount."""
    return amount * settings.trading_fee_pct

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
        fill_price = _apply_slippage(price, "BUY")
        cost = fill_price * size
        fee = _fee_cost(cost)
        total_cost = cost + fee
        self.bankroll -= total_cost

        trade = PaperTrade(
            timestamp=time.time(),
            token_id=token_id,
            side="BUY",
            price=fill_price,
            size=size,
            strategy=strategy,
            ev=ev,
            current_price=fill_price,
        )
        self._trades.append(trade)
        self._open_positions[token_id] = trade
        log.info(
            "paper_buy",
            token=token_id[:16],
            quote=f"{price:.4f}",
            fill=f"{fill_price:.4f}",
            size=size,
            cost=f"${total_cost:.2f}",
            fee=f"${fee:.2f}",
            bankroll=f"${self.bankroll:.2f}",
        )
        return trade

    def execute_sell(self, token_id: str, price: float) -> float:
        trade = self._open_positions.pop(token_id, None)
        if not trade:
            return 0.0

        fill_price = _apply_slippage(price, "SELL")
        gross_proceeds = fill_price * trade.size
        fee = _fee_cost(gross_proceeds)
        net_proceeds = gross_proceeds - fee
        pnl = net_proceeds - (trade.price * trade.size)
        self.bankroll += net_proceeds

        trade.pnl = pnl
        trade.status = "closed"

        log.info(
            "paper_sell",
            token=token_id[:16],
            entry=f"{trade.price:.4f}",
            quote=f"{price:.4f}",
            fill=f"{fill_price:.4f}",
            pnl=f"${pnl:.2f}",
            fee=f"${fee:.2f}",
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
        """Sum of unrealized PnL from all open positions (slippage+fee adjusted)."""
        total = 0.0
        for pos in self._open_positions.values():
            if pos.current_price <= 0:
                continue
            hypothetical_fill = _apply_slippage(pos.current_price, "SELL")
            gross = hypothetical_fill * pos.size
            fee = _fee_cost(gross)
            net = gross - fee
            total += net - (pos.price * pos.size)
        return total

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

    # --- Position persistence ---

    _STATE_FILE = LOGS_DIR / "paper_state.json"

    def save_state(self) -> None:
        """Save paper trader state to disk."""
        LOGS_DIR.mkdir(exist_ok=True)
        state = {
            "bankroll": self.bankroll,
            "initial_bankroll": self.initial_bankroll,
            "trades": [asdict(t) for t in self._trades],
            "open_positions": {tid: asdict(t) for tid, t in self._open_positions.items()},
            "saved_at": time.time(),
        }
        self._STATE_FILE.write_text(json.dumps(state, indent=2))
        log.debug("paper_state_saved", positions=len(self._open_positions))

    def load_state(self) -> int:
        """Load paper trader state from disk. Returns number of restored positions."""
        if not self._STATE_FILE.exists():
            return 0

        try:
            state = json.loads(self._STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("paper_state_load_failed", error=str(e))
            return 0

        self.bankroll = state.get("bankroll", self.bankroll)
        self.initial_bankroll = state.get("initial_bankroll", self.initial_bankroll)

        for t_data in state.get("trades", []):
            self._trades.append(PaperTrade(**t_data))

        for tid, pos_data in state.get("open_positions", {}).items():
            self._open_positions[tid] = PaperTrade(**pos_data)

        log.info(
            "paper_state_restored",
            positions=len(self._open_positions),
            bankroll=f"${self.bankroll:.2f}",
        )
        return len(self._open_positions)

    def clear_state_file(self) -> None:
        if self._STATE_FILE.exists():
            self._STATE_FILE.unlink()
