import asyncio
import json
import signal
import sys
import time

from config.settings import settings
from src.client.clob import PolymarketClient
from src.client.gamma import GammaClient
from src.client.websocket import MarketWebSocket
from src.data.market_store import MarketStore
from src.data.price_history import PriceHistory
from src.execution.trader import Trader
from src.strategy.ensemble import EnsembleStrategy
from src.utils.logger import get_logger, setup_logging
from src.utils.telegram import send_message

log = get_logger(__name__)

# Evaluation interval in seconds
EVAL_INTERVAL = 5.0
# Max new positions opened per evaluation cycle (correlation risk control)
MAX_ENTRIES_PER_CYCLE = 2
# WebSocket warm-up: 차익거래 스캔 전 최소 대기 시간 (초)
WARMUP_SEC = 30.0


class TradingBot:
    def __init__(self) -> None:
        self.store = MarketStore()
        self.history = PriceHistory()
        self.strategy = EnsembleStrategy()
        self.gamma = GammaClient()

        self._clob: PolymarketClient | None = None
        if settings.polymarket_private_key:
            self._clob = PolymarketClient()

        self.trader = Trader(clob_client=self._clob if not settings.paper_mode else None)
        self.ws = MarketWebSocket(on_message=self._on_ws_message)
        self._running = False
        self._started_at: float = 0.0  # 시작 시각 (warm-up 판단용)

    def _on_ws_message(self, msg: dict) -> None:
        """Handle incoming WebSocket messages."""
        self.store.handle_ws_message(msg)

        # Record price history for technical indicators
        event_type = msg.get("event_type")
        if event_type in ("last_trade_price", "best_bid_ask"):
            token_id = msg.get("asset_id", "")
            data = self.store.get(token_id)
            if data and data.price > 0:
                volume = float(msg.get("size", 0)) if event_type == "last_trade_price" else 0
                self.history.record(token_id, data.price, volume)

        # 실시간 손절 체크: 오픈 포지션의 가격이 업데이트될 때마다 즉시 확인
        if event_type in ("last_trade_price", "best_bid_ask", "price_change"):
            self._realtime_exit_check(msg)

    def _realtime_exit_check(self, msg: dict) -> None:
        """WebSocket 가격 이벤트마다 오픈 포지션의 손절/익절을 즉시 체크."""
        token_id = msg.get("asset_id", "")
        if not token_id:
            return

        positions = self.trader.risk.get_positions()
        if token_id not in positions:
            return

        data = self.store.get(token_id)
        if not data or data.price <= 0:
            return

        # 가격 업데이트
        self.trader.risk.update_position_price(token_id, data.price)
        if self._paper_mode_active:
            self.trader.paper.update_position_price(token_id, data.price)

        # 즉시 청산 체크
        pos = self.trader.risk.get_positions().get(token_id)
        if not pos or pos.is_arbitrage:
            return

        reason = self.trader.risk._should_exit(pos)
        if reason:
            exit_price = pos.current_price
            if settings.paper_mode:
                self.trader.paper.execute_sell(token_id, exit_price)
            elif self._clob:
                try:
                    resp = self._clob.place_market_order(
                        token_id=token_id,
                        # SELL market order amount is share size, not notional.
                        amount=pos.size,
                        side="SELL" if pos.side == "BUY" else "BUY",
                    )
                    fill = self.trader._verify_fill(resp, token_id)
                    if not fill:
                        log.warning("realtime_exit_unfilled", token=token_id[:16], reason=reason)
                        return
                except Exception as e:
                    error_str = str(e)
                    log.error("realtime_exit_failed", token=token_id[:16], error=error_str)
                    # "not enough balance" → 체결되지 않은 고스트 포지션 제거
                    if "not enough balance" in error_str.lower():
                        self.trader.discard_ghost_position(token_id)
                    return

            from src.utils.telegram import notify_sell
            pnl = self.trader.risk.close_position(token_id, exit_price)
            log.info(
                "realtime_exit",
                token=token_id[:16],
                reason=reason,
                pnl=f"${pnl:.2f}",
            )
            notify_sell(
                token_id=token_id,
                reason=reason,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                pnl=pnl,
                pnl_pct=pos.pnl_pct,
                hold_min=pos.hold_minutes,
                bankroll=self.trader.risk.bankroll,
                **self.trader._cumulative_stats(),
            )

    @property
    def _paper_mode_active(self) -> bool:
        return settings.paper_mode

    async def _evaluation_loop(self) -> None:
        """Periodically evaluate all markets for trading signals."""
        cycle = 0
        while self._running:
            try:
                self._evaluate_all()
                # 매 6사이클(30초)마다 포지션 상태 저장
                cycle += 1
                if cycle % 6 == 0:
                    self.trader.risk.save_state()
                    if settings.paper_mode:
                        self.trader.paper.save_state()
            except Exception as e:
                log.error("eval_error", error=str(e))
            await asyncio.sleep(EVAL_INTERVAL)

    def _evaluate_all(self) -> None:
        """Run all strategies against all tracked markets."""
        # 1. Check exits first
        closed = self.trader.check_and_close_positions(self.store)
        if closed:
            log.info("positions_closed", count=closed)

        # 2. Collect directional signals (don't execute immediately)
        candidates: list = []
        seen_conditions: set[str] = set()

        # Also track conditions that already have open positions
        for pos_token_id in self.trader.risk.get_positions():
            meta = self.store.get(pos_token_id)
            if meta:
                seen_conditions.add(meta.condition_id)

        for token_id in self.store.all_token_ids():
            # Skip if already have position in same condition (correlation risk)
            meta = self.store.get(token_id)
            if meta and meta.condition_id in seen_conditions:
                continue

            sig = self.strategy.evaluate_directional(token_id, self.store, self.history)
            if sig:
                candidates.append(sig)
                if meta:
                    seen_conditions.add(meta.condition_id)

        # Sort by EV descending → take best opportunities first
        candidates.sort(key=lambda s: s.ev, reverse=True)

        entered = 0
        for sig in candidates:
            log.info(
                "signal_detected",
                strategy=sig.strategy,
                side=sig.side,
                token=sig.token_id[:16],
                ev=f"{sig.ev:.4f}",
                strength=f"{sig.strength:.2f}",
            )
            if sig.side == "BUY" and entered >= MAX_ENTRIES_PER_CYCLE:
                log.debug("entry_limit_reached", skipped=sig.token_id[:16])
                continue
            if self.trader.execute_signal(sig, store=self.store) and sig.side == "BUY":
                entered += 1

        # 3. Arbitrage scan (warm-up 기간 이후에만)
        if time.time() - self._started_at < WARMUP_SEC:
            return
        arb_opportunities = self.strategy.find_arbitrage(self.store)
        for arb in arb_opportunities:
            bet_amount = self.trader.risk.bankroll * settings.max_position_pct
            if bet_amount >= 1.0:
                log.info(
                    "arbitrage_detected",
                    condition=arb.condition_id[:16],
                    profit_pct=f"{arb.profit_pct:.4f}",
                )
                self.trader.execute_arbitrage(arb, bet_amount, store=self.store)

    def _load_markets(self) -> list[str]:
        """Fetch active markets and register them in the store."""
        markets = self.gamma.get_active_markets()
        all_token_ids = []

        for m in markets:
            condition_id = m.get("conditionId", "")
            question = m.get("question", "")
            token_map = GammaClient.parse_token_ids(m)
            prices = GammaClient.parse_prices(m)

            for outcome, token_id in token_map.items():
                self.store.register_market(
                    token_id=token_id,
                    condition_id=condition_id,
                    question=question,
                    outcome=outcome,
                )
                # Gamma REST API에서 초기 가격 시딩
                if outcome in prices and prices[outcome] > 0:
                    self.store.update_price(token_id, prices[outcome])
                all_token_ids.append(token_id)

        log.info("markets_loaded", count=len(markets), tokens=len(all_token_ids))
        return all_token_ids

    def _check_wallet(self) -> bool:
        """Test wallet connection and log balance on startup."""
        if not self._clob:
            log.warning("wallet_skipped", reason="no private key configured")
            return True  # OK for paper mode without key

        if not self._clob.test_connection():
            log.error("wallet_connection_failed")
            return False

        address = self._clob.get_address()
        sig_type = settings.polymarket_signature_type
        funder = settings.polymarket_funder or address
        sig_label = {0: "EOA", 1: "POLY_PROXY", 2: "GNOSIS_SAFE"}.get(sig_type, str(sig_type))
        log.info(
            "wallet_connected",
            address=address,
            funder=funder[:16],
            sig_type=sig_label,
        )

        try:
            bal = self._clob.get_balance_allowance()
            balance = float(bal.get("balance", 0)) / 1e6  # USDC has 6 decimals
            allowance_raw = float(bal.get("allowance", 0)) / 1e6
            approved = allowance_raw > 1e12  # MAX_UINT256 → unlimited
            allowance_display = "UNLIMITED" if approved else f"${allowance_raw:.2f}"
            log.info("wallet_balance", usdc=f"${balance:.2f}", allowance=allowance_display)

            if balance == 0 and not approved:
                log.warning(
                    "balance_zero_hint",
                    hint="CLOB API 거래를 위해서는 지갑에 직접 USDC를 보유해야 합니다. "
                         "Polymarket 웹 디파짓(Relay)은 API 잔고에 반영되지 않습니다.",
                )

            status = "\U00002705 승인됨" if approved else "\U0000274c 미승인"
            funder_display = f"\n\U0001f4cb 프록시: <code>{funder[:10]}...{funder[-6:]}</code>" if funder != address else ""
            send_message(
                f"\U0001f517 <b>지갑 연결 완료</b>\n"
                f"\U0001f4cd 주소: <code>{address[:10]}...{address[-6:]}</code>"
                f"{funder_display}\n"
                f"\U0001f4b0 USDC 잔고: ${balance:.2f}\n"
                f"\U0001f513 허용량: {status}\n"
                f"\U0001f50f 서명: {sig_label}"
            )
        except Exception as e:
            log.warning("balance_check_failed", error=str(e))

        return True

    async def run(self) -> None:
        """Main bot entry point."""
        setup_logging()
        self._running = True
        self._started_at = time.time()

        mode = "PAPER" if settings.paper_mode else "LIVE"
        log.info("bot_starting", mode=mode, bankroll=settings.initial_bankroll)

        # Wallet connection test
        if not self._check_wallet():
            log.error("startup_aborted", reason="wallet connection failed")
            send_message("<b>봇 시작 실패</b>\n지갑 연결에 실패했습니다.")
            return

        # LIVE 모드: 시작 시 기존 오픈 주문 취소
        if not settings.paper_mode and self._clob:
            cancelled = self._clob.cancel_and_clear()
            if cancelled:
                log.info("startup_orders_cancelled", count=cancelled)

        # 이전 세션 포지션 복원
        if settings.paper_mode:
            restored = self.trader.risk.load_state()
            self.trader.paper.load_state()
        else:
            # LIVE 모드: 로컬 상태 무시, 클린 시작
            self.trader.risk.clear_state_file()
            self.trader.risk._positions.clear()
            self.trader.risk.bankroll = settings.initial_bankroll
            restored = 0
            log.info("live_clean_start", bankroll=settings.initial_bankroll)

        if restored:
            log.info("positions_restored", count=restored)
            send_message(
                "\U0001f504 <b>포지션 복원</b>\n"
                f"복원 포지션: {restored}개\n"
                f"잔고: ${self.trader.risk.bankroll:.2f}"
            )

        send_message(
            "\U0001f680 <b>봇 시작</b>\n"
            f"모드: {mode}\n"
            f"초기 자금: ${settings.initial_bankroll:.2f}\n"
            f"최대 포지션: {settings.max_open_positions}개"
        )

        # Load markets
        token_ids = self._load_markets()
        if not token_ids:
            log.error("no_markets_found")
            return

        # Start evaluation loop and WebSocket concurrently
        eval_task = asyncio.create_task(self._evaluation_loop())
        ws_task = asyncio.create_task(self.ws.connect(token_ids))

        try:
            await asyncio.gather(eval_task, ws_task)
        except asyncio.CancelledError:
            pass
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        self._running = False
        log.info("shutdown_initiated")

        # Close all open positions
        positions = self.trader.risk.get_positions()
        if positions:
            log.info("closing_all_positions", count=len(positions))
            closed_pnl = 0.0
            for token_id, pos in list(positions.items()):
                exit_price = pos.current_price
                if exit_price <= 0:
                    data = self.store.get(token_id)
                    exit_price = data.price if data and data.price > 0 else pos.entry_price

                if settings.paper_mode:
                    self.trader.paper.execute_sell(token_id, exit_price)
                elif self._clob:
                    try:
                        resp = self._clob.place_market_order(
                            token_id=token_id,
                            # SELL market order amount is share size, not notional.
                            amount=pos.size,
                            side="SELL" if pos.side == "BUY" else "BUY",
                        )
                        fill = self.trader._verify_fill(resp, token_id)
                        if not fill:
                            log.warning("shutdown_exit_unfilled", token=token_id[:16])
                            continue
                    except Exception as e:
                        error_str = str(e)
                        log.error("shutdown_sell_failed", token=token_id[:16], error=error_str)
                        # "not enough balance" → 고스트 포지션 제거 (실제 체결 안 된 주문)
                        if "not enough balance" in error_str.lower():
                            self.trader.discard_ghost_position(token_id)
                        continue

                pnl = self.trader.risk.close_position(token_id, exit_price)
                closed_pnl += pnl
                log.info(
                    "shutdown_position_closed",
                    token=token_id[:16],
                    price=f"{exit_price:.4f}",
                    pnl=f"${pnl:.2f}",
                )

            send_message(
                f"<b>종료 청산 완료</b>\n"
                f"청산 포지션: {len(positions)}개\n"
                f"청산 손익: ${closed_pnl:+.2f}\n"
                f"최종 잔고: ${self.trader.risk.bankroll:.2f}"
            )

        summary = self.trader.get_summary()
        log.info("bot_shutdown", **summary)

        # --- Session Summary Report ---
        initial = settings.initial_bankroll
        final = summary.get("current_bankroll", self.trader.risk.bankroll)
        total_pnl = summary.get("total_pnl", 0)
        realized_pnl = summary.get("realized_pnl", total_pnl)
        unrealized_pnl = summary.get("unrealized_pnl", 0)
        total_trades = summary.get("total_trades", 0)
        closed_trades = summary.get("closed_trades", 0)
        wins = summary.get("wins", 0)
        losses = summary.get("losses", 0)
        win_rate = summary.get("win_rate", 0)
        roi = ((final - initial) / initial * 100) if initial > 0 else 0

        elapsed = time.time() - self._started_at
        hours, remainder = divmod(int(elapsed), 3600)
        minutes, secs = divmod(remainder, 60)
        duration = f"{hours}h {minutes}m {secs}s"

        log.info("=" * 50)
        log.info("SESSION SUMMARY")
        log.info("=" * 50)
        log.info(f"  Duration       : {duration}")
        log.info(f"  Mode           : {'PAPER' if settings.paper_mode else 'LIVE'}")
        log.info(f"  Initial Capital: ${initial:,.2f}")
        log.info(f"  Final Capital  : ${final:,.2f}")
        log.info(f"  Total PnL      : ${total_pnl:+,.2f}")
        log.info(f"  Realized PnL   : ${realized_pnl:+,.2f}")
        log.info(f"  Unrealized PnL : ${unrealized_pnl:+,.2f}")
        log.info(f"  ROI            : {roi:+.2f}%")
        log.info("-" * 50)
        log.info(f"  Total Trades   : {total_trades}")
        log.info(f"  Closed Trades  : {closed_trades}")
        log.info(f"  Wins / Losses  : {wins}W / {losses}L")
        log.info(f"  Win Rate       : {win_rate:.1%}")
        log.info("=" * 50)

        pnl_emoji = "\U0001f4c8" if total_pnl >= 0 else "\U0001f4c9"
        send_message(
            f"\U0001f6d1 <b>봇 종료 — 세션 리포트</b>\n\n"
            f"\u23f1 운영 시간: {duration}\n"
            f"\U0001f4b0 초기 자금: ${initial:,.2f}\n"
            f"\U0001f3e6 최종 자금: ${final:,.2f}\n"
            f"{pnl_emoji} 총 손익: ${total_pnl:+,.2f} ({roi:+.2f}%)\n\n"
            f"\U0001f4ca <b>거래 통계</b>\n"
            f"총 거래: {total_trades}건 (청산: {closed_trades}건)\n"
            f"승/패: {wins}W / {losses}L\n"
            f"승률: {win_rate:.1%}"
        )

        if settings.paper_mode:
            path = self.trader.paper.save_history()
            log.info("results_saved", path=str(path))

        # 모든 포지션 청산 완료 → 상태 파일 제거 (다음 시작 시 새로 시작)
        # 포지션이 남아있으면 상태 저장 (비정상 종료 대비)
        if not self.trader.risk.get_positions():
            self.trader.risk.clear_state_file()
            if settings.paper_mode:
                self.trader.paper.clear_state_file()
        else:
            self.trader.risk.save_state()
            if settings.paper_mode:
                self.trader.paper.save_state()

        self.gamma.close()


def main() -> None:
    bot = TradingBot()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_signal(sig, frame):
        log.info("shutdown_signal_received")
        for task in asyncio.all_tasks(loop):
            task.cancel()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        loop.run_until_complete(bot.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
