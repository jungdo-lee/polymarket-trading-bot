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

log = get_logger(__name__)

# Evaluation interval in seconds
EVAL_INTERVAL = 5.0
# Max new positions opened per evaluation cycle (correlation risk control)
MAX_ENTRIES_PER_CYCLE = 2


class TradingBot:
    def __init__(self) -> None:
        self.store = MarketStore()
        self.history = PriceHistory()
        self.strategy = EnsembleStrategy()
        self.gamma = GammaClient()

        clob = None
        if not settings.paper_mode and settings.polymarket_private_key:
            clob = PolymarketClient()

        self.trader = Trader(clob_client=clob)
        self.ws = MarketWebSocket(on_message=self._on_ws_message)
        self._running = False

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

    async def _evaluation_loop(self) -> None:
        """Periodically evaluate all markets for trading signals."""
        while self._running:
            try:
                self._evaluate_all()
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
            if self.trader.execute_signal(sig) and sig.side == "BUY":
                entered += 1

        # 3. Arbitrage scan
        arb_opportunities = self.strategy.find_arbitrage(self.store)
        for arb in arb_opportunities:
            bet_amount = self.trader.risk.bankroll * settings.max_position_pct
            if bet_amount >= 1.0:
                log.info(
                    "arbitrage_detected",
                    condition=arb.condition_id[:16],
                    profit_pct=f"{arb.profit_pct:.4f}",
                )
                self.trader.execute_arbitrage(arb, bet_amount)

    def _load_markets(self) -> list[str]:
        """Fetch active markets and register them in the store."""
        markets = self.gamma.get_active_markets()
        all_token_ids = []

        for m in markets:
            condition_id = m.get("conditionId", "")
            question = m.get("question", "")
            token_map = GammaClient.parse_token_ids(m)

            for outcome, token_id in token_map.items():
                self.store.register_market(
                    token_id=token_id,
                    condition_id=condition_id,
                    question=question,
                    outcome=outcome,
                )
                all_token_ids.append(token_id)

        log.info("markets_loaded", count=len(markets), tokens=len(all_token_ids))
        return all_token_ids

    async def run(self) -> None:
        """Main bot entry point."""
        setup_logging()
        self._running = True

        log.info(
            "bot_starting",
            mode="PAPER" if settings.paper_mode else "LIVE",
            bankroll=settings.initial_bankroll,
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
        summary = self.trader.get_summary()
        log.info("bot_shutdown", **summary)

        if settings.paper_mode:
            path = self.trader.paper.save_history()
            log.info("results_saved", path=str(path))

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
