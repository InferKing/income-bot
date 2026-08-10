from decimal import Decimal

import pytest

from income_tg.common.money import (
    MoneyValidationError,
    format_decimal,
    normalize_asset,
    parse_nonnegative_decimal,
    parse_positive_decimal,
)


def test_money_helpers_normalize_human_input() -> None:
    assert normalize_asset(" btc ") == "BTC"
    assert parse_positive_decimal("10,50") == Decimal("10.50")
    assert parse_nonnegative_decimal("0") == Decimal("0")
    assert format_decimal(Decimal("10.500000")) == "10.5"


@pytest.mark.parametrize("value", ["0", "-1", "nan", "abc"])
def test_positive_decimal_rejects_invalid_values(value: str) -> None:
    with pytest.raises(MoneyValidationError):
        parse_positive_decimal(value)


@pytest.mark.parametrize("value", ["-1", "nan", "abc"])
def test_nonnegative_decimal_rejects_invalid_values(value: str) -> None:
    with pytest.raises(MoneyValidationError):
        parse_nonnegative_decimal(value)
