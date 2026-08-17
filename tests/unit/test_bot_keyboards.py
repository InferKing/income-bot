from income_tg.bot.keyboards import (
    BUY_BUTTON,
    DEPOSIT_BUTTON,
    HELP_BUTTON,
    RECONCILE_BUTTON,
    SELL_BUTTON,
    SYSTEM_BUTTON,
    WITHDRAW_BUTTON,
    command_example_keyboard,
    help_keyboard,
    main_keyboard,
)


def test_main_keyboard_exposes_common_operations() -> None:
    keyboard = main_keyboard()
    labels = {button.text for row in keyboard.keyboard for button in row}

    assert {
        DEPOSIT_BUTTON,
        WITHDRAW_BUTTON,
        BUY_BUTTON,
        SELL_BUTTON,
        RECONCILE_BUTTON,
        SYSTEM_BUTTON,
        HELP_BUTTON,
    } <= labels
    assert keyboard.is_persistent


def test_help_keyboard_has_direct_navigation_callbacks() -> None:
    callbacks = {
        button.callback_data
        for row in help_keyboard().inline_keyboard
        for button in row
        if button.callback_data is not None
    }

    assert {"menu:portfolio", "menu:signals", "menu:stats", "menu:risk", "menu:status"} <= callbacks


def test_command_example_button_copies_full_command() -> None:
    keyboard = command_example_keyboard("/deposit USDT 100")
    button = keyboard.inline_keyboard[0][0]

    assert button.copy_text is not None
    assert button.copy_text.text == "/deposit USDT 100"
