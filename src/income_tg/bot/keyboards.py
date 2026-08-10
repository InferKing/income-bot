from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Портфели"), KeyboardButton(text="Сигналы")],
            [KeyboardButton(text="Статистика"), KeyboardButton(text="Риск")],
            [KeyboardButton(text="Добавить операцию"), KeyboardButton(text="Сверить остатки")],
            [KeyboardButton(text="Система"), KeyboardButton(text="Помощь")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )
