"""Bybit V5 public market-data adapter."""

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
    bybit_interval,
    bybit_symbol,
    decimal_value,
    interval_seconds,
    levels,
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


class BybitApiError(RuntimeError):
    pass


class BybitAdapter(MarketDataAdapter):
    REST_URL = "https://api.bybit.com"
    SPOT_WS_URL = "wss://stream.bybit.com/v5/public/spot"
    LINEAR_WS_URL = "wss://stream.bybit.com/v5/public/linear"

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
    def _category(instrument: Instrument) -> str:
        return "spot" if instrument.kind is InstrumentKind.SPOT else "linear"

    @staticmethod
    def _result(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if int(payload.get("retCode", -1)) != 0:
            raise BybitApiError(str(payload.get("retMsg", "unknown Bybit error")))
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise BybitApiError("Bybit response has no result object")
        return result

    def _ws_url(self, instrument: Instrument) -> str:
        return self.SPOT_WS_URL if instrument.kind is InstrumentKind.SPOT else self.LINEAR_WS_URL

    async def get_candles(
        self, instrument: Instrument, interval: str, start: datetime, end: datetime
    ) -> list[Candle]:
        payload = await self._rest.request_json(
            "GET",
            f"{self._rest_url}/v5/market/kline",
            params={
                "category": self._category(instrument),
                "symbol": bybit_symbol(instrument),
                "interval": bybit_interval(interval),
                "start": str(int(start.timestamp() * 1000)),
                "end": str(int(end.timestamp() * 1000)),
                "limit": "1000",
            },
        )
        rows = self._result(payload).get("list", [])
        if not isinstance(rows, list):
            raise BybitApiError("Bybit candle list is malformed")
        candles = [self._parse_rest_candle(instrument, interval, row) for row in rows]
        return sorted(candles, key=lambda item: item.opened_at)

    @staticmethod
    def _parse_rest_candle(instrument: Instrument, interval: str, row: Any) -> Candle:
        if not isinstance(row, list) or len(row) < 7:
            raise BybitApiError("Bybit candle row is malformed")
        opened_at = utc_from_milliseconds(row[0])
        return Candle(
            instrument=instrument,
            interval_seconds=interval_seconds(interval),
            opened_at=opened_at,
            open=decimal_value(row[1], field="open"),
            high=decimal_value(row[2], field="high"),
            low=decimal_value(row[3], field="low"),
            close=decimal_value(row[4], field="close"),
            volume_base=decimal_value(row[5], field="volume"),
            turnover_quote=decimal_value(row[6], field="turnover"),
            closed=(
                opened_at.timestamp() + interval_seconds(interval) <= datetime.now(UTC).timestamp()
            ),
            source=DataSource.BYBIT,
        )

    def stream_candles(self, instrument: Instrument, interval: str) -> AsyncIterator[Candle]:
        return self._stream_candles(instrument, interval)

    async def _stream_candles(self, instrument: Instrument, interval: str) -> AsyncIterator[Candle]:
        topic = f"kline.{bybit_interval(interval)}.{bybit_symbol(instrument)}"
        async for payload in self._websocket.stream_json(
            self._ws_url(instrument),
            subscriptions=({"op": "subscribe", "args": [topic]},),
            heartbeat={"op": "ping"},
        ):
            if payload.get("topic") != topic:
                continue
            data = payload.get("data", [])
            if not isinstance(data, list):
                continue
            for row in data:
                if isinstance(row, Mapping):
                    yield Candle(
                        instrument=instrument,
                        interval_seconds=interval_seconds(interval),
                        opened_at=utc_from_milliseconds(row["start"]),
                        open=decimal_value(row["open"], field="open"),
                        high=decimal_value(row["high"], field="high"),
                        low=decimal_value(row["low"], field="low"),
                        close=decimal_value(row["close"], field="close"),
                        volume_base=decimal_value(row["volume"], field="volume"),
                        turnover_quote=optional_decimal(row.get("turnover"), field="turnover"),
                        closed=bool(row.get("confirm", False)),
                        source=DataSource.BYBIT,
                    )

    def stream_trades(self, instrument: Instrument) -> AsyncIterator[Trade]:
        return self._stream_trades(instrument)

    async def _stream_trades(self, instrument: Instrument) -> AsyncIterator[Trade]:
        topic = f"publicTrade.{bybit_symbol(instrument)}"
        async for payload in self._websocket.stream_json(
            self._ws_url(instrument),
            subscriptions=({"op": "subscribe", "args": [topic]},),
            heartbeat={"op": "ping"},
        ):
            if payload.get("topic") != topic:
                continue
            data = payload.get("data", [])
            if not isinstance(data, list):
                continue
            for row in data:
                if isinstance(row, Mapping):
                    yield Trade(
                        instrument=instrument,
                        trade_id=str(row["i"]),
                        occurred_at=utc_from_milliseconds(row["T"]),
                        price=decimal_value(row["p"], field="price"),
                        quantity_base=decimal_value(row["v"], field="quantity"),
                        taker_side=Side.BUY if row["S"] == "Buy" else Side.SELL,
                        source=DataSource.BYBIT,
                    )

    def stream_orderbook(
        self, instrument: Instrument, depth: int = 50
    ) -> AsyncIterator[OrderBookUpdate]:
        return self._stream_orderbook(instrument, depth)

    async def _stream_orderbook(
        self, instrument: Instrument, depth: int
    ) -> AsyncIterator[OrderBookUpdate]:
        topic = f"orderbook.{depth}.{bybit_symbol(instrument)}"
        async for payload in self._websocket.stream_json(
            self._ws_url(instrument),
            subscriptions=({"op": "subscribe", "args": [topic]},),
            heartbeat={"op": "ping"},
        ):
            if payload.get("topic") != topic:
                continue
            row = payload.get("data")
            if not isinstance(row, Mapping):
                continue
            yield OrderBookUpdate(
                instrument=instrument,
                occurred_at=utc_from_milliseconds(payload.get("ts", row.get("ts"))),
                bids=levels(row.get("b", [])),
                asks=levels(row.get("a", [])),
                sequence=int(row["u"]),
                previous_sequence=int(row["pu"]) if row.get("pu") is not None else None,
                is_snapshot=payload.get("type") == "snapshot",
                source=DataSource.BYBIT,
            )

    async def get_derivatives_metrics(
        self, instrument: Instrument, start: datetime, end: datetime
    ) -> list[DerivativesMetrics]:
        if instrument.kind is InstrumentKind.SPOT:
            return []
        common = {
            "category": "linear",
            "symbol": bybit_symbol(instrument),
            "startTime": str(int(start.timestamp() * 1000)),
            "endTime": str(int(end.timestamp() * 1000)),
            "limit": "200",
        }
        open_interest_payload = await self._rest.request_json(
            "GET",
            f"{self._rest_url}/v5/market/open-interest",
            params={**common, "intervalTime": "1h"},
        )
        funding_payload = await self._rest.request_json(
            "GET", f"{self._rest_url}/v5/market/funding/history", params=common
        )
        funding_rows = self._result(funding_payload).get("list", [])
        funding_by_time = {
            int(row["fundingRateTimestamp"]): decimal_value(row["fundingRate"], field="funding")
            for row in funding_rows
            if isinstance(row, Mapping)
        }
        rows = self._result(open_interest_payload).get("list", [])
        result: list[DerivativesMetrics] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            timestamp = int(row["timestamp"])
            # Strict backward as-of join: future funding must never change a past row.
            eligible_funding = (item for item in funding_by_time if item <= timestamp)
            latest_funding = max(eligible_funding, default=None)
            result.append(
                DerivativesMetrics(
                    instrument=instrument,
                    occurred_at=utc_from_milliseconds(timestamp),
                    open_interest_base=optional_decimal(
                        row.get("openInterest"), field="open_interest"
                    ),
                    funding_rate=(
                        funding_by_time[latest_funding] if latest_funding is not None else None
                    ),
                    # The ticker endpoint only exposes the current value. Attaching it to
                    # historical OI would leak the future; historical mark/index series
                    # must be collected separately before these fields can be populated.
                    mark_price=None,
                    index_price=None,
                    source=DataSource.BYBIT,
                )
            )
        return sorted(result, key=lambda item: item.occurred_at)

    async def get_instrument_spec(self, instrument: Instrument) -> InstrumentSpec:
        payload = await self._rest.request_json(
            "GET",
            f"{self._rest_url}/v5/market/instruments-info",
            params={"category": self._category(instrument), "symbol": bybit_symbol(instrument)},
        )
        rows = self._result(payload).get("list", [])
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], Mapping):
            raise BybitApiError("instrument not found")
        row = rows[0]
        lot = row.get("lotSizeFilter", {})
        price = row.get("priceFilter", {})
        if not isinstance(lot, Mapping) or not isinstance(price, Mapping):
            raise BybitApiError("instrument filters are malformed")
        return InstrumentSpec(
            instrument=instrument,
            price_tick=decimal_value(price["tickSize"], field="tick_size"),
            quantity_step=decimal_value(
                lot.get("qtyStep", lot.get("basePrecision")), field="quantity_step"
            ),
            minimum_quantity=decimal_value(lot["minOrderQty"], field="minimum_quantity"),
            minimum_notional=optional_decimal(
                lot.get("minNotionalValue", lot.get("minOrderAmt")),
                field="minimum_notional",
            ),
            maker_fee_rate=None,
            taker_fee_rate=None,
            source=DataSource.BYBIT,
        )

    async def health(self) -> AdapterHealth:
        started = perf_counter()
        try:
            payload = await self._rest.request_json("GET", f"{self._rest_url}/v5/market/time")
            self._result(payload)
        except Exception as exc:  # health checks deliberately convert provider errors to state
            return AdapterHealth(
                source=DataSource.BYBIT,
                healthy=False,
                checked_at=datetime.now(UTC),
                latency_ms=int((perf_counter() - started) * 1000),
                detail=str(exc),
            )
        return AdapterHealth(
            source=DataSource.BYBIT,
            healthy=True,
            checked_at=datetime.now(UTC),
            latency_ms=int((perf_counter() - started) * 1000),
        )
