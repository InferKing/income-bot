from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from income_tg.features.pipeline import (
    CandleInput,
    FeaturePipeline,
    LookaheadError,
    MarketObservation,
)


def _observation() -> MarketObservation:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    candles = tuple(
        CandleInput(
            close_time=now - timedelta(minutes=20 - index),
            close=100 + index,
            high=101 + index,
            low=99 + index,
            volume=1000 + index * 10,
        )
        for index in range(20)
    )
    return MarketObservation(
        instrument="BTCUSDT",
        as_of=now,
        data_cutoff=now,
        candles=candles,
        bids=((119.0, 2.0), (118.5, 3.0)),
        asks=((120.0, 1.0), (120.5, 1.0)),
        aggressive_buy_volume=10,
        aggressive_sell_volume=5,
        orderbook_at=now,
        trade_flow_at=now,
        derivatives_at=now,
        funding_rate=0.0001,
        open_interest=1100,
        previous_open_interest=1000,
        mark_price=119.7,
        index_price=119.5,
        long_liquidations=2,
        short_liquidations=5,
    )


def test_feature_pipeline_builds_stable_sorted_vector() -> None:
    vector = FeaturePipeline().build(_observation())
    assert vector.names == tuple(sorted(vector.names))
    assert len(vector.names) == len(vector.values) == 17
    assert vector.as_dict()["open_interest_change"] == pytest.approx(0.1)
    assert vector.as_dict()["trade_flow_imbalance"] == pytest.approx(1 / 3)


def test_feature_pipeline_rejects_future_data() -> None:
    observation = _observation()
    future_candle = CandleInput(
        close_time=observation.as_of + timedelta(minutes=1),
        close=121,
        high=122,
        low=120,
        volume=1,
    )
    with pytest.raises(LookaheadError):
        FeaturePipeline().build(replace(observation, candles=(*observation.candles, future_candle)))


@pytest.mark.parametrize("field", ["orderbook_at", "trade_flow_at", "derivatives_at"])
def test_feature_pipeline_rejects_future_non_candle_source(field: str) -> None:
    observation = _observation()
    with pytest.raises(LookaheadError, match="future source"):
        FeaturePipeline().build(
            replace(observation, **{field: observation.as_of + timedelta(seconds=1)})
        )
