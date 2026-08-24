from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from uuid import UUID

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.bot.keyboards import (
    BUY_BUTTON,
    DEPOSIT_BUTTON,
    HELP_BUTTON,
    PORTFOLIOS_BUTTON,
    RECONCILE_BUTTON,
    RISK_BUTTON,
    SELL_BUTTON,
    SIGNALS_BUTTON,
    STATISTICS_BUTTON,
    SYSTEM_BUTTON,
    WITHDRAW_BUTTON,
    candidate_details_keyboard,
    command_example_keyboard,
    help_keyboard,
    main_keyboard,
    operations_help_keyboard,
    system_help_keyboard,
    system_keyboard,
)
from income_tg.bot.presenters import (
    CandidateDetailInfo,
    CandidateTradeInfo,
    SystemHealthItem,
    SystemModelInfo,
    SystemTrainingInfo,
    render_candidate_details,
    render_portfolios,
    render_system_status,
)
from income_tg.common.enums import PortfolioKind, TradeSide
from income_tg.common.money import (
    MoneyValidationError,
    parse_nonnegative_decimal,
    parse_positive_decimal,
)
from income_tg.config import Settings
from income_tg.models.evaluation import AdmissionCriteria
from income_tg.portfolio.errors import PortfolioError
from income_tg.portfolio.service import PortfolioService
from income_tg.risk.settings import RiskSettingsService
from income_tg.storage.models import Portfolio
from income_tg.storage.trading_models import (
    EquityPointRecord,
    FeatureVectorRecord,
    FxRateRecord,
    InstrumentRecord,
    MarketCandleRecord,
    ModelVersionRecord,
    ServiceHealthRecord,
    SignalRecord,
    TrainingRunRecord,
)

router = Router(name="owner")

HELP_TEXT = """❓ <b>Помощь</b>

Выберите раздел ниже — бот сразу откроет нужный экран.

💼 <b>Портфели</b> — реальные и виртуальные остатки
📈 <b>Сигналы</b> — последние торговые идеи
📊 <b>Статистика</b> — результаты paper trading
🛡️ <b>Риск</b> — действующие ограничения
➕ <b>Операции</b> — пополнение, вывод, покупка и продажа
🔄 <b>Сверка</b> — замена остатков полным снимком
🖥️ <b>Система</b> — сервисы, модель и свежесть данных

<i>Команды с параметрами можно скопировать одной кнопкой.</i>"""

OPERATIONS_HELP_TEXT = """➕ <b>Операции с реальным портфелем</b>

Выберите действие. Бот покажет готовый пример команды и кнопку копирования.

⚠️ Эти команды меняют только ручной Crypto Wallet.
Paper-портфель управляется торговым движком отдельно."""

SYSTEM_HELP_TEXT = """🖥️ <b>Как читать состояние системы</b>

🟢 — компонент работает, heartbeat свежий
🟡 — работа продолжается, но компонент ещё не полностью готов
🔴 — heartbeat устарел или обнаружена ошибка

<b>Champion-модель</b> — модель, допущенная к созданию сигналов.
После чистого запуска она появится не сразу: системе нужно накопить размеченные данные
и пройти проверку качества.

<b>Векторы признаков</b> показывают, что рыночные данные поступают и обрабатываются."""

OPERATION_HELP: dict[str, tuple[str, str, str]] = {
    "deposit": (
        "➕ <b>Пополнение</b>",
        "/deposit USDT 100",
        "Добавляет указанную сумму к реальному портфелю.",
    ),
    "withdraw": (
        "➖ <b>Вывод</b>",
        "/withdraw USDT 25",
        "Вычитает указанную сумму из реального портфеля.",
    ),
    "buy": (
        "🟢 <b>Покупка</b>",
        "/buy BTC USDT 0.001 60000 0.06",
        "Формат: актив, валюта расчёта, количество, цена и необязательная комиссия.",
    ),
    "sell": (
        "🔴 <b>Продажа</b>",
        "/sell BTC USDT 0.001 65000 0.065",
        "Формат: актив, валюта расчёта, количество, цена и необязательная комиссия.",
    ),
    "reconcile": (
        "🔄 <b>Сверка остатков</b>",
        "/reconcile BTC=0.01 USDT=500 RUB=1000",
        "Полностью заменяет снимок остатков. Неуказанные существующие активы будут обнулены.",
    ),
}


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "👋 <b>Income Bot готов к работе</b>\n\n"
        "Здесь можно вести ручной Crypto Wallet, следить за рыночными сигналами и "
        "наблюдать за paper-trading стратегией.\n\n"
        "💼 Реальный портфель изменяется только вашими командами.\n"
        "🧪 Виртуальный портфель полностью изолирован и управляется ботом.\n\n"
        "Выберите действие на клавиатуре ниже.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("id"))
