import json

import httpx

from config.settings import settings
from src.utils.logger import get_logger

log = get_logger(__name__)


class GammaClient:
    """Client for Polymarket Gamma API (market discovery)."""

    def __init__(self) -> None:
        self._base = settings.gamma_host
        self._http = httpx.Client(base_url=self._base, timeout=15)

    def get_active_markets(
        self,
        limit: int = 100,
        min_liquidity: float | None = None,
        min_volume_24h: float | None = None,
    ) -> list[dict]:
        """Fetch active, tradeable markets sorted by 24h volume."""
        markets: list[dict] = []
        offset = 0

        while True:
            resp = self._http.get(
                "/markets",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": limit,
                    "offset": offset,
                },
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            markets.extend(batch)
            offset += limit

        liq = min_liquidity or settings.min_liquidity
        vol = min_volume_24h or settings.min_volume_24h

        filtered = [
            m
            for m in markets
            if float(m.get("liquidity", 0)) >= liq
            and float(m.get("volume24hr", 0)) >= vol
        ]
        log.info("markets_fetched", total=len(markets), filtered=len(filtered))
        return filtered

    @staticmethod
    def parse_token_ids(market: dict) -> dict[str, str]:
        """Extract {outcome: token_id} mapping from a Gamma market."""
        outcomes = json.loads(market.get("outcomes", "[]"))
        token_ids = json.loads(market.get("clobTokenIds", "[]"))
        return dict(zip(outcomes, token_ids))

    @staticmethod
    def parse_prices(market: dict) -> dict[str, float]:
        """Extract {outcome: price} mapping from a Gamma market."""
        outcomes = json.loads(market.get("outcomes", "[]"))
        prices = json.loads(market.get("outcomePrices", "[]"))
        return {o: float(p) for o, p in zip(outcomes, prices)}

    def close(self) -> None:
        self._http.close()
