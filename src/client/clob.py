from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
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
        self._client = ClobClient(
            host=settings.clob_host,
            chain_id=settings.chain_id,
            key=settings.polymarket_private_key or None,
        )
        if settings.polymarket_private_key:
            creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(creds)
            log.info("clob_client_authenticated")

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

    def cancel_order(self, order_id: str) -> dict:
        return self._client.cancel(order_id)

    def cancel_all(self) -> dict:
        return self._client.cancel_all()

    def get_open_orders(self) -> list:
        return self._client.get_orders()

    def get_trades(self) -> list:
        return self._client.get_trades()
