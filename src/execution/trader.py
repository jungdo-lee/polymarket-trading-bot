from config.settings import settings
from src.client.clob import PolymarketClient
from src.execution.paper import PaperTrader
from src.execution.risk import RiskManager
from src.strategy.arbitrage import ArbitrageSignal
from src.strategy.base import Signal
from src.utils.logger import get_logger
from src.utils.telegram import notify_arbitrage, notify_buy, notify_sell

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

        # Polymarket CLOB 최소 주문 크기 적용
        min_shares = settings.min_order_size
        if shares < min_shares:
            min_cost = min_shares * signal.market_price
            if min_cost <= self.risk.bankroll * self.risk.max_position_pct * 1.5:
                shares = min_shares
            else:
                log.debug("trade_skipped_min_size", shares=f"{shares:.1f}", min=min_shares)
                return False

        if self._paper_mode:
            self.paper.execute_buy(
                token_id=signal.token_id,
                price=signal.market_price,
                size=shares,
                strategy=signal.strategy,
                ev=signal.ev,
            )
        else:
            try:
                resp = self._clob.place_limit_order(
                    token_id=signal.token_id,
                    price=signal.market_price,
                    size=shares,
                    side="BUY",
                )
                # 체결 확인: 주문이 실제로 매칭되었는지 검증
                order_status = (resp.get("status") or "").lower()
                order_id = resp.get("orderID") or resp.get("order_id") or ""

                if order_status != "matched" and order_id:
                    # 즉시 체결되지 않음 → 대기 후 확인
                    fill = self._clob.check_order_fill(order_id)
                    if not fill["filled"]:
                        log.warning(
                            "buy_order_unfilled",
                            token=signal.token_id[:16],
                            order_id=order_id[:16],
                        )
                        return False
                    if fill["size_filled"] > 0:
                        shares = fill["size_filled"]
            except Exception as e:
                log.error("buy_order_failed", token=signal.token_id[:16], error=str(e)[:100])
                return False

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
        notify_buy(
            token_id=signal.token_id,
            strategy=signal.strategy,
            price=signal.market_price,
            size=shares,
            ev=signal.ev,
            bankroll=self.risk.bankroll,
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
            try:
                self._clob.place_market_order(
                    token_id=signal.token_id,
                    amount=pos.size * exit_price,
                    side="SELL",
                )
            except Exception as e:
                error_str = str(e)
                log.error("sell_order_failed", token=signal.token_id[:16], error=error_str[:100])
                # "not enough balance" → 체결되지 않은 고스트 포지션 제거
                if "not enough balance" in error_str.lower():
                    self.discard_ghost_position(signal.token_id)
                return False

        pnl = self.risk.close_position(signal.token_id, exit_price)
        log.info(
            "sell_signal_exit",
            token=signal.token_id[:16],
            strategy=signal.strategy,
            price=f"{exit_price:.4f}",
            pnl=f"${pnl:.2f}",
        )
        notify_sell(
            token_id=signal.token_id,
            reason=f"sell_signal ({signal.strategy})",
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pos.pnl_pct,
            hold_min=pos.hold_minutes,
            bankroll=self.risk.bankroll,
            **self._cumulative_stats(),
        )
        return True

    def execute_arbitrage(self, arb: ArbitrageSignal, bet_amount: float) -> bool:
        """Execute a YES+NO arbitrage trade."""
        yes_shares = bet_amount / arb.yes_price
        no_shares = bet_amount / arb.no_price

        # Polymarket CLOB 최소 주문 크기 적용
        min_shares = settings.min_order_size
        if yes_shares < min_shares or no_shares < min_shares:
            yes_shares = max(yes_shares, min_shares)
            no_shares = max(no_shares, min_shares)

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
            try:
                resp_yes = self._clob.place_limit_order(
                    token_id=arb.yes_token_id,
                    price=arb.yes_price,
                    size=yes_shares,
                    side="BUY",
                )
                resp_no = self._clob.place_limit_order(
                    token_id=arb.no_token_id,
                    price=arb.no_price,
                    size=no_shares,
                    side="BUY",
                )
                # 양쪽 모두 체결 확인
                yes_filled = self._verify_fill(resp_yes, arb.yes_token_id)
                no_filled = self._verify_fill(resp_no, arb.no_token_id)

                if not yes_filled or not no_filled:
                    log.warning(
                        "arbitrage_partial_fill",
                        yes_filled=yes_filled,
                        no_filled=no_filled,
                    )
                    self._clob.cancel_and_clear()
                    return False

                if yes_filled.get("size_filled", 0) > 0:
                    yes_shares = yes_filled["size_filled"]
                if no_filled.get("size_filled", 0) > 0:
                    no_shares = no_filled["size_filled"]
            except Exception as e:
                log.error("arbitrage_order_failed", error=str(e)[:100])
                # 하나만 체결되면 위험하므로 모두 취소
                self._clob.cancel_and_clear()
                return False

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
        notify_arbitrage(
            condition_id=arb.condition_id,
            yes_price=arb.yes_price,
            no_price=arb.no_price,
            profit_pct=arb.profit_pct,
            bankroll=self.risk.bankroll,
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
                try:
                    self._clob.place_market_order(
                        token_id=token_id,
                        amount=pos.size * exit_price,
                        side="SELL" if pos.side == "BUY" else "BUY",
                    )
                except Exception as e:
                    error_str = str(e)
                    log.error("exit_sell_failed", token=token_id[:16], error=error_str[:100])
                    # "not enough balance" → 체결되지 않은 고스트 포지션 제거
                    if "not enough balance" in error_str.lower():
                        self.discard_ghost_position(token_id)
                    continue  # 다음 시도에서 재시도

            pnl = self.risk.close_position(token_id, exit_price)
            log.info(
                "position_exited",
                token=token_id[:16],
                reason=reason,
                pnl=f"${pnl:.2f}",
                pnl_pct=f"{pos.pnl_pct:.2%}",
                hold_min=f"{pos.hold_minutes:.1f}",
            )
            notify_sell(
                token_id=token_id,
                reason=reason,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                pnl=pnl,
                pnl_pct=pos.pnl_pct,
                hold_min=pos.hold_minutes,
                bankroll=self.risk.bankroll,
                **self._cumulative_stats(),
            )

        return len(exits)

    def _verify_fill(self, resp: dict, token_id: str) -> dict | None:
        """Verify a limit order was filled. Returns fill info or None."""
        if not self._clob:
            return None

        order_status = (resp.get("status") or "").lower()
        order_id = resp.get("orderID") or resp.get("order_id") or ""

        if order_status == "matched":
            return {"filled": True, "size_filled": 0}

        if order_id:
            fill = self._clob.check_order_fill(order_id)
            if fill["filled"]:
                return fill
            log.warning("order_unfilled", token=token_id[:16], order_id=order_id[:16])
            return None

        log.warning("order_no_id", token=token_id[:16], status=order_status)
        return None

    def discard_ghost_position(self, token_id: str) -> None:
        """Remove a position that was never actually filled on-chain.

        Called when SELL fails with 'not enough balance' — the BUY order
        was recorded but never actually filled.
        """
        positions = self.risk.get_positions()
        if token_id not in positions:
            return

        pos = positions[token_id]
        # 포지션 비용을 bankroll에 복원 (가상 차감분 원복)
        cost = pos.size * pos.entry_price
        self.risk.bankroll += cost
        del self.risk._positions[token_id]
        log.warning(
            "ghost_position_discarded",
            token=token_id[:16],
            size=f"{pos.size:.2f}",
            cost=f"${cost:.2f}",
            bankroll=f"${self.risk.bankroll:.2f}",
        )

    def _cumulative_stats(self) -> dict:
        """Get cumulative stats for sell notification."""
        if self._paper_mode:
            s = self.paper.get_summary()
            return {
                "total_trades": s.get("closed_trades", 0),
                "wins": s.get("wins", 0),
                "total_pnl": s.get("realized_pnl", 0.0),
                "win_rate": s.get("win_rate", 0.0),
            }
        return {}

    def get_summary(self) -> dict:
        if self._paper_mode:
            return self.paper.get_summary()
        return {
            "mode": "live",
            "bankroll": self.risk.bankroll,
            "positions": len(self.risk.get_positions()),
            "total_pnl": self.risk.get_total_pnl(),
        }
