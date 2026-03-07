import asyncio
import json
from collections.abc import Callable
from typing import Any

import websockets

from config.settings import settings
from src.utils.logger import get_logger

log = get_logger(__name__)


class MarketWebSocket:
    """WebSocket client for real-time Polymarket market data."""

    def __init__(self, on_message: Callable[[dict], Any]) -> None:
        self._url = settings.ws_url
        self._on_message = on_message
        self._ws = None
        self._running = False
        self._token_ids: list[str] = []

    async def connect(self, token_ids: list[str]) -> None:
        self._token_ids = token_ids
        self._running = True

        while self._running:
            try:
                async with websockets.connect(self._url) as ws:
                    self._ws = ws
                    log.info("ws_connected", tokens=len(token_ids))

                    await ws.send(
                        json.dumps(
                            {
                                "assets_ids": token_ids,
                                "type": "market",
                                "custom_feature_enabled": True,
                            }
                        )
                    )

                    heartbeat_task = asyncio.create_task(self._heartbeat(ws))
                    try:
                        await self._receive_loop(ws)
                    finally:
                        heartbeat_task.cancel()

            except websockets.ConnectionClosed:
                log.warning("ws_disconnected", reconnecting=True)
                await asyncio.sleep(2)
            except Exception as e:
                log.error("ws_error", error=str(e))
                await asyncio.sleep(5)

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(10)
            try:
                await ws.send("PING")
            except Exception:
                break

    async def _receive_loop(self, ws) -> None:
        async for raw in ws:
            if raw == "PONG":
                continue
            try:
                msg = json.loads(raw)
                self._on_message(msg)
            except json.JSONDecodeError:
                log.warning("ws_invalid_json", raw=raw[:100])

    async def disconnect(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
            log.info("ws_closed")
