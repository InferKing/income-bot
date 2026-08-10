from enum import StrEnum


class PortfolioKind(StrEnum):
    REAL_MANUAL = "REAL_MANUAL"
    PAPER = "PAPER"


class PortfolioEventType(StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    TRADE = "TRADE"
    ADJUSTMENT = "ADJUSTMENT"
    REVERSAL = "REVERSAL"


class EventSource(StrEnum):
    BOT = "BOT"
    BOOTSTRAP = "BOOTSTRAP"
    SYSTEM = "SYSTEM"


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
