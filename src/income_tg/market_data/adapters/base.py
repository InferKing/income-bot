"""Ports implemented by exchanges and their transports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

from income_tg.market_data.schemas import (
    AdapterHealth,
    Candle,
    DerivativesMetrics,
    Instrument,
    InstrumentSpec,
    OrderBookUpdate,
    Trade,
)

JsonObject = Mapping[str, Any]


class RestTransport(Protocol):
    async def request_json(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> JsonObject: ...


class WebSocketTransport(Protocol):
    def stream_json(
        self,
        url: str,
        *,
        subscriptions: Sequence[JsonObject],
        heartbeat: JsonObject | str,
    ) -> AsyncIterator[JsonObject]: ...


class MarketDataAdapter(ABC):
    @abstractmethod
    def stream_trades(self, instrument: Instrument) -> AsyncIterator[Trade]: ...

    @abstractmethod
    def stream_orderbook(
        self, instrument: Instrument, depth: int = 50
    ) -> AsyncIterator[OrderBookUpdate]: ...

    @abstractmethod
    def stream_candles(self, instrument: Instrument, interval: str) -> AsyncIterator[Candle]: ...

    @abstractmethod
    async def get_candles(
        self,
        instrument: Instrument,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Candle]: ...

    @abstractmethod
    async def get_derivatives_metrics(
        self, instrument: Instrument, start: datetime, end: datetime
    ) -> list[DerivativesMetrics]: ...

    @abstractmethod
    async def get_instrument_spec(self, instrument: Instrument) -> InstrumentSpec: ...

    @abstractmethod
    async def health(self) -> AdapterHealth: ...
