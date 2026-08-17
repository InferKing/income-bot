from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from html import escape

from income_tg.common.money import format_decimal
from income_tg.portfolio.schemas import PortfolioBalance


@dataclass(frozen=True, slots=True)
class SystemHealthItem:
    service: str
    instance_id: str
    status: str
    code: str
    heartbeat_at: datetime


@dataclass(frozen=True, slots=True)
class SystemModelInfo:
    version: str
    activated_at: datetime | None


def render_portfolios(portfolios: list[PortfolioBalance], manual_usdt_rub_rate: Decimal) -> str:
    parts = ["💼 <b>Портфели</b>", "<i>Ручной и paper-trading балансы хранятся отдельно.</i>"]
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


def render_system_status(
    health: Sequence[SystemHealthItem],
    *,
    model: SystemModelInfo | None,
    feature_vectors: int,
    training_vectors: int,
    labeled_training_vectors: int,
    signals: int,
    latest_feature_at: datetime | None,
    environment: str,
    paper_only: bool,
    market_sources: str,
    symbols: str,
    now: datetime,
) -> str:
    now = _as_utc(now)
    grouped = _group_health(health)
    unhealthy_services = {
        item.service
        for item in health
        if item.status not in {"HEALTHY", "DEGRADED"} or _age_seconds(item.heartbeat_at, now) > 30
    }
    if not health:
        headline = "⚪ <b>Состояние пока неизвестно</b>"
    elif unhealthy_services <= {"MODEL"} and model is None:
        headline = "🟡 <b>Сервисы работают, модель обучается</b>"
    elif unhealthy_services:
        headline = "🔴 <b>Часть системы требует внимания</b>"
    else:
        headline = "🟢 <b>Система работает штатно</b>"

    lines = ["🖥️ <b>Система</b>", headline, "", "<b>Сервисы</b>"]
    if not health:
        lines.append("⚪ Heartbeat ещё не поступал.")
    for service in ("DATABASE", "MARKET", "BOT", "MODEL"):
        items = grouped.get(service, [])
        if items:
            lines.append(_render_service(service, items, now))

    lines.extend(("", "<b>Модель и данные</b>"))
    if model is None:
        lines.append("⏳ Champion-модель ещё не назначена — обучение продолжится автоматически.")
    else:
        activated = _format_timestamp(model.activated_at) if model.activated_at else "не указано"
        lines.append(f"🧠 Champion: <code>{escape(model.version)}</code> · {activated}")
    latest = _format_timestamp(latest_feature_at) if latest_feature_at else "пока нет"
    champion_target = 500
    progress_percent = min(100, labeled_training_vectors * 100 // champion_target)
    progress = _progress_bar(progress_percent)
    lines.extend(
        (
            f"🧩 Векторов признаков: <b>{feature_vectors}</b> · последний: {latest}",
            (
                "🎯 BTC · 15m: "
                f"<b>{labeled_training_vectors}</b> размечено из {training_vectors} · "
                f"<code>{progress}</code> {progress_percent}%"
            ),
            _training_progress_hint(labeled_training_vectors, champion_target),
            f"📈 Сигналов сформировано: <b>{signals}</b>",
            "",
            "<b>Конфигурация</b>",
            f"🧪 Режим: <b>{'только paper trading' if paper_only else 'торговый'}</b>",
            f"🌐 Среда: <code>{escape(environment)}</code>",
            f"📡 Источники: <code>{escape(market_sources)}</code>",
            f"💱 Инструменты: <code>{escape(symbols)}</code>",
            "",
            f"<i>Проверено: {_format_timestamp(now)}</i>",
        )
    )
    return "\n".join(lines)


def _progress_bar(percent: int) -> str:
    filled = min(10, max(0, percent) // 10)
    return "█" * filled + "░" * (10 - filled)


def _training_progress_hint(labeled: int, champion_target: int) -> str:
    if labeled < 40:
        return f"<i>До первого обучения: ещё {40 - labeled}. Ориентир для champion — 500+.</i>"
    if labeled < champion_target:
        return (
            "<i>Обучение уже доступно; до минимальной полноценной проверки "
            f"ещё около {champion_target - labeled}.</i>"
        )
    return "<i>Данных достаточно для полноценной проверки; кандидат проходит критерии качества.</i>"


def _group_health(health: Sequence[SystemHealthItem]) -> dict[str, list[SystemHealthItem]]:
    grouped: dict[str, list[SystemHealthItem]] = {}
    for item in sorted(health, key=lambda value: (value.service, value.instance_id)):
        grouped.setdefault(item.service, []).append(item)
    return grouped


def _render_service(service: str, items: list[SystemHealthItem], now: datetime) -> str:
    labels = {
        "DATABASE": "База данных",
        "MARKET": "Рыночные данные",
        "BOT": "Telegram-бот",
        "MODEL": "ML-модель",
    }
    stale = any(_age_seconds(item.heartbeat_at, now) > 30 for item in items)
    statuses = {item.status for item in items}
    if stale or statuses - {"HEALTHY", "DEGRADED"}:
        icon = "🔴"
    elif "DEGRADED" in statuses:
        icon = "🟡"
    else:
        icon = "🟢"
    details = []
    for item in items:
        age = _human_age(_age_seconds(item.heartbeat_at, now))
        code = _code_label(item.code)
        instance = f"{escape(item.instance_id)} · " if len(items) > 1 else ""
        details.append(f"{instance}{code}, {age}")
    return f"{icon} <b>{labels.get(service, escape(service))}</b> — " + "; ".join(details)


def _code_label(code: str) -> str:
    return {
        "RUNNING": "работает",
        "DATABASE_OK": "подключение доступно",
        "MODEL_OK": "артефакт проверен",
        "MODEL_POINTER_MISSING": "ожидает первую модель",
        "MODEL_NOT_CHAMPION": "champion не назначен",
        "HEARTBEAT_STALE": "heartbeat устарел",
        "HEARTBEAT_MISSING": "heartbeat отсутствует",
    }.get(code, escape(code.lower().replace("_", " ")))


def _age_seconds(value: datetime, now: datetime) -> int:
    return max(0, round((now - _as_utc(value)).total_seconds()))


def _human_age(seconds: int) -> str:
    if seconds < 5:
        return "только что"
    if seconds < 60:
        return f"{seconds} сек. назад"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин. назад"
    return f"{minutes // 60} ч. назад"


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "пока нет"
    return _as_utc(value).strftime("%d.%m.%Y %H:%M UTC")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
