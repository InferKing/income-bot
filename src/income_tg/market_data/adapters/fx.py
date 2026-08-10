"""USDT/RUB rate adapter with an injectable timeout-bounded transport."""

from __future__ import annotations

from datetime import UTC, datetime

from income_tg.market_data.adapters.base import RestTransport
from income_tg.market_data.normalization import decimal_value
from income_tg.market_data.schemas import FxRate


class CoinGeckoFxAdapter:
    URL = "https://api.coingecko.com/api/v3/simple/price"

    def __init__(self, rest: RestTransport, *, url: str = URL) -> None:
        self._rest = rest
        self._url = url

    async def get_usdt_rub(self) -> FxRate:
        payload = await self._rest.request_json(
            "GET", self._url, params={"ids": "tether", "vs_currencies": "rub"}
        )
        tether = payload.get("tether")
        if not isinstance(tether, dict) or "rub" not in tether:
            raise ValueError("USDT/RUB rate is absent from provider response")
        return FxRate(
            base="USDT",
            quote="RUB",
            rate=decimal_value(tether["rub"], field="usdt_rub"),
            observed_at=datetime.now(UTC),
            source="coingecko",
        )
