from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from config import SUBJECTS


def main_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📊 Ваша статистика"),
                KeyboardButton(text="📝 Начать тест"),
            ],
        ],
        resize_keyboard=True,
    )


def stats_subject_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📈 Общая статистика", callback_data="stats:overall")],
    ]
    for code, info in SUBJECTS.items():
        buttons.append([
            InlineKeyboardButton(
                text=f"{info['emoji']} {info['name']}",
                callback_data=f"stats:{code}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def test_subject_kb() -> InlineKeyboardMarkup:
    buttons = []
    for code, info in SUBJECTS.items():
        buttons.append([
            InlineKeyboardButton(
                text=f"{info['emoji']} {info['name']}",
                callback_data=f"test_subj:{code}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def test_mode_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Полный вариант", callback_data="mode:full")],
        [InlineKeyboardButton(text="❓ Вопрос-ответ", callback_data="mode:random")],
        [InlineKeyboardButton(text="🔢 Конкретный номер", callback_data="mode:specific")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back:subject")],
    ])


def question_count_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="5", callback_data="count:5"),
            InlineKeyboardButton(text="10", callback_data="count:10"),
        ],
        [
            InlineKeyboardButton(text="15", callback_data="count:15"),
            InlineKeyboardButton(text="20", callback_data="count:20"),
        ],
        [InlineKeyboardButton(text="✏️ Другое", callback_data="count:custom")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back:mode")],
    ])


def next_question_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➡️ Далее", callback_data="next"),
            InlineKeyboardButton(text="⏹ Завершить", callback_data="finish"),
        ],
    ])


def finish_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Результаты", callback_data="finish")],
    ])


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 В меню", callback_data="back:menu")],
    ])


def stats_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ К статистике", callback_data="back:stats")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="back:menu")],
    ])
