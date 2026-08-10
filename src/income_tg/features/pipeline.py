from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from income_tg.features.derivatives import derivatives_features
from income_tg.features.microstructure import microstructure_features
from income_tg.features.technical import technical_features


class LookaheadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CandleInput:
    close_time: datetime
    close: float
    high: float
    low: float
    volume: float


@dataclass(frozen=True, slots=True)
class MarketObservation:
    instrument: str
    as_of: datetime
    data_cutoff: datetime
    candles: tuple[CandleInput, ...]
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    aggressive_buy_volume: float
    aggressive_sell_volume: float
    orderbook_at: datetime
    trade_flow_at: datetime
    derivatives_at: datetime
    funding_rate: float = 0.0
    open_interest: float = 0.0
    previous_open_interest: float = 0.0
    mark_price: float = 0.0
    index_price: float = 0.0
    long_liquidations: float = 0.0
    short_liquidations: float = 0.0


@dataclass(frozen=True, slots=True)
class FeatureVector:
    instrument: str
    as_of: datetime
    data_cutoff: datetime
    names: tuple[str, ...]
    values: tuple[float, ...]

    def as_dict(self) -> dict[str, float]:
        return dict(zip(self.names, self.values, strict=True))


class FeaturePipeline:
    def build(self, observation: MarketObservation) -> FeatureVector:
        if observation.as_of.tzinfo is None or observation.as_of.utcoffset() is None:
            raise LookaheadError("as_of must be timezone-aware")
        if observation.data_cutoff > observation.as_of:
            raise LookaheadError("data_cutoff не может находиться в будущем относительно as_of")
        if any(candle.close_time > observation.as_of for candle in observation.candles):
            raise LookaheadError("Обнаружена свеча из будущего")
        source_times = (
            *(candle.close_time for candle in observation.candles),
            observation.orderbook_at,
            observation.trade_flow_at,
            observation.derivatives_at,
        )
        if any(value.tzinfo is None or value.utcoffset() is None for value in source_times):
            raise LookaheadError("all source timestamps must be timezone-aware")
        if any(value > observation.as_of for value in source_times):
            raise LookaheadError("future source data detected")
        effective_cutoff = max(source_times, default=observation.data_cutoff)
        candles = sorted(observation.candles, key=lambda candle: candle.close_time)
        technical = technical_features(
            [item.close for item in candles],
            [item.high for item in candles],
            [item.low for item in candles],
            [item.volume for item in candles],
        )
        microstructure = microstructure_features(
            observation.bids,
            observation.asks,
            observation.aggressive_buy_volume,
            observation.aggressive_sell_volume,
        )
        derivatives = derivatives_features(
            funding_rate=observation.funding_rate,
            open_interest=observation.open_interest,
            previous_open_interest=observation.previous_open_interest,
            mark_price=observation.mark_price,
            index_price=observation.index_price,
            long_liquidations=observation.long_liquidations,
            short_liquidations=observation.short_liquidations,
        )
        values = {**technical, **microstructure, **derivatives}
        names = tuple(sorted(values))
        return FeatureVector(
            instrument=observation.instrument,
            as_of=observation.as_of,
            data_cutoff=effective_cutoff,
            names=names,
            values=tuple(values[name] for name in names),
        )