async def telegram_id(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Не удалось определить Telegram ID.")
        return
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("help"))
@router.message(F.text.in_({"Помощь", HELP_BUTTON}))
async def help_message(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=help_keyboard())


@router.message(F.text == "Добавить операцию")
async def operations_help(message: Message) -> None:
    await message.answer(OPERATIONS_HELP_TEXT, reply_markup=operations_help_keyboard())


@router.message(F.text == DEPOSIT_BUTTON)
async def deposit_help(message: Message) -> None:
    await _send_operation_help(message, "deposit")


@router.message(F.text == WITHDRAW_BUTTON)
async def withdraw_help(message: Message) -> None:
    await _send_operation_help(message, "withdraw")


@router.message(F.text == BUY_BUTTON)
async def buy_help(message: Message) -> None:
    await _send_operation_help(message, "buy")


@router.message(F.text == SELL_BUTTON)
async def sell_help(message: Message) -> None:
    await _send_operation_help(message, "sell")


@router.message(F.text.in_({"Сверить остатки", RECONCILE_BUTTON}))
async def reconcile_help(message: Message) -> None:
    await _send_operation_help(message, "reconcile")


@router.message(Command("portfolio"))
@router.message(F.text.in_({"Портфели", PORTFOLIOS_BUTTON}))
async def portfolio_summary(
    message: Message,
    session: AsyncSession,
    settings: Settings,
) -> None:
    if message.from_user is None:
        raise PortfolioError("Не удалось определить пользователя")
    await message.answer(
        await _portfolio_text(message.from_user.id, session, settings),
        reply_markup=main_keyboard(),
    )


@router.message(Command("signals"))
@router.message(F.text.in_({"Сигналы", SIGNALS_BUTTON}))
async def signals_history(message: Message, session: AsyncSession) -> None:
    records = list(
        await session.scalars(
            select(SignalRecord).order_by(SignalRecord.created_at.desc()).limit(10)
        )
    )
    if not records:
        await message.answer(
            "📈 <b>Сигналы</b>\n\n"
            "⏳ Сигналов пока нет. Это нормально после первого запуска: бот накапливает "
            "данные и готовит champion-модель."
        )
        return
    lines = ["📈 <b>Последние сигналы</b>", "<i>До 10 последних решений модели.</i>", ""]
    for record in records:
        action_icon = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}.get(record.action, "🔹")
        status_label = {
            "APPROVED": "одобрен",
            "REJECTED": "отклонён",
            "PENDING": "ожидает",
        }.get(record.status, record.status.lower())
        lines.append(
            f"{action_icon} <b>{record.action}</b> · {status_label}\n"
            f"   уверенность {record.confidence:.0%} · цена <code>{record.reference_price}</code>"
        )
    await message.answer("\n".join(lines))


@router.message(Command("stats"))
@router.message(F.text.in_({"Статистика", STATISTICS_BUTTON}))
async def statistics(message: Message, session: AsyncSession) -> None:
    signals_total = await session.scalar(select(func.count()).select_from(SignalRecord)) or 0
    approved = (
        await session.scalar(
            select(func.count()).select_from(SignalRecord).where(SignalRecord.status == "APPROVED")
        )
        or 0
    )
    equity = await session.scalar(
        select(EquityPointRecord).order_by(EquityPointRecord.observed_at.desc()).limit(1)
    )
    lines = [
        "📊 <b>Статистика paper trading</b>",
        "",
        f"📈 Сигналов: <b>{signals_total}</b>",
        f"✅ Одобрено риск-модулем: <b>{approved}</b>",
    ]
    if equity is not None:
        lines.extend(
            (
                "",
                f"💰 Equity: <code>{equity.equity_usdt}</code> USDT",
                f"💵 Оценка: <code>{equity.equity_rub}</code> RUB",
                f"📉 Текущая просадка: <b>{equity.drawdown_fraction:.2%}</b>",
            )
        )
    else:
        lines.extend(("", "⏳ Кривая капитала появится после первых paper-сделок."))
    await message.answer("\n".join(lines))


