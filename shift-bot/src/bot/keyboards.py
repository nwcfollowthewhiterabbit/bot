from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

SKIP_COMMENT = "Пропустити"


def share_contact_keyboard() -> ReplyKeyboardMarkup:
    button = KeyboardButton(text="Поделиться номером", request_contact=True)
    return ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True)


def employee_menu(is_manager: bool) -> ReplyKeyboardMarkup:
    if is_manager:
        rows = [
            [
                KeyboardButton(text="В очікуванні"),
                KeyboardButton(text="Мої співробітники"),
            ],
            [KeyboardButton(text="Переглянути таблицю")],
        ]
        return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
    rows = [
        [KeyboardButton(text="Добавить новую смену")],
        [KeyboardButton(text="Редактировать поданую смену")],
        [
            KeyboardButton(text="Мои смены (7 дней)"),
            KeyboardButton(text="Заявки в ожидании"),
        ],
        [KeyboardButton(text="Помощь")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def skip_comment_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=SKIP_COMMENT)]], resize_keyboard=True
    )


def manager_decision_keyboard(shift_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Підтвердити", callback_data=f"approve:{shift_id}"
                ),
                InlineKeyboardButton(
                    text="Відхилити", callback_data=f"decline:{shift_id}"
                ),
            ]
        ]
    )
