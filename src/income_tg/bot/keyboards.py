from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

PORTFOLIOS_BUTTON = "💼 Портфели"
SIGNALS_BUTTON = "📈 Сигналы"
STATISTICS_BUTTON = "📊 Статистика"
RISK_BUTTON = "🛡️ Риск"
DEPOSIT_BUTTON = "➕ Пополнить"
WITHDRAW_BUTTON = "➖ Вывести"
BUY_BUTTON = "🟢 Купить"
SELL_BUTTON = "🔴 Продать"
RECONCILE_BUTTON = "🔄 Сверить остатки"
SYSTEM_BUTTON = "🖥️ Система"
HELP_BUTTON = "❓ Помощь"


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=PORTFOLIOS_BUTTON), KeyboardButton(text=SIGNALS_BUTTON)],
            [KeyboardButton(text=STATISTICS_BUTTON), KeyboardButton(text=RISK_BUTTON)],
            [KeyboardButton(text=DEPOSIT_BUTTON), KeyboardButton(text=WITHDRAW_BUTTON)],
            [KeyboardButton(text=BUY_BUTTON), KeyboardButton(text=SELL_BUTTON)],
            [KeyboardButton(text=RECONCILE_BUTTON)],
            [KeyboardButton(text=SYSTEM_BUTTON), KeyboardButton(text=HELP_BUTTON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие",
    )


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💼 /portfolio", callback_data="menu:portfolio"),
                InlineKeyboardButton(text="📈 /signals", callback_data="menu:signals"),
            ],
            [
                InlineKeyboardButton(text="📊 /stats", callback_data="menu:stats"),
                InlineKeyboardButton(text="🛡️ /risk", callback_data="menu:risk"),
            ],
            [
                InlineKeyboardButton(text="➕ Операции", callback_data="help:operations"),
                InlineKeyboardButton(text="🔄 Сверка", callback_data="help:reconcile"),
            ],
            [InlineKeyboardButton(text="🖥️ /status", callback_data="menu:status")],
        ]
    )


def operations_help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Пополнение", callback_data="help:deposit"),
                InlineKeyboardButton(text="➖ Вывод", callback_data="help:withdraw"),
            ],
            [
                InlineKeyboardButton(text="🟢 Покупка", callback_data="help:buy"),
                InlineKeyboardButton(text="🔴 Продажа", callback_data="help:sell"),
            ],
            [InlineKeyboardButton(text="🔄 Сверка", callback_data="help:reconcile")],
            [InlineKeyboardButton(text="← Все разделы", callback_data="help:main")],
        ]
    )


def command_example_keyboard(command: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Скопировать команду",
                    copy_text=CopyTextButton(text=command),
                )
            ],
            [
                InlineKeyboardButton(text="← Операции", callback_data="help:operations"),
                InlineKeyboardButton(text="🏠 Помощь", callback_data="help:main"),
            ],
        ]
    )


def system_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="system:refresh")],
            [
                InlineKeyboardButton(
                    text="🔎 Подробнее о кандидате", callback_data="system:candidate"
                )
            ],
            [
                InlineKeyboardButton(text="📈 Сигналы", callback_data="menu:signals"),
                InlineKeyboardButton(text="📊 Статистика", callback_data="menu:stats"),
            ],
            [InlineKeyboardButton(text="❓ Что означает статус?", callback_data="help:system")],
        ]
    )


def candidate_details_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить кандидата", callback_data="system:candidate")],
            [InlineKeyboardButton(text="← Вернуться к системе", callback_data="system:refresh")],
        ]
    )


def system_help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← Вернуться к системе", callback_data="system:refresh")],
            [InlineKeyboardButton(text="🏠 Все разделы", callback_data="help:main")],
        ]
    )
