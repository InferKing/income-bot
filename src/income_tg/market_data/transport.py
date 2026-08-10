"""Timeout-bounded HTTP and reconnecting WebSocket transports."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp

from income_tg.market_data.adapters.base import JsonObject


@dataclass(frozen=True, slots=True)
class ReconnectPolicy:
    initial_delay_seconds: float = 0.5
    maximum_delay_seconds: float = 30.0
    multiplier: float = 2.0
    maximum_attempts: int | None = None


@dataclass(frozen=True, slots=True)
class RestRetryPolicy:
    maximum_attempts: int = 4
    initial_delay_seconds: float = 0.25
    maximum_delay_seconds: float = 4.0
    multiplier: float = 2.0
    jitter_fraction: float = 0.2


class AioHttpRestTransport:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        timeout_seconds: float = 10.0,
        retry: RestRetryPolicy | None = None,
    ) -> None:
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._retry = retry or RestRetryPolicy()

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> JsonObject:
        delay = self._retry.initial_delay_seconds
        for attempt in range(1, self._retry.maximum_attempts + 1):
            try:
                async with self._session.request(
                    method, url, params=params, timeout=self._timeout
                ) as response:
                    response.raise_for_status()
                    payload: Any = await response.json()
                if not isinstance(payload, Mapping):
                    raise ValueError("HTTP response must be a JSON object")
                return payload
            except (aiohttp.ClientError, TimeoutError):
                if attempt >= self._retry.maximum_attempts or method.upper() != "GET":
                    raise
                jitter = 1 + random.uniform(
                    -self._retry.jitter_fraction,
                    self._retry.jitter_fraction,
                )
                await asyncio.sleep(max(0.0, delay * jitter))
                delay = min(delay * self._retry.multiplier, self._retry.maximum_delay_seconds)
        raise AssertionError("unreachable")


class AioHttpWebSocketTransport:
    """Reconnects and replays subscriptions after every disconnection."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        connect_timeout_seconds: float = 10.0,
        heartbeat_seconds: float = 20.0,
        receive_timeout_seconds: float = 45.0,
        reconnect: ReconnectPolicy | None = None,
    ) -> None:
        self._session = session
        self._connect_timeout = connect_timeout_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._receive_timeout = receive_timeout_seconds
        self._reconnect = reconnect or ReconnectPolicy()

    async def stream_json(
        self,
        url: str,
        *,
        subscriptions: Sequence[JsonObject],
        heartbeat: JsonObject | str,
    ) -> AsyncIterator[JsonObject]:
        attempts = 0
        delay = self._reconnect.initial_delay_seconds
        while (
            self._reconnect.maximum_attempts is None or attempts < self._reconnect.maximum_attempts
        ):
            disconnected = False
            try:
                timeout = aiohttp.ClientWSTimeout(ws_close=self._connect_timeout)
                async with self._session.ws_connect(
                    url, timeout=timeout, heartbeat=self._heartbeat_seconds
                ) as socket:
                    for subscription in subscriptions:
                        await socket.send_json(subscription)
                    while True:
                        try:
                            message = await socket.receive(timeout=self._receive_timeout)
                        except TimeoutError:
                            if isinstance(heartbeat, str):
                                await socket.send_str(heartbeat)
                            else:
                                await socket.send_json(heartbeat)
                            continue
                        if message.type is aiohttp.WSMsgType.TEXT:
                            try:
                                payload: Any = message.json()
                            except (TypeError, ValueError):
                                continue
                            if isinstance(payload, Mapping):
                                attempts = 0
                                delay = self._reconnect.initial_delay_seconds
                                yield payload
                        elif message.type in {
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        }:
                            disconnected = True
                            break
            except (aiohttp.ClientError, TimeoutError, ConnectionError):
                attempts += 1
                if (
                    self._reconnect.maximum_attempts is not None
                    and attempts >= self._reconnect.maximum_attempts
                ):
                    raise
                await asyncio.sleep(delay)
                delay = min(
                    delay * self._reconnect.multiplier, self._reconnect.maximum_delay_seconds
                )
            else:
                if disconnected:
                    attempts += 1
                    if (
                        self._reconnect.maximum_attempts is not None
                        and attempts >= self._reconnect.maximum_attempts
                    ):
                        raise ConnectionError("websocket reconnect attempts exhausted")
                    await asyncio.sleep(delay)
                    delay = min(
                        delay * self._reconnect.multiplier,
                        self._reconnect.maximum_delay_seconds,
                    )
