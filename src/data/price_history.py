from collections import deque

import pandas as pd


class PriceHistory:
    """Per-token price time series for technical indicator calculation."""

    def __init__(self, max_length: int = 200) -> None:
        self._max = max_length
        self._prices: dict[str, deque[float]] = {}
        self._volumes: dict[str, deque[float]] = {}

    def record(self, token_id: str, price: float, volume: float = 0.0) -> None:
        if token_id not in self._prices:
            self._prices[token_id] = deque(maxlen=self._max)
            self._volumes[token_id] = deque(maxlen=self._max)
        self._prices[token_id].append(price)
        self._volumes[token_id].append(volume)

    def get_prices(self, token_id: str) -> pd.Series:
        data = self._prices.get(token_id, deque())
        return pd.Series(list(data), dtype=float)

    def get_volumes(self, token_id: str) -> pd.Series:
        data = self._volumes.get(token_id, deque())
        return pd.Series(list(data), dtype=float)

    def has_enough_data(self, token_id: str, min_points: int = 30) -> bool:
        return len(self._prices.get(token_id, deque())) >= min_points

    def latest_price(self, token_id: str) -> float | None:
        data = self._prices.get(token_id)
        return data[-1] if data else None

    def count(self, token_id: str) -> int:
        return len(self._prices.get(token_id, deque()))
