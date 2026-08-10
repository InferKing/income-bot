"""OKX V5 public adapter used as an independent fallback source."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from income_tg.market_data.adapters.base import (
    MarketDataAdapter,
    RestTransport,
    WebSocketTransport,
)
from income_tg.market_data.normalization import (
    decimal_value,
    interval_seconds,
    levels,
    okx_interval,
    okx_symbol,
    optional_decimal,
    utc_from_milliseconds,
)
from income_tg.market_data.schemas import (
    AdapterHealth,
    Candle,
    DataSource,
    DerivativesMetrics,
    Instrument,
    InstrumentKind,
    InstrumentSpec,
    OrderBookUpdate,
    Side,
    Trade,
)


class OkxApiError(RuntimeError):
    pass


class OkxAdapter(MarketDataAdapter):
    REST_URL = "https://www.okx.com"
    WS_URL = "wss://ws.okx.com:8443/ws/v5/public"

    def __init__(
        self,
        rest: RestTransport,
        websocket: WebSocketTransport,
        *,
        rest_url: str = REST_URL,
    ) -> None:
        self._rest = rest
        self._websocket = websocket
        self._rest_url = rest_url.rstrip("/")

    @staticmethod
    def _data(payload: Mapping[str, Any]) -> list[Any]:
        if str(payload.get("code", "-1")) != "0":
            raise OkxApiError(str(payload.get("msg", "unknown OKX error")))
        data = payload.get("data")
        if not isinstance(data, list):
            raise OkxApiError("OKX response has no data list")
        return data

    @staticmethod
    def _instrument_type(instrument: Instrument) -> str:
        return "SPOT" if instrument.kind is InstrumentKind.SPOT else "SWAP"

    async def get_candles(
        self, instrument: Instrument, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        payload = await self._rest.request_json(
            "GET",
            f"{self._rest_url}/api/v5/market/history-candles",
            params={
                "instId": okx_symbol(instrument),
                "bar": okx_interval(interval),
                # OKX returns records older than `after` and newer than `before`.
                "after": str(int(end.timestamp() * 1000)),
                "before": str(int(start.timestamp() * 1000)),
                "limit": "300",
            },
        )
        result = [self._parse_candle(instrument, interval, row) for row in self._data(payload)]
        return sorted(result, key=lambda item: item.opened_at)

    @staticmethod
    def _parse_candle(instrument: Instrument, interval: str, row: Any) -> Candle:
        if not isinstance(row, list) or len(row) < 9:
            raise OkxApiError("OKX candle row is malformed")
        return Candle(
            instrument=instrument,
            interval_seconds=interval_seconds(interval),
            opened_at=utc_from_milliseconds(row[0]),
            open=decimal_value(row[1], field="open"),
            high=decimal_value(row[2], field="high"),
            low=decimal_value(row[3], field="low"),
            close=decimal_value(row[4], field="close"),
            volume_base=decimal_value(row[5], field="volume"),
            turnover_quote=optional_decimal(row[7], field="turnover"),
            closed=str(row[8]) == "1",
            source=DataSource.OKX,
        )

    def stream_candles(self, instrument: Instrument, interval: str) -> AsyncIterator[Candle]:
        return self._stream_candles(instrument, interval)

    async def _stream_candles(self, instrument: Instrument, interval: str) -> AsyncIterator[Candle]:
        channel = f"candle{okx_interval(interval)}"
        subscription = {
            "op": "subscribe",
            "args": [{"channel": channel, "instId": okx_symbol(instrument)}],
        }
        async for payload in self._websocket.stream_json(
            self.WS_URL, subscriptions=(subscription,), heartbeat="ping"
        ):
            argument = payload.get("arg", {})
            if not isinstance(argument, Mapping) or argument.get("channel") != channel:
                continue
            data = payload.get("data", [])
            if isinstance(data, list):
                for row in data:
                    yield self._parse_candle(instrument, interval, row)

    def stream_trades(self, instrument: Instrument) -> AsyncIterator[Trade]:
        return self._stream_trades(instrument)

    async def _stream_trades(self, instrument: Instrument) -> AsyncIterator[Trade]:
        subscription = {
            "op": "subscribe",
            "args": [{"channel": "trades", "instId": okx_symbol(instrument)}],
        }
        async for payload in self._websocket.stream_json(
            self.WS_URL, subscriptions=(subscription,), heartbeat="ping"
        ):
            argument = payload.get("arg", {})
            if not isinstance(argument, Mapping) or argument.get("channel") != "trades":
                continue
            data = payload.get("data", [])
            if not isinstance(data, list):
                continue
            for row in data:
                if isinstance(row, Mapping):
                    yield Trade(
                        instrument=instrument,
                        trade_id=str(row["tradeId"]),
                        occurred_at=utc_from_milliseconds(row["ts"]),
                        price=decimal_value(row["px"], field="price"),
                        quantity_base=decimal_value(row["sz"], field="quantity"),
                        taker_side=Side.BUY if row["side"] == "buy" else Side.SELL,
                        source=DataSource.OKX,
                    )

    def stream_orderbook(
        self, instrument: Instrument, depth: int = 50
    ) -> AsyncIterator[OrderBookUpdate]:
        return self._stream_orderbook(instrument, depth)

    async def _stream_orderbook(
        self, instrument: Instrument, depth: int
    ) -> AsyncIterator[OrderBookUpdate]:
        channel = "books" if depth > 5 else "books5"
        subscription = {
            "op": "subscribe",
            "args": [{"channel": channel, "instId": okx_symbol(instrument)}],
        }
        async for payload in self._websocket.stream_json(
            self.WS_URL, subscriptions=(subscription,), heartbeat="ping"
        ):
            argument = payload.get("arg", {})
            if not isinstance(argument, Mapping) or argument.get("channel") != channel:
                continue
            data = payload.get("data", [])
            if not isinstance(data, list):
                continue
            for row in data:
                if not isinstance(row, Mapping):
                    continue
                yield OrderBookUpdate(
                    instrument=instrument,
                    occurred_at=utc_from_milliseconds(row["ts"]),
                    bids=levels(row.get("bids", [])),
                    asks=levels(row.get("asks", [])),
                    sequence=int(row.get("seqId", 0)),
                    previous_sequence=(
                        int(row["prevSeqId"]) if row.get("prevSeqId") is not None else None
                    ),
                    is_snapshot=payload.get("action", "snapshot") == "snapshot",
                    source=DataSource.OKX,
                )

    async def get_derivatives_metrics(
        self, instrument: Instrument, start: datetime, end: datetime
    ) -> list[DerivativesMetrics]:
        del start, end  # OKX public endpoints expose current OI/funding without an auth token.
        if instrument.kind is InstrumentKind.SPOT:
            return []
        open_interest_payload = await self._rest.request_json(
            "GET",
            f"{self._rest_url}/api/v5/public/open-interest",
            params={"instType": "SWAP", "instId": okx_symbol(instrument)},
        )
        funding_payload = await self._rest.request_json(
            "GET",
            f"{self._rest_url}/api/v5/public/funding-rate",
            params={"instId": okx_symbol(instrument)},
        )
        ticker_payload = await self._rest.request_json(
            "GET",
            f"{self._rest_url}/api/v5/market/ticker",
            params={"instId": okx_symbol(instrument)},
        )
        oi_rows = self._data(open_interest_payload)
        funding_rows = self._data(funding_payload)
        ticker_rows = self._data(ticker_payload)
        if not oi_rows or not isinstance(oi_rows[0], Mapping):
            return []
        oi = oi_rows[0]
        funding = funding_rows[0] if funding_rows and isinstance(funding_rows[0], Mapping) else {}
        ticker = ticker_rows[0] if ticker_rows and isinstance(ticker_rows[0], Mapping) else {}
        timestamp = oi.get("ts") or funding.get("fundingTime")
        return [
            DerivativesMetrics(
                instrument=instrument,
                occurred_at=utc_from_milliseconds(timestamp),
                open_interest_base=optional_decimal(oi.get("oiCcy"), field="open_interest"),
                funding_rate=optional_decimal(funding.get("fundingRate"), field="funding"),
                mark_price=optional_decimal(ticker.get("last"), field="mark_price"),
                index_price=None,
                source=DataSource.OKX,
            )
        ]

    async def get_instrument_spec(self, instrument: Instrument) -> InstrumentSpec:
        payload = await self._rest.request_json(
            "GET",
            f"{self._rest_url}/api/v5/public/instruments",
            params={
                "instType": self._instrument_type(instrument),
                "instId": okx_symbol(instrument),
            },
        )
        rows = self._data(payload)
        if not rows or not isinstance(rows[0], Mapping):
            raise OkxApiError("instrument not found")
        row = rows[0]
        return InstrumentSpec(
            instrument=instrument,
            price_tick=decimal_value(row["tickSz"], field="tick_size"),
            quantity_step=decimal_value(row["lotSz"], field="quantity_step"),
            minimum_quantity=decimal_value(row["minSz"], field="minimum_quantity"),
            minimum_notional=None,
            maker_fee_rate=None,
            taker_fee_rate=None,
            source=DataSource.OKX,
        )

    async def health(self) -> AdapterHealth:
        started = perf_counter()
        try:
            payload = await self._rest.request_json("GET", f"{self._rest_url}/api/v5/public/time")
            self._data(payload)
        except Exception as exc:  # health checks deliberately convert provider errors to state
            return AdapterHealth(
                source=DataSource.OKX,
                healthy=False,
                checked_at=datetime.now(UTC),
                latency_ms=int((perf_counter() - started) * 1000),
                detail=str(exc),
            )
        return AdapterHealth(
            source=DataSource.OKX,
            healthy=True,
            checked_at=datetime.now(UTC),
            latency_ms=int((perf_counter() - started) * 1000),
        )
