import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from config.settings import settings
from src.strategy.base import Signal
from src.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class PositionInfo:
    token_id: str
    side: str
    size: float  # number of shares
    entry_price: float
    current_price: float = 0.0
    peak_price: float = 0.0       # 보유 중 최고가 (BUY) / 최저가 (SELL)
    entry_time: float = 0.0       # 진입 시각 (unix timestamp)
    last_move_time: float = 0.0   # 마지막 의미있는 가격 변동 시각
    trailing_active: bool = False  # 트레일링 스탑 활성화 여부
    is_arbitrage: bool = False     # 차익거래 포지션 (청산 로직 제외)

    @property
    def pnl(self) -> float:
        if self.side == "BUY":
            return (self.current_price - self.entry_price) * self.size
        return (self.entry_price - self.current_price) * self.size

    @property
    def pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        if self.side == "BUY":
            return (self.current_price - self.entry_price) / self.entry_price
        return (self.entry_price - self.current_price) / self.entry_price

    @property
    def peak_pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        if self.side == "BUY":
            return (self.peak_price - self.entry_price) / self.entry_price
        return (self.entry_price - self.peak_price) / self.entry_price

    @property
    def drawdown_from_peak(self) -> float:
        """Current drawdown from peak profit (positive = losing from peak)."""
        return self.peak_pnl_pct - self.pnl_pct

    @property
    def hold_minutes(self) -> float:
        return (time.time() - self.entry_time) / 60.0

    @property
    def minutes_since_move(self) -> float:
        return (time.time() - self.last_move_time) / 60.0


