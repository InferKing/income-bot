from datetime import UTC, datetime
from decimal import Decimal

import pytest

from income_tg.market_data.orderbook import (
    LocalOrderBook,
    OrderBookNotReadyError,
    OrderBookSequenceGapError,
)
from income_tg.market_data.schemas import (
    DataSource,
    Instrument,
    OrderBookLevel,
    OrderBookUpdate,
)


def update(
    sequence: int,
    *,
    previous: int | None = None,
    snapshot: bool = False,
    bids: tuple[tuple[str, str], ...] = (("100", "2"),),
    asks: tuple[tuple[str, str], ...] = (("101", "3"),),
) -> OrderBookUpdate:
    return OrderBookUpdate(
        instrument=Instrument("BTC"),
        occurred_at=datetime.now(UTC),
        bids=tuple(OrderBookLevel(Decimal(price), Decimal(size)) for price, size in bids),
        asks=tuple(OrderBookLevel(Decimal(price), Decimal(size)) for price, size in asks),
        sequence=sequence,
        previous_sequence=previous,
        is_snapshot=snapshot,
        source=DataSource.BYBIT,
    )


def test_snapshot_and_delta_build_sorted_book() -> None:
    book = LocalOrderBook(Instrument("BTC"), depth=2)
    book.apply(
        update(
            10,
            snapshot=True,
            bids=(("100", "2"), ("99", "1")),
            asks=(("101", "3"), ("102", "4")),
        )
    )
    book.apply(update(11, previous=10, bids=(("100", "0"), ("100.5", "5")), asks=()))

    view = book.view()
    assert [level.price for level in view.bids] == [Decimal("100.5"), Decimal("99")]
    assert view.mid_price == Decimal("100.75")


def test_sequence_gap_invalidates_until_snapshot() -> None:
    book = LocalOrderBook(Instrument("BTC"))
    book.apply(update(10, snapshot=True))

    with pytest.raises(OrderBookSequenceGapError):
        book.apply(update(12, previous=9))
    assert not book.valid
    with pytest.raises(OrderBookNotReadyError):
        book.apply(update(13, previous=12))

    book.apply(update(20, snapshot=True))
    assert book.valid
    assert book.view().sequence == 20
