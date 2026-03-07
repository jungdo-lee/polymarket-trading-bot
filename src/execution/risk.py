import time
from dataclasses import dataclass, field
from datetime import date

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

    def compute_bet_size(self, signal: Signal) -> float:
        """Compute dollar amount to bet based on Kelly + risk limits."""
        fraction = self.kelly_fraction(signal.market_price, signal.estimated_prob)
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

        bet_size = self.compute_bet_size(signal)
        if bet_size < 1.0:
            return False, f"Bet size too small: ${bet_size:.2f}"

        return True, "ok"

    def open_position(
        self, token_id: str, side: str, size: float, price: float, *, is_arbitrage: bool = False
    ) -> None:
        now = time.time()
        self._positions[token_id] = PositionInfo(
            token_id=token_id,
            side=side,
            size=size,
            entry_price=price,
            current_price=price,
            peak_price=price,
            entry_time=now,
            last_move_time=now,
            is_arbitrage=is_arbitrage,
        )
        cost = size * price
        self.bankroll -= cost
        log.info("position_opened", token_id=token_id[:16], side=side, size=size, price=price)

    def close_position(self, token_id: str, exit_price: float) -> float:
        pos = self._positions.pop(token_id, None)
        if not pos:
            return 0.0

        pos.current_price = exit_price
        pnl = pos.pnl
        proceeds = pos.size * exit_price
        self.bankroll += proceeds
        self._daily_pnl += pnl
        log.info("position_closed", token_id=token_id[:16], pnl=f"{pnl:.2f}")
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

    def _reset_daily_if_needed(self) -> None:
        today = date.today()
        if today != self._today:
            self._daily_pnl = 0.0
            self._today = today
