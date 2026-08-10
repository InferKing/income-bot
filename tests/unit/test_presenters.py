from decimal import Decimal
from uuid import uuid4

from income_tg.bot.presenters import render_portfolios
from income_tg.portfolio.schemas import PortfolioBalance


def test_portfolio_presenter_converts_cash_with_manual_rate() -> None:
    text = render_portfolios(
        [
            PortfolioBalance(
                portfolio_id=uuid4(),
                name="Виртуальный портфель",
                kind="PAPER",
                balances={"RUB": Decimal("100000")},
            )
        ],
        Decimal("100"),
    )
    assert "100000" in text
    assert "1000" in text
    assert "USDT" in text
