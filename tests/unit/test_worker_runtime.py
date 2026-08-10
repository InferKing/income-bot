from datetime import UTC, datetime
from decimal import Decimal

import pytest

from income_tg.features.pipeline import CandleInput, FeatureVector, MarketObservation
from income_tg.paper_trading.models import InstrumentKind, PaperPosition, PositionSide
from income_tg.worker.cli import StoredFeatureBuilder, _equity

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _position(side: PositionSide) -> PaperPosition:
    return PaperPosition(
        position_id=f"p-{side.value}",
        symbol="BTC/USDT:PERP",
        instrument=InstrumentKind.PERPETUAL,
        side=side,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        leverage=10,
        margin=Decimal("10"),
        stop_loss=Decimal("98") if side is not PositionSide.SHORT else Decimal("102"),
        take_profit=Decimal("104") if side is not PositionSide.SHORT else Decimal("96"),
        opening_commission=Decimal("0.05"),
        funding_pnl=Decimal("-0.01"),
        opened_at=NOW,
        liquidation_price=Decimal("90") if side is not PositionSide.SHORT else Decimal("110"),
    )


def test_runtime_equity_marks_long_and_short_to_market() -> None:
    assert _equity(Decimal("100"), [_position(PositionSide.LONG)], Decimal("110")) == Decimal(
        "119.99"
    )
    assert _equity(Decimal("100"), [_position(PositionSide.SHORT)], Decimal("110")) == Decimal(
        "99.99"
    )


def test_stored_feature_builder_rejects_different_observation() -> None:
    vector = FeatureVector("BTC", NOW, NOW, ("x",), (1.0,))
    observation = MarketObservation(
        instrument="ETH",
        as_of=NOW,
        data_cutoff=NOW,
        candles=(CandleInput(NOW, 1, 1, 1, 1),),
        bids=((1, 1),),
        asks=((1, 1),),
        aggressive_buy_volume=0,
        aggressive_sell_volume=0,
        orderbook_at=NOW,
        trade_flow_at=NOW,
        derivatives_at=NOW,
    )
    with pytest.raises(ValueError, match="does not belong"):
        StoredFeatureBuilder(vector).build(observation)
