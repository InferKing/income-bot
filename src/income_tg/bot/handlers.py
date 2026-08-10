from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from income_tg.bot.keyboards import main_keyboard
from income_tg.bot.presenters import render_portfolios
from income_tg.common.enums import PortfolioKind, TradeSide
from income_tg.common.money import (
    MoneyValidationError,
    parse_nonnegative_decimal,
    parse_positive_decimal,
)
from income_tg.config import Settings
from income_tg.portfolio.errors import PortfolioError
from income_tg.portfolio.service import PortfolioService
from income_tg.risk.settings import RiskSettingsService
from income_tg.storage.models import Portfolio
from income_tg.storage.trading_models import (
    EquityPointRecord,
    ModelVersionRecord,
    ServiceHealthRecord,
    SignalRecord,
)

router = Router(name="owner")

HELP_TEXT = """<b>Доступные команды</b>

<code>/portfolio</code> — показать оба портфеля
<code>/deposit USDT 100</code> — внести пополнение реального портфеля
<code>/withdraw USDT 25</code> — внести вывод
<code>/buy BTC USDT 0.001 60000 0.06</code> — покупка; последняя величина — комиссия
<code>/sell BTC USDT 0.001 65000 0.065</code> — продажа
<code>/reconcile BTC=0.01 USDT=500 RUB=1000</code> — заменить текущие остатки снимком
<code>/signals</code> — последние сигналы
<code>/risk</code> — текущие лимиты риска
<code>/setrisk max_leverage 10</code> — изменить лимит
<code>/status</code> — состояние сервисов
<code>/id</code> — показать ваш Telegram ID

В <code>/reconcile</code> неуказанные ранее существовавшие активы будут обнулены.
Все операции относятся к реальному Crypto Wallet.
Виртуальный портфель изменяет только paper-trading движок."""


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Бот запущен. Реальный Crypto Wallet ведется вручную, виртуальный портфель изолирован.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("id"))
async def telegram_id(message: Message) -> None:
    if message.from_user is None:
        await message.answer("Не удалось определить Telegram ID.")
        return
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("help"))
@router.message(F.text == "Помощь")
@router.message(F.text == "Добавить операцию")
async def help_message(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_keyboard())


@router.message(F.text == "Сверить остатки")
async def reconcile_help(message: Message) -> None:
    await message.answer(
        "Отправьте полный снимок остатков, например:\n"
        "<code>/reconcile BTC=0.01 USDT=500 RUB=1000</code>\n\n"
        "Активы, которые были в портфеле, но отсутствуют в снимке, будут обнулены.",
        reply_markup=main_keyboard(),
    )


@router.message(Command("portfolio"))
@router.message(F.text == "Портфели")
async def portfolio_summary(
    message: Message,
    session: AsyncSession,
    settings: Settings,
) -> None:
    service, user_id = await _service_and_user(message, session)
    portfolios = await service.list_balances(user_id)
    await message.answer(
        render_portfolios(portfolios, settings.manual_usdt_rub_rate),
        reply_markup=main_keyboard(),
    )


@router.message(Command("signals"))
@router.message(F.text == "Сигналы")
async def signals_history(message: Message, session: AsyncSession) -> None:
    records = list(
        await session.scalars(
            select(SignalRecord).order_by(SignalRecord.created_at.desc()).limit(10)
        )
    )
    if not records:
        await message.answer("Сигналы еще не формировались.")
        return
    lines = ["<b>Последние сигналы</b>"]
    for record in records:
        lines.append(
            f"• {record.action} · {record.status} · {record.confidence:.0%} · "
            f"<code>{record.reference_price}</code>"
        )
    await message.answer("\n".join(lines))


@router.message(Command("stats"))
@router.message(F.text == "Статистика")
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
        "<b>Статистика</b>",
        f"Сигналов: {signals_total}",
        f"Одобрено риск-модулем: {approved}",
    ]
    if equity is not None:
        lines.extend(
            (
                f"Paper equity: <code>{equity.equity_usdt}</code> USDT",
                f"≈ <code>{equity.equity_rub}</code> RUB",
                f"Просадка: {equity.drawdown_fraction:.2%}",
            )
        )
    else:
        lines.append("Кривая капитала появится после запуска paper trading.")
    await message.answer("\n".join(lines))


@router.message(Command("risk"))
@router.message(F.text == "Риск")
async def risk_settings(message: Message, session: AsyncSession) -> None:
    _, user_id = await _service_and_user(message, session)
    profile = await RiskSettingsService(session).get(user_id)
    await message.answer(
        "<b>Настройки риска</b>\n"
        f"Маржа позиции: {profile.max_margin_fraction:.1%}\n"
        f"Риск по стопу: {profile.max_stop_risk_fraction:.1%}\n"
        f"Дневная потеря: {profile.max_daily_loss_fraction:.1%}\n"
        f"Максимальная просадка: {profile.max_drawdown_fraction:.1%}\n"
        f"Открытых позиций: {profile.max_open_positions}\n"
        f"Максимальное плечо: {profile.max_leverage}x\n"
        f"Порог сигнала: {profile.min_signal_confidence:.0%}\n\n"
        "Изменение: <code>/setrisk max_leverage 10</code>"
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
@router.message(F.text == "Система")
async def system_status(message: Message, session: AsyncSession) -> None:
    health = list(await session.scalars(select(ServiceHealthRecord)))
    champion = await session.scalar(
        select(ModelVersionRecord)
        .where(ModelVersionRecord.stage == "CHAMPION")
        .order_by(ModelVersionRecord.activated_at.desc())
        .limit(1)
    )
    lines = ["<b>Состояние системы</b>"]
    if health:
        lines.extend(f"• {item.service}/{item.instance_id}: {item.status}" for item in health)
    else:
        lines.append("Health-события еще не поступали.")
    lines.append(f"Модель: {champion.version if champion else 'champion еще не назначен'}")
    await message.answer("\n".join(lines))


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
    service = PortfolioService(session)
    user = await service.get_owner(message.from_user.id)
    if user is None:
        raise PortfolioError("Владелец не инициализирован")
    return service, user.id


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
