from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

ASSET_PATTERN = re.compile(r"^[A-Z0-9]{2,16}$")


class MoneyValidationError(ValueError):
    pass


def normalize_asset(value: str) -> str:
    asset = value.strip().upper()
    if not ASSET_PATTERN.fullmatch(asset):
        raise MoneyValidationError(f"Некорректный код актива: {value!r}")
    return asset


def parse_positive_decimal(value: str | Decimal, field: str = "amount") -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(value.replace(",", "."))
    except (InvalidOperation, AttributeError) as error:
        raise MoneyValidationError(f"{field}: требуется число") from error
    if not number.is_finite() or number <= 0:
        raise MoneyValidationError(f"{field}: значение должно быть больше нуля")
    return number


def parse_nonnegative_decimal(value: str | Decimal, field: str = "amount") -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(value.replace(",", "."))
    except (InvalidOperation, AttributeError) as error:
        raise MoneyValidationError(f"{field}: требуется число") from error
    if not number.is_finite() or number < 0:
        raise MoneyValidationError(f"{field}: значение не может быть отрицательным")
    return number


def format_decimal(value: Decimal) -> str:
    rendered = format(value.normalize(), "f")
    return "0" if rendered == "-0" else rendered
