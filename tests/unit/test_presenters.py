from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from income_tg.bot.presenters import (
    SystemHealthItem,
    render_portfolios,
    render_system_status,
)
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
    assert "курс <code>100</code> RUB/USDT" in text


def test_system_presenter_explains_learning_state_and_freshness() -> None:
    now = datetime(2026, 8, 17, 16, 0, tzinfo=UTC)
    text = render_system_status(
        [
            SystemHealthItem("DATABASE", "readiness-1", "HEALTHY", "DATABASE_OK", now),
            SystemHealthItem("BOT", "telegram-bot-1", "HEALTHY", "RUNNING", now),
            SystemHealthItem(
                "MARKET", "collector-bybit", "HEALTHY", "RUNNING", now - timedelta(seconds=7)
            ),
            SystemHealthItem("MODEL", "readiness-1", "UNHEALTHY", "MODEL_POINTER_MISSING", now),
        ],
        model=None,
        feature_vectors=42,
        training_vectors=12,
        labeled_training_vectors=10,
        signals=3,
        latest_feature_at=now - timedelta(minutes=1),
        environment="production",
        paper_only=True,
        market_sources="BYBIT,OKX",
        symbols="BTCUSDT,ETHUSDT",
        now=now,
    )

    assert "Сервисы работают, модель обучается" in text
    assert "База данных" in text
    assert "7 сек. назад" in text
    assert "Champion-модель ещё не назначена" in text
    assert "Векторов признаков: <b>42</b>" in text
    assert "<b>10</b> размечено из 12" in text
    assert "До первого обучения: ещё 30" in text
    assert "только paper trading" in text


def test_system_presenter_marks_stale_non_model_service_as_problem() -> None:
    now = datetime(2026, 8, 17, 16, 0, tzinfo=UTC)
    text = render_system_status(
        [
            SystemHealthItem(
                "BOT", "telegram-bot-1", "HEALTHY", "RUNNING", now - timedelta(minutes=2)
            )
        ],
        model=None,
        feature_vectors=0,
        training_vectors=0,
        labeled_training_vectors=0,
        signals=0,
        latest_feature_at=None,
        environment="production",
        paper_only=True,
        market_sources="BYBIT",
        symbols="BTCUSDT",
        now=now,
    )

    assert "Часть системы требует внимания" in text
    assert "🔴 <b>Telegram-бот</b>" in text