class RiskManager:
    """Position sizing (Kelly) and risk limit enforcement."""

    def __init__(self) -> None:
        self.bankroll = settings.initial_bankroll
        self.max_position_pct = settings.max_position_pct
        self.kelly_multiplier = settings.kelly_multiplier
        self.min_ev = settings.min_ev_threshold
        self.max_daily_loss = settings.max_daily_loss
        self.max_open_positions = settings.max_open_positions

        self._positions: dict[str, PositionInfo] = {}
        self._daily_pnl: float = 0.0
        self._today: date = date.today()
        # Cooldown: token_id → timestamp of last exit
        self._exit_times: dict[str, float] = {}

    def kelly_fraction(self, market_price: float, estimated_prob: float) -> float:
        """Compute fractional Kelly bet size as a fraction of bankroll."""
        if not (0 < market_price < 1) or not (0 < estimated_prob < 1):
            return 0.0

        p = estimated_prob
        q = 1 - p
        b = (1 - market_price) / market_price

        full_kelly = (b * p - q) / b
        if full_kelly <= 0:
            return 0.0

        return full_kelly * self.kelly_multiplier

    def compute_bet_size(
        self, signal: Signal, execution_price: float | None = None
    ) -> float:
        """Compute dollar amount to bet based on Kelly + risk limits.

        Args:
            execution_price: actual execution price (e.g. best_ask).
                If provided, Kelly uses this instead of signal.market_price
                for more accurate sizing.
        """
        price_for_kelly = execution_price if execution_price else signal.market_price
        fraction = self.kelly_fraction(price_for_kelly, signal.estimated_prob)
        if fraction <= 0:
            return 0.0

        # Cap at max position percentage
        fraction = min(fraction, self.max_position_pct)

        return self.bankroll * fraction

    def can_trade(self, signal: Signal) -> tuple[bool, str]:
        """Check all risk limits. Returns (allowed, reason)."""
        self._reset_daily_if_needed()

        if signal.ev < self.min_ev:
            return False, f"EV too low: {signal.ev:.4f} < {self.min_ev}"

        if len(self._positions) >= self.max_open_positions:
            return False, f"Max positions reached: {len(self._positions)}"

        if self._daily_pnl <= -self.max_daily_loss:
            return False, f"Daily loss limit hit: {self._daily_pnl:.2f}"

        if signal.token_id in self._positions:
            return False, "Already have position in this market"

        # Cooldown: 최근 청산한 토큰 재진입 방지
        last_exit = self._exit_times.get(signal.token_id, 0)
        cooldown_sec = settings.cooldown_minutes * 60
        if time.time() - last_exit < cooldown_sec:
            remaining = cooldown_sec - (time.time() - last_exit)
            return False, f"Cooldown active ({remaining:.0f}s remaining)"

        # 가격대 필터: 0.40~0.60 구간 진입 회피
        if settings.avoid_mid_price_low <= signal.market_price <= settings.avoid_mid_price_high:
            return False, f"Mid-price zone ({signal.market_price:.2f})"

        bet_size = self.compute_bet_size(signal)
        if bet_size < 1.0:
            return False, f"Bet size too small: ${bet_size:.2f}"

        return True, "ok"

    def open_position(
        self,
        token_id: str,
        side: str,
        size: float,
        price: float,
        *,
        is_arbitrage: bool = False,
        skip_slippage: bool = False,
    ) -> None:
        now = time.time()
        # Live limit orders fill at exact price → skip slippage
        fill_price = price if skip_slippage else self._apply_slippage(price, side)
        self._positions[token_id] = PositionInfo(
            token_id=token_id,
            side=side,
            size=size,
            entry_price=fill_price,
            current_price=fill_price,
            peak_price=fill_price,
            entry_time=now,
            last_move_time=now,
            is_arbitrage=is_arbitrage,
        )
        cost = size * fill_price
        fee = cost * settings.trading_fee_pct
        self.bankroll -= (cost + fee)
        log.info(
            "position_opened",
            token_id=token_id[:16], side=side, size=size,
            quote=f"{price:.4f}", fill=f"{fill_price:.4f}", fee=f"{fee:.2f}",
        )

    def close_position(self, token_id: str, exit_price: float) -> float:
        pos = self._positions.pop(token_id, None)
        if not pos:
            return 0.0

        exit_side = "SELL" if pos.side == "BUY" else "BUY"
        fill_price = self._apply_slippage(exit_price, exit_side)
        pos.current_price = fill_price
        gross_proceeds = pos.size * fill_price
        fee = gross_proceeds * settings.trading_fee_pct
        net_proceeds = gross_proceeds - fee
        pnl = net_proceeds - (pos.size * pos.entry_price)
        self.bankroll += net_proceeds
        self._daily_pnl += pnl
        self._exit_times[token_id] = time.time()
        log.info(
            "position_closed",
            token_id=token_id[:16],
            quote=f"{exit_price:.4f}", fill=f"{fill_price:.4f}",
            pnl=f"{pnl:.2f}", fee=f"{fee:.2f}",
        )
        return pnl

    def update_position_price(self, token_id: str, price: float) -> None:
        pos = self._positions.get(token_id)
        if not pos:
            return
        old_price = pos.current_price
        pos.current_price = price

        # Update peak price
        if pos.side == "BUY":
            if price > pos.peak_price:
                pos.peak_price = price
        else:
            if price < pos.peak_price or pos.peak_price == 0:
                pos.peak_price = price

        # Track last meaningful price move (> 0.5% change)
        if old_price > 0 and abs(price - old_price) / old_price > 0.005:
            pos.last_move_time = time.time()

        # Activate trailing stop once breakeven trigger is hit
        if not pos.trailing_active and pos.pnl_pct >= settings.breakeven_trigger_pct:
            pos.trailing_active = True
            log.info("trailing_activated", token=token_id[:16], pnl_pct=f"{pos.pnl_pct:.4f}")

    def check_exits(self) -> list[tuple[str, str]]:
        """
        Check all positions for exit conditions.
        Returns list of (token_id, reason) pairs to close.
        """
        exits: list[tuple[str, str]] = []

        for token_id, pos in self._positions.items():
            if pos.is_arbitrage:
                continue  # 차익거래 포지션은 시장 결산 시 자동 청산
            reason = self._should_exit(pos)
            if reason:
                exits.append((token_id, reason))

        return exits

    def _should_exit(self, pos: PositionInfo) -> str | None:
        pnl_pct = pos.pnl_pct

        # 0. Price gap protection: 진입가 대비 큰 갭 발생 시 즉시 청산
        if pos.entry_price > 0:
            gap = abs(pos.current_price - pos.entry_price) / pos.entry_price
            if gap >= settings.max_price_gap_pct and pnl_pct < 0:
                return f"price_gap ({gap:.2%} from entry)"

        # 1. Stop loss: 손절
        if pnl_pct <= -settings.stop_loss_pct:
            return f"stop_loss ({pnl_pct:.2%})"

        # 2. Take profit: 익절
        if pnl_pct >= settings.take_profit_pct:
            return f"take_profit ({pnl_pct:.2%})"

        # 3. Trailing stop: 고점 대비 하락 시 청산 (활성화 이후만)
        if pos.trailing_active and pos.drawdown_from_peak >= settings.trailing_stop_pct:
            return f"trailing_stop (peak={pos.peak_pnl_pct:.2%}, now={pnl_pct:.2%})"

        # 4. Max hold time: 최대 보유 시간 초과
        if pos.hold_minutes >= settings.max_hold_minutes:
            return f"max_hold_time ({pos.hold_minutes:.0f}min)"

        # 5. Stale position: 장기간 가격 변동 없음
        if pos.minutes_since_move >= settings.stale_exit_minutes:
            return f"stale_position ({pos.minutes_since_move:.0f}min no move)"

        return None

    def get_positions(self) -> dict[str, PositionInfo]:
        return dict(self._positions)

    def get_total_pnl(self) -> float:
        return sum(p.pnl for p in self._positions.values()) + self._daily_pnl

    @staticmethod
    def _apply_slippage(price: float, side: str) -> float:
        """Apply slippage: worse price for both buy and sell."""
        slip = settings.slippage_pct
        if side == "BUY":
            return min(price * (1 + slip), 0.99)
        return max(price * (1 - slip), 0.01)

    def _reset_daily_if_needed(self) -> None:
        today = date.today()
        if today != self._today:
            self._daily_pnl = 0.0
            self._today = today

    # --- Position persistence ---

    _STATE_FILE = Path("logs/positions_state.json")

    def save_state(self) -> None:
        """Save current positions and bankroll to disk."""
        self._STATE_FILE.parent.mkdir(exist_ok=True)
        state = {
            "bankroll": self.bankroll,
            "daily_pnl": self._daily_pnl,
            "today": str(self._today),
            "positions": {tid: asdict(pos) for tid, pos in self._positions.items()},
            "exit_times": self._exit_times,
            "saved_at": time.time(),
        }
        self._STATE_FILE.write_text(json.dumps(state, indent=2))
        log.debug("state_saved", positions=len(self._positions))

    def load_state(self) -> int:
        """Load positions from disk. Returns number of restored positions."""
        if not self._STATE_FILE.exists():
            return 0

        try:
            state = json.loads(self._STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("state_load_failed", error=str(e))
            return 0

        self.bankroll = state.get("bankroll", self.bankroll)
        self._daily_pnl = state.get("daily_pnl", 0.0)
        saved_today = state.get("today", "")
        if saved_today != str(date.today()):
            self._daily_pnl = 0.0  # 날짜가 바뀌었으면 일일 손익 리셋

        self._exit_times = state.get("exit_times", {})

        positions = state.get("positions", {})
        for tid, pos_data in positions.items():
            self._positions[tid] = PositionInfo(**pos_data)

        log.info(
            "state_restored",
            positions=len(self._positions),
            bankroll=f"${self.bankroll:.2f}",
        )
        return len(self._positions)

    def clear_state_file(self) -> None:
        """Remove state file (called after clean shutdown)."""
        if self._STATE_FILE.exists():
            self._STATE_FILE.unlink()
            log.debug("state_file_removed")