@router.message(Command("risk"))
@router.message(F.text.in_({"Риск", RISK_BUTTON}))
async def risk_settings(message: Message, session: AsyncSession) -> None:
    if message.from_user is None:
        raise PortfolioError("Не удалось определить пользователя")
    await message.answer(await _risk_text(message.from_user.id, session))


async def _risk_text(telegram_user_id: int, session: AsyncSession) -> str:
    _, user_id = await _service_and_telegram_user(telegram_user_id, session)
    profile = await RiskSettingsService(session).get(user_id)
    return (
        "🛡️ <b>Контроль риска</b>\n"
        "<i>Эти ограничения проверяются перед каждой paper-сделкой.</i>\n\n"
        f"💼 Маржа одной позиции: <b>{profile.max_margin_fraction:.1%}</b>\n"
        f"🛑 Риск по стопу: <b>{profile.max_stop_risk_fraction:.1%}</b>\n"
        f"📅 Лимит потерь за день: <b>{profile.max_daily_loss_fraction:.1%}</b>\n"
        f"📉 Максимальная просадка: <b>{profile.max_drawdown_fraction:.1%}</b>\n"
        f"📚 Открытых позиций: <b>{profile.max_open_positions}</b>\n"
        f"⚙️ Максимальное плечо: <b>{profile.max_leverage}x</b>\n"
        f"🎯 Порог уверенности: <b>{profile.min_signal_confidence:.0%}</b>\n\n"
        "Пример изменения: <code>/setrisk max_leverage 10</code>"
    )


@router.message(Command("setrisk"))
async def set_risk(message: Message, session: AsyncSession) -> None:
    try:
        arguments = _command_arguments(message)
        if len(arguments) != 2:
            raise ValueError("Формат: /setrisk SETTING VALUE")
        _, user_id = await _service_and_user(message, session)
        profile = await RiskSettingsService(session).update(user_id, arguments[0], arguments[1])
        await session.commit()
        await message.answer(
            f"Настройка сохранена. Максимальное плечо сейчас: {profile.max_leverage}x"
        )
    except (ValueError, PortfolioError) as error:
        await message.answer(f"Настройка не изменена: {error}")


@router.message(Command("status"))
@router.message(F.text.in_({"Система", SYSTEM_BUTTON}))
async def system_status(
    message: Message,
    session: AsyncSession,
    settings: Settings,
) -> None:
    text = await _system_status_text(session, settings)
    await message.answer(text, reply_markup=system_keyboard())


