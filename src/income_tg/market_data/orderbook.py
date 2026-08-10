"""Deterministic local order book with snapshot/delta sequence protection."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from income_tg.market_data.schemas import Instrument, OrderBookLevel, OrderBookUpdate


class OrderBookError(RuntimeError):
    pass


class OrderBookNotReadyError(OrderBookError):
    pass


class OrderBookSequenceGapError(OrderBookError):
    pass


@dataclass(frozen=True, slots=True)
class OrderBookView:
    instrument: Instrument
    sequence: int
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2


class LocalOrderBook:
    def __init__(self, instrument: Instrument, *, depth: int = 50) -> None:
        if depth <= 0:
            raise ValueError("depth must be positive")
        self.instrument = instrument
        self.depth = depth
        self._bids: dict[Decimal, Decimal] = {}
        self._asks: dict[Decimal, Decimal] = {}
        self._sequence: int | None = None
        self._valid = False

    @property
    def valid(self) -> bool:
        return self._valid

    def invalidate(self) -> None:
        self._valid = False
        self._bids.clear()
        self._asks.clear()

    def apply(self, update: OrderBookUpdate) -> None:
        if update.instrument != self.instrument:
            raise ValueError("order-book update belongs to another instrument")
        if update.is_snapshot:
            self._bids = self._replace(update.bids)
            self._asks = self._replace(update.asks)
            self._sequence = update.sequence
            self._valid = True
            self._validate_not_crossed()
            return
        if not self._valid or self._sequence is None:
            raise OrderBookNotReadyError("delta received before a fresh snapshot")
        if update.previous_sequence is not None:
            contiguous = update.previous_sequence == self._sequence
        else:
            contiguous = update.sequence > self._sequence
        if not contiguous:
            previous = self._sequence
            self.invalidate()
            raise OrderBookSequenceGapError(
                f"expected update after sequence {previous}, got "
                f"previous={update.previous_sequence}, sequence={update.sequence}"
            )
        self._merge(self._bids, update.bids)
        self._merge(self._asks, update.asks)
        self._sequence = update.sequence
        self._trim()
        self._validate_not_crossed()

    def view(self) -> OrderBookView:
        if not self._valid or self._sequence is None:
            raise OrderBookNotReadyError("order book requires a new snapshot")
        return OrderBookView(
            instrument=self.instrument,
            sequence=self._sequence,
            bids=tuple(
                OrderBookLevel(price, quantity)
                for price, quantity in sorted(self._bids.items(), reverse=True)[: self.depth]
            ),
            asks=tuple(
                OrderBookLevel(price, quantity)
                for price, quantity in sorted(self._asks.items())[: self.depth]
            ),
        )

    @staticmethod
    def _replace(levels: tuple[OrderBookLevel, ...]) -> dict[Decimal, Decimal]:
        return {level.price: level.quantity_base for level in levels if level.quantity_base > 0}

    @staticmethod
    def _merge(target: dict[Decimal, Decimal], updates: tuple[OrderBookLevel, ...]) -> None:
        for level in updates:
            if level.quantity_base == 0:
                target.pop(level.price, None)
            elif level.quantity_base > 0:
                target[level.price] = level.quantity_base

    def _trim(self) -> None:
        self._bids = dict(sorted(self._bids.items(), reverse=True)[: self.depth])
        self._asks = dict(sorted(self._asks.items())[: self.depth])

    def _validate_not_crossed(self) -> None:
        view = self.view()
        if (
            view.best_bid is not None
            and view.best_ask is not None
            and view.best_bid >= view.best_ask
        ):
            self.invalidate()
            raise OrderBookError("crossed order book")
