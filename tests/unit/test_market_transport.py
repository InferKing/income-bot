from __future__ import annotations

from typing import Any

import aiohttp
import pytest

from income_tg.market_data.transport import AioHttpWebSocketTransport, ReconnectPolicy


class FakeMessage:
    def __init__(
        self, message_type: aiohttp.WSMsgType, payload: dict[str, Any] | None = None
    ) -> None:
        self.type = message_type
        self._payload = payload

    def json(self) -> dict[str, Any] | None:
        return self._payload


class FakeSocket:
    def __init__(self, messages: list[FakeMessage]) -> None:
        self._messages = messages
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def receive(self, **kwargs: float) -> FakeMessage:
        del kwargs
        return self._messages.pop(0)


class SocketContext:
    def __init__(self, socket: FakeSocket) -> None:
        self.socket = socket

    async def __aenter__(self) -> FakeSocket:
        return self.socket

    async def __aexit__(self, *args: object) -> None:
        del args


class FakeSession:
    def __init__(self, sockets: list[FakeSocket]) -> None:
        self.sockets = sockets
        self.connected: list[FakeSocket] = []

    def ws_connect(self, *args: object, **kwargs: object) -> SocketContext:
        del args, kwargs
        socket = self.sockets.pop(0)
        self.connected.append(socket)
        return SocketContext(socket)


@pytest.mark.asyncio
async def test_reconnect_replays_subscriptions() -> None:
    first = FakeSocket([FakeMessage(aiohttp.WSMsgType.CLOSE)])
    second = FakeSocket([FakeMessage(aiohttp.WSMsgType.TEXT, {"topic": "trade", "data": []})])
    session = FakeSession([first, second])
    transport = AioHttpWebSocketTransport(
        session,  # type: ignore[arg-type]
        reconnect=ReconnectPolicy(maximum_attempts=2),
    )
    subscription = {"op": "subscribe", "args": ["trade"]}

    stream = transport.stream_json(
        "wss://example.test", subscriptions=(subscription,), heartbeat={"op": "ping"}
    )
    payload = await anext(stream)
    await stream.aclose()

    assert payload["topic"] == "trade"
    assert first.sent == [subscription]
    assert second.sent == [subscription]