@router.callback_query(F.data == "system:refresh")
async def refresh_system_status(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    message = _callback_message(callback)
    if message is None:
        await callback.answer("Сообщение больше недоступно", show_alert=True)
        return
    text = await _system_status_text(session, settings)
    try:
        await message.edit_text(text, reply_markup=system_keyboard())
        await callback.answer("Состояние обновлено")
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Данные уже актуальны")


@router.callback_query(F.data == "system:candidate")
async def show_candidate_details(callback: CallbackQuery, session: AsyncSession) -> None:
    message = _callback_message(callback)
    if message is None:
        await callback.answer("Сообщение больше недоступно", show_alert=True)
        return
    latest_training = await session.scalar(
        select(TrainingRunRecord).order_by(TrainingRunRecord.started_at.desc()).limit(1)
    )
    if latest_training is None:
        await callback.answer("Попыток обучения пока не было", show_alert=True)
        return
    training_attempts = int(
        await session.scalar(select(func.count()).select_from(TrainingRunRecord)) or 0
    )
    training = _system_training_info(latest_training, training_attempts)
    if training is None:
        await callback.answer("Данные кандидата недоступны", show_alert=True)
        return
    text = render_candidate_details(training)
    try:
        await message.edit_text(text, reply_markup=candidate_details_keyboard())
        await callback.answer()
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Данные уже актуальны")


@router.callback_query(F.data == "help:main")
async def show_help_callback(callback: CallbackQuery) -> None:
    await _edit_callback(callback, HELP_TEXT, help_keyboard())


@router.callback_query(F.data == "help:operations")
async def show_operations_callback(callback: CallbackQuery) -> None:
    await _edit_callback(callback, OPERATIONS_HELP_TEXT, operations_help_keyboard())


@router.callback_query(F.data == "help:system")
async def show_system_help_callback(callback: CallbackQuery) -> None:
    await _edit_callback(callback, SYSTEM_HELP_TEXT, system_help_keyboard())


@router.callback_query(F.data.startswith("help:"))
async def show_operation_callback(callback: CallbackQuery) -> None:
    operation = (callback.data or "").partition(":")[2]
    details = OPERATION_HELP.get(operation)
    if details is None:
        await callback.answer("Раздел не найден", show_alert=True)
        return
    title, command, description = details
    text = f"{title}\n\n{description}\n\n<code>{command}</code>"
    await _edit_callback(callback, text, command_example_keyboard(command))


@router.callback_query(F.data == "menu:portfolio")
async def open_portfolio_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    message = _callback_message(callback)
    await callback.answer()
    if message is not None:
        await message.answer(
            await _portfolio_text(callback.from_user.id, session, settings),
            reply_markup=main_keyboard(),
        )


@router.callback_query(F.data == "menu:signals")
async def open_signals_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    message = _callback_message(callback)
    await callback.answer()
    if message is not None:
        await signals_history(message, session)


@router.callback_query(F.data == "menu:stats")
async def open_statistics_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    message = _callback_message(callback)
    await callback.answer()
    if message is not None:
        await statistics(message, session)


@router.callback_query(F.data == "menu:risk")
async def open_risk_callback(callback: CallbackQuery, session: AsyncSession) -> None:
    message = _callback_message(callback)
    await callback.answer()
    if message is not None:
        await message.answer(await _risk_text(callback.from_user.id, session))


@router.callback_query(F.data == "menu:status")
async def open_system_callback(
    callback: CallbackQuery,
    session: AsyncSession,
    settings: Settings,
) -> None:
    message = _callback_message(callback)
    await callback.answer()
    if message is not None:
        await system_status(message, session, settings)


async def _system_status_text(session: AsyncSession, settings: Settings) -> str:
    health = list(await session.scalars(select(ServiceHealthRecord)))
    champion = await session.scalar(
        select(ModelVersionRecord)
        .where(ModelVersionRecord.stage == "CHAMPION")
        .order_by(ModelVersionRecord.activated_at.desc())
        .limit(1)
    )
    latest_training = await session.scalar(
        select(TrainingRunRecord).order_by(TrainingRunRecord.started_at.desc()).limit(1)
    )
    training_attempts = int(
        await session.scalar(select(func.count()).select_from(TrainingRunRecord)) or 0
    )
    feature_vectors = (
        await session.scalar(select(func.count()).select_from(FeatureVectorRecord)) or 0
    )
    latest_feature_at = await session.scalar(select(func.max(FeatureVectorRecord.as_of)))
    training_vectors = 0
    labeled_training_vectors = 0
    training_instrument = await session.scalar(
        select(InstrumentRecord).where(
            InstrumentRecord.canonical_symbol == "BTC/USDT",
            InstrumentRecord.market_type == "linear_perpetual",
        )
    )
    if training_instrument is not None:
        training_vectors = int(
            await session.scalar(
                select(func.count())
                .select_from(FeatureVectorRecord)
                .where(
                    FeatureVectorRecord.instrument_id == training_instrument.id,
                    FeatureVectorRecord.horizon == "15m",
                )
            )
            or 0
        )
        latest_primary_candle = await session.scalar(
            select(func.max(MarketCandleRecord.opened_at)).where(
                MarketCandleRecord.instrument_id == training_instrument.id,
                MarketCandleRecord.provider == "bybit",
                MarketCandleRecord.interval_seconds == 60,
                MarketCandleRecord.is_closed.is_(True),
            )
        )
        if latest_primary_candle is not None:
            label_cutoff = latest_primary_candle + timedelta(minutes=1) - timedelta(minutes=15)
            labeled_training_vectors = int(
                await session.scalar(
                    select(func.count())
                    .select_from(FeatureVectorRecord)
                    .where(
                        FeatureVectorRecord.instrument_id == training_instrument.id,
                        FeatureVectorRecord.horizon == "15m",
                        FeatureVectorRecord.as_of <= label_cutoff,
                    )
                )
                or 0
            )
    signals = await session.scalar(select(func.count()).select_from(SignalRecord)) or 0
    health_items = [
        SystemHealthItem(
            service=item.service,
            instance_id=item.instance_id,
            status=item.status,
            code=_health_code(item.details),
            heartbeat_at=item.last_heartbeat_at,
        )
        for item in health
    ]
    model = (
        SystemModelInfo(version=champion.version, activated_at=champion.activated_at)
        if champion is not None
        else None
    )
    training = _system_training_info(latest_training, training_attempts)
    return render_system_status(
        health_items,
        model=model,
        training=training,
        feature_vectors=feature_vectors,
        training_vectors=training_vectors,
        labeled_training_vectors=labeled_training_vectors,
        signals=signals,
        latest_feature_at=latest_feature_at,
        environment=settings.environment,
        paper_only=settings.paper_only,
        market_sources=settings.market_sources,
        symbols=settings.symbols,
        now=datetime.now(UTC),
    )


def _system_training_info(
    run: TrainingRunRecord | None,
    attempt_count: int,
) -> SystemTrainingInfo | None:
    if run is None:
        return None
    metrics = run.metrics or {}
    parameters = run.parameters or {}
    reasons = metrics.get("admission_reasons", [])
    finished_at = run.finished_at
    criteria = AdmissionCriteria()
    required_trade_fraction = _decimal_metric(parameters, "min_closed_trade_fraction") or Decimal(
        str(criteria.min_closed_trade_fraction)
    )
    test_samples = _integer_metric(metrics, "test_samples")
    required_closed_trades = (
        int(
            (Decimal(test_samples) * required_trade_fraction).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        if test_samples is not None and test_samples > 0
        else None
    )
    details = _candidate_detail_info(metrics, parameters)
    return SystemTrainingInfo(
        attempt_count=attempt_count,
        status=run.status,
        started_at=run.started_at,
        finished_at=finished_at,
        next_attempt_at=(finished_at + timedelta(minutes=15) if finished_at is not None else None),
        net_return=_decimal_metric(metrics, "net_return"),
        max_drawdown=_decimal_metric(metrics, "max_drawdown"),
        profit_factor=_decimal_metric(metrics, "profit_factor"),
        closed_trades=_integer_metric(metrics, "closed_trades"),
        test_samples=test_samples,
        required_closed_trades=required_closed_trades,
        required_trade_fraction=required_trade_fraction,
        admission_reasons=(
            tuple(str(reason) for reason in reasons) if isinstance(reasons, list) else ()
        ),
        details=details,
    )


def _candidate_detail_info(
    metrics: dict[str, object],
    parameters: dict[str, object],
) -> CandidateDetailInfo | None:
    long_trades = _integer_metric(metrics, "long_trades")
    if long_trades is None:
        return None
    criteria = AdmissionCriteria()
    return CandidateDetailInfo(
        candidate_version=_string_metric(metrics, "candidate_version"),
        test_from=_datetime_metric(metrics, "test_from"),
        test_to=_datetime_metric(metrics, "test_to"),
        confidence_threshold=(
            _decimal_metric(metrics, "confidence_threshold")
            or _decimal_metric(parameters, "confidence_threshold")
            or Decimal("0.70")
        ),
        long_trades=long_trades,
        short_trades=_integer_metric(metrics, "short_trades") or 0,
        skipped_points=_integer_metric(metrics, "skipped_points") or 0,
        winning_trades=_integer_metric(metrics, "winning_trades") or 0,
        losing_trades=_integer_metric(metrics, "losing_trades") or 0,
        breakeven_trades=_integer_metric(metrics, "breakeven_trades") or 0,
        win_rate=_decimal_metric(metrics, "win_rate") or Decimal(0),
        gross_profit=_decimal_metric(metrics, "gross_profit") or Decimal(0),
        gross_loss=_decimal_metric(metrics, "gross_loss") or Decimal(0),
        total_costs=_decimal_metric(metrics, "total_costs") or Decimal(0),
        average_trade_return=(_decimal_metric(metrics, "average_trade_return") or Decimal(0)),
        best_trade_return=_decimal_metric(metrics, "best_trade_return") or Decimal(0),
        worst_trade_return=_decimal_metric(metrics, "worst_trade_return") or Decimal(0),
        average_confidence=_decimal_metric(metrics, "average_confidence") or Decimal(0),
        recent_return=_decimal_metric(metrics, "recent_return") or Decimal(0),
        baseline_return=_decimal_metric(metrics, "baseline_return") or Decimal(0),
        champion_return=_decimal_metric(metrics, "champion_return"),
        max_allowed_drawdown=(
            _decimal_metric(parameters, "max_drawdown") or Decimal(str(criteria.max_drawdown))
        ),
        min_profit_factor=(
            _decimal_metric(parameters, "min_profit_factor")
            or Decimal(str(criteria.min_profit_factor))
        ),
        recent_trades=_candidate_trades(metrics.get("recent_trades")),
    )


def _candidate_trades(value: object) -> tuple[CandidateTradeInfo, ...]:
    if not isinstance(value, list):
        return ()
    result: list[CandidateTradeInfo] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        occurred_at = _datetime_metric(item, "occurred_at")
        direction = _string_metric(item, "direction")
        confidence = _decimal_metric(item, "confidence")
        net_return = _decimal_metric(item, "net_return")
        if (
            occurred_at is None
            or direction not in {"LONG", "SHORT"}
            or confidence is None
            or net_return is None
        ):
            continue
        result.append(CandidateTradeInfo(occurred_at, direction, confidence, net_return))
    return tuple(result[-5:])


def _decimal_metric(metrics: dict[str, object], name: str) -> Decimal | None:
    value = metrics.get(name)
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except ArithmeticError:
        return None
    return result if result.is_finite() else None


def _integer_metric(metrics: dict[str, object], name: str) -> int | None:
    value = metrics.get(name)
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_metric(metrics: dict[str, object], name: str) -> str | None:
    value = metrics.get(name)
    return value if isinstance(value, str) and value else None


def _datetime_metric(metrics: dict[str, object], name: str) -> datetime | None:
    value = _string_metric(metrics, name)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


@router.message(Command("deposit"))
async def deposit(message: Message, session: AsyncSession) -> None:
    await _handle_simple_cash_operation(message, session, withdrawal=False)


@router.message(Command("withdraw"))
async def withdraw(message: Message, session: AsyncSession) -> None:
    await _handle_simple_cash_operation(message, session, withdrawal=True)


@router.message(Command("buy"))
async def buy(message: Message, session: AsyncSession) -> None:
    await _handle_trade(message, session, TradeSide.BUY)


@router.message(Command("sell"))
async def sell(message: Message, session: AsyncSession) -> None:
    await _handle_trade(message, session, TradeSide.SELL)


@router.message(Command("reconcile"))
async def reconcile(message: Message, session: AsyncSession) -> None:
    try:
        arguments = _command_arguments(message)
        if not arguments:
            raise ValueError("Укажите хотя бы один остаток в формате ASSET=AMOUNT")
        target: dict[str, Decimal] = {}
        for item in arguments:
            asset, separator, raw_amount = item.partition("=")
            if not separator:
                raise ValueError(f"Некорректная часть снимка: {item}")
            amount = parse_nonnegative_decimal(raw_amount, f"остаток {asset}")
            target[asset] = amount
        service, portfolio = await _real_portfolio(message, session)
        event = await service.reconcile(
            portfolio.id,
            target,
            idempotency_key=_message_idempotency_key(message),
        )
        await session.commit()
        await message.answer(
            f"Сверка сохранена. ID события: <code>{event.id}</code>",
            reply_markup=main_keyboard(),
        )
    except (ValueError, PortfolioError) as error:
        await message.answer(f"Не удалось выполнить сверку: {error}")


async def _handle_simple_cash_operation(
    message: Message,
    session: AsyncSession,
    *,
    withdrawal: bool,
) -> None:
    try:
        arguments = _command_arguments(message)
        if len(arguments) != 2:
            command = "withdraw" if withdrawal else "deposit"
            raise ValueError(f"Формат: /{command} ASSET AMOUNT")
        asset, raw_amount = arguments
        amount = parse_positive_decimal(raw_amount)
        service, portfolio = await _real_portfolio(message, session)
        if withdrawal:
            event = await service.record_withdrawal(
                portfolio.id,
                asset,
                amount,
                idempotency_key=_message_idempotency_key(message),
            )
        else:
            event = await service.record_deposit(
                portfolio.id,
                asset,
                amount,
                idempotency_key=_message_idempotency_key(message),
            )
        await session.commit()
        action = "Вывод" if withdrawal else "Пополнение"
        await message.answer(f"{action} сохранен. ID события: <code>{event.id}</code>")
    except (ValueError, MoneyValidationError, PortfolioError) as error:
        await message.answer(f"Операция не сохранена: {error}")


async def _handle_trade(
    message: Message,
    session: AsyncSession,
    side: TradeSide,
) -> None:
    try:
        arguments = _command_arguments(message)
        if len(arguments) not in (4, 5):
            command = "buy" if side is TradeSide.BUY else "sell"
            raise ValueError(f"Формат: /{command} BASE QUOTE QUANTITY PRICE [FEE]")
        base, quote, raw_quantity, raw_price, *raw_fee = arguments
        quantity = parse_positive_decimal(raw_quantity, "quantity")
        price = parse_positive_decimal(raw_price, "price")
        fee = Decimal(raw_fee[0].replace(",", ".")) if raw_fee else Decimal("0")
        service, portfolio = await _real_portfolio(message, session)
        event = await service.record_trade(
            portfolio.id,
            side,
            base,
            quote,
            quantity,
            price,
            idempotency_key=_message_idempotency_key(message),
            fee_amount=fee,
            fee_asset=quote,
        )
        await session.commit()
        await message.answer(f"Сделка сохранена. ID события: <code>{event.id}</code>")
    except (ValueError, MoneyValidationError, PortfolioError) as error:
        await message.answer(f"Сделка не сохранена: {error}")


async def _service_and_user(
    message: Message, session: AsyncSession
) -> tuple[PortfolioService, UUID]:
    if message.from_user is None:
        raise PortfolioError("Не удалось определить пользователя")
    return await _service_and_telegram_user(message.from_user.id, session)


async def _service_and_telegram_user(
    telegram_user_id: int,
    session: AsyncSession,
) -> tuple[PortfolioService, UUID]:
    service = PortfolioService(session)
    user = await service.get_owner(telegram_user_id)
    if user is None:
        raise PortfolioError("Владелец не инициализирован")
    return service, user.id


async def _portfolio_text(
    telegram_user_id: int,
    session: AsyncSession,
    settings: Settings,
) -> str:
    service, user_id = await _service_and_telegram_user(telegram_user_id, session)
    portfolios = await service.list_balances(user_id)
    market_rate = await session.scalar(
        select(FxRateRecord.rate)
        .where(FxRateRecord.base == "USDT", FxRateRecord.quote == "RUB")
        .order_by(FxRateRecord.observed_at.desc())
        .limit(1)
    )
    rate = market_rate if market_rate is not None else settings.manual_usdt_rub_rate
    return render_portfolios(portfolios, rate)


async def _real_portfolio(
    message: Message, session: AsyncSession
) -> tuple[PortfolioService, Portfolio]:
    service, user_id = await _service_and_user(message, session)
    portfolio = await service.get_portfolio(user_id, PortfolioKind.REAL_MANUAL)
    return service, portfolio


def _command_arguments(message: Message) -> list[str]:
    text = message.text or ""
    return text.split()[1:]


def _message_idempotency_key(message: Message) -> str:
    chat_id = message.chat.id
    return f"telegram:{chat_id}:{message.message_id}"


async def _send_operation_help(message: Message, operation: str) -> None:
    title, command, description = OPERATION_HELP[operation]
    await message.answer(
        f"{title}\n\n{description}\n\n<code>{command}</code>",
        reply_markup=command_example_keyboard(command),
    )


async def _edit_callback(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    message = _callback_message(callback)
    if message is None:
        await callback.answer("Сообщение больше недоступно", show_alert=True)
        return
    try:
        await message.edit_text(text, reply_markup=reply_markup)
        await callback.answer()
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Этот раздел уже открыт")


def _callback_message(callback: CallbackQuery) -> Message | None:
    return callback.message if isinstance(callback.message, Message) else None


def _health_code(details: dict[str, object]) -> str:
    code = details.get("code")
    return code if isinstance(code, str) else "UNKNOWN"
