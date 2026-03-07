from config.settings import settings
from src.client.clob import PolymarketClient
from src.execution.paper import PaperTrader
from src.execution.risk import RiskManager
from src.strategy.arbitrage import ArbitrageSignal
from src.strategy.base import Signal
from src.utils.logger import get_logger

log = get_logger(__name__)


class Trader:
    """
    Unified trade executor.

    Routes to PaperTrader or PolymarketClient based on PAPER_MODE.
    """

    def __init__(self, clob_client: PolymarketClient | None = None) -> None:
        self.risk = RiskManager()
        self.paper = PaperTrader(initial_bankroll=settings.initial_bankroll)
        self._clob = clob_client
        self._paper_mode = settings.paper_mode

        if self._paper_mode:
            log.info("trader_mode", mode="PAPER")
        else:
            log.info("trader_mode", mode="LIVE")

    def execute_signal(self, signal: Signal) -> bool:
        """Execute a directional signal if risk checks pass."""
        # SELL signal → close existing position (prediction markets don't support shorting)
        if signal.side == "SELL":
            return self._handle_sell_signal(signal)

        allowed, reason = self.risk.can_trade(signal)
        if not allowed:
            log.debug("trade_rejected", reason=reason, token=signal.token_id[:16])
            return False

        bet_size = self.risk.compute_bet_size(signal)
        shares = bet_size / signal.market_price

        if self._paper_mode:
            self.paper.execute_buy(
                token_id=signal.token_id,
                price=signal.market_price,
                size=shares,
                strategy=signal.strategy,
                ev=signal.ev,
            )
        else:
            self._clob.place_limit_order(
                token_id=signal.token_id,
                price=signal.market_price,
                size=shares,
                side="BUY",
            )

        self.risk.open_position(
            token_id=signal.token_id,
            side=signal.side,
            size=shares,
            price=signal.market_price,
        )

        log.info(
            "trade_executed",
            strategy=signal.strategy,
            side=signal.side,
            token=signal.token_id[:16],
            price=f"{signal.market_price:.4f}",
            size=f"{shares:.2f}",
            ev=f"{signal.ev:.4f}",
        )
        return True

    def _handle_sell_signal(self, signal: Signal) -> bool:
        """SELL signal triggers early exit of existing long position."""
        positions = self.risk.get_positions()
        if signal.token_id not in positions:
            log.debug("sell_skipped", reason="no_position", token=signal.token_id[:16])
            return False

        pos = positions[signal.token_id]
        if pos.is_arbitrage:
            log.debug("sell_skipped", reason="arbitrage_position", token=signal.token_id[:16])
            return False

        exit_price = signal.market_price

        if self._paper_mode:
            self.paper.execute_sell(signal.token_id, exit_price)
        else:
            self._clob.place_market_order(
                token_id=signal.token_id,
                amount=pos.size * exit_price,
                side="SELL",
            )

        pnl = self.risk.close_position(signal.token_id, exit_price)
        log.info(
            "sell_signal_exit",
            token=signal.token_id[:16],
            strategy=signal.strategy,
            price=f"{exit_price:.4f}",
            pnl=f"${pnl:.2f}",
        )
        return True

    def execute_arbitrage(self, arb: ArbitrageSignal, bet_amount: float) -> bool:
        """Execute a YES+NO arbitrage trade."""
        yes_shares = bet_amount / arb.yes_price
        no_shares = bet_amount / arb.no_price

        if self._paper_mode:
            self.paper.execute_buy(
                token_id=arb.yes_token_id,
                price=arb.yes_price,
                size=yes_shares,
                strategy="arbitrage",
                ev=arb.guaranteed_profit,
            )
            self.paper.execute_buy(
                token_id=arb.no_token_id,
                price=arb.no_price,
                size=no_shares,
                strategy="arbitrage",
                ev=arb.guaranteed_profit,
            )
        else:
            self._clob.place_limit_order(
                token_id=arb.yes_token_id,
                price=arb.yes_price,
                size=yes_shares,
                side="BUY",
            )
            self._clob.place_limit_order(
                token_id=arb.no_token_id,
                price=arb.no_price,
                size=no_shares,
                side="BUY",
            )

        # Register arbitrage positions in risk manager for tracking
        self.risk.open_position(
            token_id=arb.yes_token_id,
            side="BUY",
            size=yes_shares,
            price=arb.yes_price,
            is_arbitrage=True,
        )
        self.risk.open_position(
            token_id=arb.no_token_id,
            side="BUY",
            size=no_shares,
            price=arb.no_price,
            is_arbitrage=True,
        )

        log.info(
            "arbitrage_executed",
            condition=arb.condition_id[:16],
            yes_price=f"{arb.yes_price:.4f}",
            no_price=f"{arb.no_price:.4f}",
            profit_pct=f"{arb.profit_pct:.4f}",
        )
        return True

    def check_and_close_positions(self, store) -> int:
        """Check all positions for exit conditions and close if triggered."""
        # Update current prices from store (both risk manager and paper)
        for token_id, pos in self.risk.get_positions().items():
            data = store.get(token_id)
            if data and data.price > 0:
                self.risk.update_position_price(token_id, data.price)
                if self._paper_mode:
                    self.paper.update_position_price(token_id, data.price)

        exits = self.risk.check_exits()
        for token_id, reason in exits:
            pos = self.risk.get_positions().get(token_id)
            if not pos:
                continue

            exit_price = pos.current_price

            if self._paper_mode:
                self.paper.execute_sell(token_id, exit_price)
            else:
                self._clob.place_market_order(
                    token_id=token_id,
                    amount=pos.size * exit_price,
                    side="SELL" if pos.side == "BUY" else "BUY",
                )

            pnl = self.risk.close_position(token_id, exit_price)
            log.info(
                "position_exited",
                token=token_id[:16],
                reason=reason,
                pnl=f"${pnl:.2f}",
                pnl_pct=f"{pos.pnl_pct:.2%}",
                hold_min=f"{pos.hold_minutes:.1f}",
            )

        return len(exits)

    def get_summary(self) -> dict:
        if self._paper_mode:
            return self.paper.get_summary()
        return {
            "mode": "live",
            "bankroll": self.risk.bankroll,
            "positions": len(self.risk.get_positions()),
            "total_pnl": self.risk.get_total_pnl(),
        }
