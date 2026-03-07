import time
from dataclasses import dataclass, field
from typing import Any

from src.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class OrderBook:
    bids: list[dict] = field(default_factory=list)  # [{price, size}, ...]
    asks: list[dict] = field(default_factory=list)
    best_bid: float = 0.0
    best_ask: float = 0.0
    spread: float = 0.0


@dataclass
class MarketData:
    token_id: str = ""
    condition_id: str = ""
    question: str = ""
    outcome: str = ""
    price: float = 0.0
    last_trade_price: float = 0.0
    price_updated_at: float = 0.0  # unix timestamp of last real price update
    order_book: OrderBook = field(default_factory=OrderBook)


class MarketStore:
    """In-memory cache of current market state per token."""

    def __init__(self) -> None:
        self._markets: dict[str, MarketData] = {}
        # Maps condition_id -> {outcome: token_id}
        self._token_map: dict[str, dict[str, str]] = {}

    def register_market(
        self,
        token_id: str,
        condition_id: str,
        question: str,
        outcome: str,
    ) -> None:
        self._markets[token_id] = MarketData(
            token_id=token_id,
            condition_id=condition_id,
            question=question,
            outcome=outcome,
        )
        if condition_id not in self._token_map:
            self._token_map[condition_id] = {}
        self._token_map[condition_id][outcome] = token_id

    def get(self, token_id: str) -> MarketData | None:
        return self._markets.get(token_id)

    def get_pair(self, condition_id: str) -> dict[str, str] | None:
        """Return {outcome: token_id} for YES/NO pair of a market."""
        return self._token_map.get(condition_id)

    def all_token_ids(self) -> list[str]:
        return list(self._markets.keys())

    def update_order_book(self, token_id: str, bids: list[dict], asks: list[dict]) -> None:
        data = self._markets.get(token_id)
        if not data:
            return
        data.order_book.bids = bids
        data.order_book.asks = asks
        if bids:
            data.order_book.best_bid = bids[0]["price"]
        if asks:
            data.order_book.best_ask = asks[0]["price"]
        if bids and asks:
            data.order_book.spread = asks[0]["price"] - bids[0]["price"]
            data.price = (bids[0]["price"] + asks[0]["price"]) / 2
            data.price_updated_at = time.time()

    def update_price(self, token_id: str, price: float) -> None:
        data = self._markets.get(token_id)
        if data:
            data.price = price
            data.price_updated_at = time.time()

    def update_last_trade(self, token_id: str, price: float) -> None:
        data = self._markets.get(token_id)
        if data:
            data.last_trade_price = price

    def update_best_bid_ask(self, token_id: str, best_bid: float, best_ask: float) -> None:
        data = self._markets.get(token_id)
        if not data:
            return
        data.order_book.best_bid = best_bid
        data.order_book.best_ask = best_ask
        data.order_book.spread = best_ask - best_bid
        data.price = (best_bid + best_ask) / 2
        data.price_updated_at = time.time()

    def handle_ws_message(self, msg: dict) -> None:
        """Process a WebSocket message and update the store."""
        event_type = msg.get("event_type")

        if event_type == "book":
            token_id = msg.get("asset_id", "")
            bids = [{"price": float(b["price"]), "size": float(b["size"])} for b in msg.get("bids", [])]
            asks = [{"price": float(a["price"]), "size": float(a["size"])} for a in msg.get("asks", [])]
            self.update_order_book(token_id, bids, asks)

        elif event_type == "price_change":
            for change in msg.get("price_changes", []):
                token_id = change.get("asset_id", "")
                best_bid = float(change.get("best_bid", 0))
                best_ask = float(change.get("best_ask", 0))
                self.update_best_bid_ask(token_id, best_bid, best_ask)

        elif event_type == "last_trade_price":
            token_id = msg.get("asset_id", "")
            price = float(msg.get("price", 0))
            self.update_last_trade(token_id, price)

        elif event_type == "best_bid_ask":
            token_id = msg.get("asset_id", "")
            best_bid = float(msg.get("best_bid", 0))
            best_ask = float(msg.get("best_ask", 0))
            self.update_best_bid_ask(token_id, best_bid, best_ask)
