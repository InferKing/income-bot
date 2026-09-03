from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from income_tg.market_data.adapters.base import JsonObject
from income_tg.market_data.adapters.bybit import BybitAdapter
from income_tg.market_data.adapters.okx import OkxAdapter
from income_tg.market_data.schemas import DataSource, Instrument, InstrumentKind, Side


class FakeRest:
    def __init__(self, responses: list[JsonObject]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, Mapping[str, str] | None]] = []

    async def request_json(
        self, method: str, url: str, *, params: Mapping[str, str] | None = None
    ) -> JsonObject:
        self.calls.append((method, url, params))
        return self.responses.pop(0)


class FakeWebSocket:
    def __init__(self, messages: list[JsonObject]) -> None:
        self.messages = messages
        self.subscriptions: Sequence[JsonObject] = ()

    async def stream_json(
        self,
        url: str,
        *,
        subscriptions: Sequence[JsonObject],
        heartbeat: JsonObject | str,
    ) -> AsyncIterator[JsonObject]:
        del url, heartbeat
        self.subscriptions = subscriptions
        for message in self.messages:
            yield message


@pytest.mark.asyncio
async def test_bybit_rest_candles_are_canonical_and_sorted() -> None:
    rest = FakeRest(
        [
            {
                "retCode": 0,
                "result": {
                    "list": [
                        ["1720000060000", "11", "13", "10", "12", "2", "24"],
                        ["1720000000000", "10", "12", "9", "11", "3", "33"],
                    ]
                },
            }
        ]
    )
    adapter = BybitAdapter(rest, FakeWebSocket([]))
    instrument = Instrument("btc")
    candles = await adapter.get_candles(
        instrument,
        "1m",
        datetime(2024, 7, 3, tzinfo=UTC),
        datetime(2024, 7, 4, tzinfo=UTC),
    )

    assert [item.close for item in candles] == [Decimal("11"), Decimal("12")]
    assert candles[0].source is DataSource.BYBIT
    assert rest.calls[0][2] is not None
    assert rest.calls[0][2]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_bybit_trade_stream_normalizes_decimal_and_side() -> None:
    ws = FakeWebSocket(
        [
            {
                "topic": "publicTrade.BTCUSDT",
                "data": [{"i": "t1", "T": 1720000000000, "p": "60000.1", "v": "0.2", "S": "Buy"}],
            }
        ]
    )
    adapter = BybitAdapter(FakeRest([]), ws)

    trades = [item async for item in adapter.stream_trades(Instrument("BTC"))]

    assert trades[0].price == Decimal("60000.1")
    assert trades[0].quantity_base == Decimal("0.2")
    assert trades[0].taker_side is Side.BUY
    assert ws.subscriptions[0]["args"] == ["publicTrade.BTCUSDT"]


@pytest.mark.asyncio
async def test_bybit_orderbook_preserves_sequence() -> None:
    ws = FakeWebSocket(
        [
            {
                "topic": "orderbook.50.BTCUSDT",
                "type": "delta",
                "ts": 1720000000000,
                "data": {"u": 12, "pu": 11, "b": [["10", "2"]], "a": [["11", "3"]]},
            }
        ]
    )
    adapter = BybitAdapter(FakeRest([]), ws)

    updates = [item async for item in adapter.stream_orderbook(Instrument("BTC"))]

    assert updates[0].sequence == 12
    assert updates[0].previous_sequence == 11
    assert updates[0].bids[0].quantity_base == Decimal("2")


@pytest.mark.asyncio
async def test_bybit_derivatives_use_backward_as_of_funding_only() -> None:
    observation_ms = 1_720_000_000_000
    rest = FakeRest(
        [
            {
                "retCode": 0,
                "result": {"list": [{"timestamp": str(observation_ms), "openInterest": "12"}]},
            },
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {"fundingRateTimestamp": str(observation_ms - 1), "fundingRate": "0.001"},
                        {"fundingRateTimestamp": str(observation_ms + 1), "fundingRate": "0.009"},
                    ]
                },
            },
            {
                "retCode": 0,
                "result": {
                    "list": [
                        [str(observation_ms - 3_600_000), "10", "12", "9", "11"],
                        [str(observation_ms), "20", "22", "19", "21"],
                    ]
                },
            },
            {
                "retCode": 0,
                "result": {
                    "list": [
                        [str(observation_ms - 3_600_000), "9", "11", "8", "10"],
                        [str(observation_ms), "19", "21", "18", "20"],
                    ]
                },
            },
        ]
    )
    adapter = BybitAdapter(rest, FakeWebSocket([]))
    instrument = Instrument("BTC", kind=InstrumentKind.LINEAR_PERPETUAL)

    metrics = await adapter.get_derivatives_metrics(
        instrument,
        datetime(2024, 7, 1, tzinfo=UTC),
        datetime(2024, 7, 5, tzinfo=UTC),
    )

    assert metrics[0].funding_rate == Decimal("0.001")
    assert metrics[0].mark_price == Decimal("11")
    assert metrics[0].index_price == Decimal("10")
    assert len(rest.calls) == 4
    assert rest.calls[2][1].endswith("/v5/market/mark-price-kline")
    assert rest.calls[3][1].endswith("/v5/market/index-price-kline")


@pytest.mark.asyncio
async def test_okx_rest_candle_has_same_decimal_units() -> None:
    rest = FakeRest(
        [{"code": "0", "data": [["1720000000000", "10", "12", "9", "11", "3", "3", "33", "1"]]}]
    )
    adapter = OkxAdapter(rest, FakeWebSocket([]))
    candles = await adapter.get_candles(
        Instrument("BTC"),
        "1m",
        datetime(2024, 7, 3, tzinfo=UTC),
        datetime(2024, 7, 4, tzinfo=UTC),
    )

    assert candles[0].close == Decimal("11")
    assert candles[0].volume_base == Decimal("3")
    assert candles[0].turnover_quote == Decimal("33")
    assert candles[0].source is DataSource.OKX


@pytest.mark.asyncio
async def test_okx_uses_swap_symbol_for_linear_perpetual() -> None:
    rest = FakeRest(
        [{"code": "0", "data": [{"tickSz": "0.1", "lotSz": "0.001", "minSz": "0.001"}]}]
    )
    adapter = OkxAdapter(rest, FakeWebSocket([]))
    instrument = Instrument("ETH", kind=InstrumentKind.LINEAR_PERPETUAL)

    spec = await adapter.get_instrument_spec(instrument)

    assert spec.quantity_step == Decimal("0.001")
    assert rest.calls[0][2] == {"instType": "SWAP", "instId": "ETH-USDT-SWAP"}


@pytest.mark.asyncio
async def test_okx_snapshot_normalizes_negative_previous_sequence() -> None:
    ws = FakeWebSocket(
        [
            {
                "arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
                "action": "snapshot",
                "data": [
                    {
                        "ts": "1720000000000",
                        "seqId": 12,
                        "prevSeqId": -1,
                        "bids": [["10", "2", "0", "1"]],
                        "asks": [["11", "3", "0", "1"]],
                    }
                ],
            }
        ]
    )
    adapter = OkxAdapter(FakeRest([]), ws)
    instrument = Instrument("BTC", kind=InstrumentKind.LINEAR_PERPETUAL)

    updates = [item async for item in adapter.stream_orderbook(instrument)]

    assert updates[0].sequence == 12
    assert updates[0].previous_sequence is None
    assert updates[0].is_snapshot
