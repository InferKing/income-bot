from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from income_tg.bot.presenters import (
    SystemHealthItem,
    SystemTrainingInfo,
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
        training=None,
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
        training=None,
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


def test_system_presenter_shows_rejected_training_metrics_and_reasons() -> None:
    now = datetime(2026, 8, 20, 9, 27, tzinfo=UTC)
    finished_at = now - timedelta(minutes=5)
    text = render_system_status(
        [SystemHealthItem("MODEL", "readiness-1", "UNHEALTHY", "MODEL_POINTER_MISSING", now)],
        model=None,
        training=SystemTrainingInfo(
            attempt_count=247,
            status="REJECTED",
            started_at=finished_at - timedelta(seconds=4),
            finished_at=finished_at,
            next_attempt_at=finished_at + timedelta(minutes=15),
            net_return=Decimal("-0.0022266"),
            max_drawdown=Decimal("0.0022266"),
            profit_factor=Decimal("0"),
            closed_trades=1,
            test_samples=159,
            required_closed_trades=32,
            required_trade_fraction=Decimal("0.20"),
            admission_reasons=(
                "NET_RETURN_NOT_POSITIVE",
                "PROFIT_FACTOR_TOO_LOW",
                "NOT_ENOUGH_TRADES",
                "DOES_NOT_BEAT_BASELINE",
                "RECENT_PERIOD_UNSTABLE",
            ),
        ),
        feature_vectors=6340,
        training_vectors=792,
        labeled_training_vectors=790,
        signals=0,
        latest_feature_at=now - timedelta(minutes=5),
        environment="development",
        paper_only=True,
        market_sources="BYBIT,OKX",
        symbols="BTCUSDT,ETHUSDT,TONUSDT",
        now=now,
    )

    assert "champion ещё не принят" in text
    assert "кандидат отклонён" in text
    assert "Попыток: <b>247</b>" in text
    assert "Доходность: <code>-0.22</code>%" in text
    assert "Сделок: <b>1</b> из необходимых <b>32</b> (20% теста)" in text
    assert "недостаточно сделок" in text
    assert "не обгоняет baseline" in text
    assert "последний кандидат отклонён по метрикам" in text
    assert "Следующая попытка" in text
