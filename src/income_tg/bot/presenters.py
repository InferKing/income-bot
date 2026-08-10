from __future__ import annotations

from decimal import Decimal

from income_tg.common.money import format_decimal
from income_tg.portfolio.schemas import PortfolioBalance


def render_portfolios(portfolios: list[PortfolioBalance], manual_usdt_rub_rate: Decimal) -> str:
    parts = ["<b>Портфели</b>"]
    for portfolio in portfolios:
        label = "Реальный" if portfolio.kind == "REAL_MANUAL" else "Виртуальный"
        parts.append(f"\n<b>{label}: {portfolio.name}</b>")
        if not portfolio.balances:
            parts.append("Остатки пока не внесены.")
            continue
        for asset, amount in sorted(portfolio.balances.items()):
            parts.append(f"• {asset}: <code>{format_decimal(amount)}</code>")
        parts.extend(_render_cash_conversion(portfolio.balances, manual_usdt_rub_rate))
    return "\n".join(parts)


def _render_cash_conversion(
    balances: dict[str, Decimal], manual_usdt_rub_rate: Decimal
) -> list[str]:
    if manual_usdt_rub_rate <= 0:
        return ["<i>Конвертация RUB/USDT появится после настройки курса.</i>"]
    rub = balances.get("RUB", Decimal("0"))
    usdt = balances.get("USDT", Decimal("0"))
    result: list[str] = []
    if rub:
        result.append(
            f"≈ <code>{format_decimal(rub / manual_usdt_rub_rate)}</code> USDT по ручному курсу"
        )
    if usdt:
        result.append(
            f"≈ <code>{format_decimal(usdt * manual_usdt_rub_rate)}</code> RUB по ручному курсу"
        )
    return result
