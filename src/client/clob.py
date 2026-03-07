from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    AssetType,
    BalanceAllowanceParams,
    MarketOrderArgs,
    OrderArgs,
    OrderType,
)
from py_clob_client.order_builder.constants import BUY, SELL

from config.settings import settings
from src.utils.logger import get_logger

log = get_logger(__name__)


class PolymarketClient:
    """Wrapper around py-clob-client for Polymarket CLOB API."""

    def __init__(self) -> None:
        sig_type = settings.polymarket_signature_type or None
        funder = settings.polymarket_funder or None
        self._client = ClobClient(
            host=settings.clob_host,
            chain_id=settings.chain_id,
            key=settings.polymarket_private_key or None,
            signature_type=sig_type,
            funder=funder,
        )
        if settings.polymarket_private_key:
            creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(creds)
            log.info(
                "clob_client_authenticated",
                sig_type=sig_type or 0,
                funder=funder[:10] + "..." if funder else "EOA",
            )

    # --- Read-only ---

    def get_order_book(self, token_id: str) -> dict:
        book = self._client.get_order_book(token_id)
        return {
            "bids": [{"price": float(b.price), "size": float(b.size)} for b in book.bids],
            "asks": [{"price": float(a.price), "size": float(a.size)} for a in book.asks],
        }

    def get_price(self, token_id: str, side: str = "BUY") -> float:
        resp = self._client.get_price(token_id, side)
        return float(resp.get("price", 0))

    def get_midpoint(self, token_id: str) -> float:
        resp = self._client.get_midpoint(token_id)
        return float(resp.get("mid", 0))

    def get_markets(self, next_cursor: str = "MA==") -> dict:
        return self._client.get_markets(next_cursor=next_cursor)

    # --- Trading ---

    def place_limit_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
    ) -> dict:
        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY if side == "BUY" else SELL,
        )
        signed = self._client.create_order(order_args)
        resp = self._client.post_order(signed, OrderType.GTC)
        log.info("limit_order_placed", token_id=token_id[:16], price=price, size=size, side=side)
        return resp

    def place_market_order(
        self,
        token_id: str,
        amount: float,
        side: str,
    ) -> dict:
        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=BUY if side == "BUY" else SELL,
        )
        signed = self._client.create_market_order(order_args)
        resp = self._client.post_order(signed, OrderType.FOK)
        log.info("market_order_placed", token_id=token_id[:16], amount=amount, side=side)
        return resp

    def check_order_fill(self, order_id: str, timeout: float = 3.0) -> dict:
        """Wait for order fill and return fill status.

        Returns dict:
            filled: bool — True if any shares were matched
            size_filled: float — actual filled size (shares)
            order_id: str
        """
        import time

        start = time.time()
        while time.time() - start < timeout:
            try:
                order = self._client.get_order(order_id)
                if not order:
                    time.sleep(0.5)
                    continue

                status = (order.get("status") or "").upper()
                size_matched = float(order.get("size_matched") or 0)

                if status == "MATCHED":
                    log.info("order_filled", order_id=order_id[:16], size=size_matched)
                    return {"filled": True, "size_filled": size_matched, "order_id": order_id}
                if status in ("CANCELLED", "EXPIRED"):
                    return {"filled": False, "size_filled": 0, "order_id": order_id}

                # LIVE or other — still pending
                time.sleep(0.5)
            except Exception:
                time.sleep(0.5)

        # Timeout — cancel unfilled order
        try:
            self._client.cancel(order_id)
            log.info("unfilled_order_cancelled", order_id=order_id[:16])
        except Exception:
            pass

        return {"filled": False, "size_filled": 0, "order_id": order_id}

    def cancel_order(self, order_id: str) -> dict:
        return self._client.cancel(order_id)

    def cancel_all(self) -> dict:
        return self._client.cancel_all()

    def get_open_orders(self) -> list:
        return self._client.get_orders()

    def get_trades(self) -> list:
        return self._client.get_trades()

    def get_order(self, order_id: str) -> dict:
        """Get a single order by ID to check fill status."""
        try:
            return self._client.get_order(order_id)
        except Exception:
            return {}

    def cancel_and_clear(self) -> int:
        """Cancel all open orders. Returns count cancelled."""
        try:
            orders = self._client.get_orders()
            if not orders:
                return 0
            self._client.cancel_all()
            count = len(orders) if isinstance(orders, list) else 0
            log.info("orders_cancelled", count=count)
            return count
        except Exception as e:
            log.warning("cancel_all_failed", error=str(e))
            return 0

    # --- Account info ---

    def get_address(self) -> str:
        return self._client.get_address()

    def get_balance_allowance(self) -> dict:
        """Get USDC collateral balance and allowance.

        Calls update first to sync on-chain state, then returns
        a normalised dict with ``balance`` (raw) and ``allowance``
        (max value among exchange contracts, raw).
        """
        try:
            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            self._client.update_balance_allowance(params=params)
            params2 = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            raw = self._client.get_balance_allowance(params=params2)
        except TypeError:
            raw = self._client.get_balance_allowance(asset_type=0)

        # allowances는 dict {exchange_addr: raw_value}
        allowances = raw.get("allowances", {})
        max_allowance = max(
            (int(v) for v in allowances.values()), default=0
        )
        return {
            "balance": raw.get("balance", "0"),
            "allowance": str(max_allowance),
        }

    def test_connection(self) -> bool:
        """Test API connectivity. Returns True if server responds OK."""
        try:
            resp = self._client.get_ok()
            return resp == "OK"
        except Exception as e:
            log.error("connection_test_failed", error=str(e))
            return False
