import asyncio
from html import escape as html_escape

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext

from states import TestStates
from keyboards import (
    main_menu_kb,
    stats_subject_kb,
    stats_back_kb,
    test_subject_kb,
    test_mode_kb,
    question_count_kb,
    next_question_kb,
    finish_kb,
    back_to_menu_kb,
)
from database import db
from config import SUBJECTS, MAX_VARIANTS, MAX_CUSTOM_QUESTIONS

router = Router()

#  Вспомогательные функции

async def safe_delete(msg: Message):
    """Удалить сообщение, игнорируя ошибки."""
    try:
        await msg.delete()
    except Exception:
        pass


async def delete_by_id(bot: Bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def cleanup_old(bot: Bot, chat_id: int, state: FSMContext):
    """Удалить предыдущее сообщение бота, если оно сохранено."""
    data = await state.get_data()
    old = data.get("bot_message_id")
    if old:
        await delete_by_id(bot, chat_id, old)


async def show(source, state: FSMContext, text: str, reply_markup=None):
    """Универсальный показ: редактирует для callback, отправляет/редактирует для message."""
    if isinstance(source, CallbackQuery):
        try:
            await source.message.edit_text(text, reply_markup=reply_markup)
            await state.update_data(bot_message_id=source.message.message_id)
            return
        except Exception:
            # Не получилось отредактировать — удаляем и шлём новое
            try:
                await source.message.delete()
            except Exception:
                pass
            msg = await source.message.answer(text, reply_markup=reply_markup)
            await state.update_data(bot_message_id=msg.message_id)
    else:
        # Message — пытаемся отредактировать отслеживаемое сообщение бота
        data = await state.get_data()
        msg_id = data.get("bot_message_id")
        if msg_id:
            try:
                await source.bot.edit_message_text(
                    text=text,
                    chat_id=source.chat.id,
                    message_id=msg_id,
                    reply_markup=reply_markup,
                )
                return
            except Exception:
                await delete_by_id(source.bot, source.chat.id, msg_id)

        msg = await source.answer(text, reply_markup=reply_markup)
        await state.update_data(bot_message_id=msg.message_id)


def normalize_answer(answer: str) -> str:
    """Нормализация ответа для сравнения."""
    a = answer.strip().lower()
    a = a.replace(",", "").replace(";", "").replace("  ", " ")
    return a


def pct(correct: int, total: int) -> str:
    if total == 0:
        return "0.0"
    return f"{correct / total * 100:.1f}"

#  /start

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await cleanup_old(message.bot, message.chat.id, state)
    await state.clear()
    await safe_delete(message)

    user = message.from_user
    await db.get_or_create_user(user.id, user.username, user.first_name)

    text = (
        "👋 <b>Привет!</b>\n\n"
        "Я — бот для подготовки к ЕГЭ. Помогу тебе подготовиться к экзаменам по "
        "<b>русскому языку</b>, <b>базовой математике</b> и "
        "<b>профильной математике</b>.\n\n"
        "Решай задания, отслеживай свою статистику "
        "и улучшай результаты по предметам!\n\n"
        "Используй кнопки меню внизу для навигации 👇"
    )
    await message.answer(text, reply_markup=main_menu_kb())

#  Статистика

@router.message(F.text == "📊 Ваша статистика")
async def btn_stats(message: Message, state: FSMContext):
    await cleanup_old(message.bot, message.chat.id, state)
    await state.clear()
    await safe_delete(message)

    msg = await message.answer(
        "📊 <b>Статистика</b>\n\nВыберите раздел:",
        reply_markup=stats_subject_kb(),
    )
    await state.update_data(bot_message_id=msg.message_id)


@router.callback_query(F.data.startswith("stats:"))
async def cb_stats(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    key = callback.data.split(":")[1]
    uid = callback.from_user.id

    if key == "overall":
        s = await db.get_overall_stats(uid)
        text = (
            "📈 <b>Общая статистика</b>\n\n"
            f"📋 Всего решено заданий: <b>{s['total']}</b>\n"
            f"✅ Верных: <b>{s['correct']}</b> ({pct(s['correct'], s['total'])}%)\n"
            f"❌ Неверных: <b>{s['incorrect']}</b> "
            f"({pct(s['incorrect'], s['total'])}%)\n"
        )
        if s["total"] == 0:
            text += "\n💡 Вы ещё не решали заданий."
    else:
        info = SUBJECTS[key]
        s = await db.get_subject_stats(uid, key)
        text = (
            f"📊 <b>Статистика: {info['emoji']} {info['name']}</b>\n\n"
            f"📋 Всего решено: <b>{s['total']}</b>\n"
            f"✅ Верных: <b>{s['correct']}</b> ({pct(s['correct'], s['total'])}%)\n"
            f"❌ Неверных: <b>{s['incorrect']}</b> "
            f"({pct(s['incorrect'], s['total'])}%)\n"
        )

        if s["by_task"]:
            text += "\n📌 <b>По заданиям:</b>\n"
            for t in s["by_task"]:
                tp = float(pct(t["correct"], t["total"]))
                icon = "✅" if tp >= 70 else "⚠️" if tp >= 40 else "❌"
                text += (
                    f"  №{t['task_number']}: {icon} "
                    f"{t['correct']}/{t['total']} ({tp:.0f}%)\n"
                )

        if s["total"] == 0:
            text += "\n💡 Вы ещё не решали заданий по этому предмету."

    await show(callback, state, text, reply_markup=stats_back_kb())


# ════════════════════════════════════════════
#  Начать тест — выбор предмета
# ════════════════════════════════════════════

@router.message(F.text == "📝 Начать тест")
async def btn_start_test(message: Message, state: FSMContext):
    await cleanup_old(message.bot, message.chat.id, state)
    await state.clear()
    await safe_delete(message)

    msg = await message.answer(
        "📝 <b>Новый тест</b>\n\nВыберите предмет:",
        reply_markup=test_subject_kb(),
    )
    await state.update_data(bot_message_id=msg.message_id)
    await state.set_state(TestStates.choosing_subject)


@router.callback_query(F.data.startswith("test_subj:"))
async def cb_test_subject(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    subject = callback.data.split(":")[1]
    info = SUBJECTS[subject]
    await state.update_data(subject=subject)

    await show(
        callback, state,
        f"{info['emoji']} <b>{info['name']}</b>\n\nВыберите режим:",
        reply_markup=test_mode_kb(),
    )
    await state.set_state(TestStates.choosing_mode)

#  Выбор режима

@router.callback_query(F.data.startswith("mode:"))
async def cb_test_mode(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    mode = callback.data.split(":")[1]
    data = await state.get_data()
    subject = data["subject"]
    await state.update_data(mode=mode)

    if mode == "full":
        await show(
            callback, state,
            f"📋 <b>Полный вариант</b>\n\n"
            f"Введите номер варианта от 1 до {MAX_VARIANTS}:",
        )
        await state.set_state(TestStates.entering_variant)

    elif mode == "random":
        await show(
            callback, state,
            "❓ <b>Вопрос-ответ</b>\n\nСколько вопросов хотите решить?",
            reply_markup=question_count_kb(),
        )
        await state.set_state(TestStates.choosing_count)

    elif mode == "specific":
        available = await db.get_available_task_numbers(subject)
        if not available:
            await show(
                callback, state,
                "❌ Нет доступных заданий по этому предмету.\n"
                "Запустите парсер или seed.py для загрузки вопросов.",
                reply_markup=back_to_menu_kb(),
            )
            return

        nums = ", ".join(str(n) for n in available)
        await show(
            callback, state,
            f"🔢 <b>Конкретный номер</b>\n\n"
            f"Доступные номера заданий: <b>{nums}</b>\n\n"
            f"Введите номер задания:",
        )
        await state.set_state(TestStates.choosing_task_number)

#  Ввод номера варианта (текст)

@router.message(StateFilter(TestStates.entering_variant))
async def msg_variant_number(message: Message, state: FSMContext):
    await safe_delete(message)

    try:
        num = int(message.text.strip())
        if not 1 <= num <= MAX_VARIANTS:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        err = await message.answer(f"⚠️ Введите число от 1 до {MAX_VARIANTS}")
        asyncio.create_task(_delete_later(message.bot, message.chat.id, err.message_id))
        return

    data = await state.get_data()
    subject = data["subject"]
    questions = await db.get_variant_questions(subject, num)

    if not questions:
        err = await message.answer(
            "❌ Нет вопросов для этого варианта. Попробуйте другой."
        )
        asyncio.create_task(_delete_later(message.bot, message.chat.id, err.message_id))
        return

    await state.update_data(
        questions=questions,
        current_idx=0,
        results=[],
    )
    await _show_question(message.bot, message.chat.id, state)

#  Ввод номера задания (конкретный номер)

@router.message(StateFilter(TestStates.choosing_task_number))
async def msg_task_number(message: Message, state: FSMContext):
    await safe_delete(message)

    data = await state.get_data()
    subject = data["subject"]
    available = await db.get_available_task_numbers(subject)

    try:
        num = int(message.text.strip())
        if num not in available:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        nums = ", ".join(str(n) for n in available)
        err = await message.answer(f"⚠️ Доступные номера: {nums}")
        asyncio.create_task(_delete_later(message.bot, message.chat.id, err.message_id))
        return

    await state.update_data(task_number=num)
    await show(
        message, state,
        f"🔢 <b>Задание №{num}</b>\n\nСколько вопросов хотите решить?",
        reply_markup=question_count_kb(),
    )
    await state.set_state(TestStates.choosing_count)

#  Выбор количества вопросов

@router.callback_query(F.data.startswith("count:"))
async def cb_question_count(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    value = callback.data.split(":")[1]

    if value == "custom":
        await show(
            callback, state,
            f"✏️ Введите количество вопросов (1–{MAX_CUSTOM_QUESTIONS}):",
        )
        await state.set_state(TestStates.entering_custom_count)
        return

    count = int(value)
    await _start_questions(callback, state, count)


@router.message(StateFilter(TestStates.entering_custom_count))
async def msg_custom_count(message: Message, state: FSMContext):
    await safe_delete(message)

    try:
        count = int(message.text.strip())
        if not 1 <= count <= MAX_CUSTOM_QUESTIONS:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        err = await message.answer(f"⚠️ Введите число от 1 до {MAX_CUSTOM_QUESTIONS}")
        asyncio.create_task(_delete_later(message.bot, message.chat.id, err.message_id))
        return

    await _start_questions(message, state, count)

#  Запуск решения вопросов

async def _start_questions(source, state: FSMContext, count: int):
    data = await state.get_data()
    subject = data["subject"]
    task_number = data.get("task_number")

    questions = await db.get_random_questions(subject, count, task_number)

    if not questions:
        await show(
            source, state,
            "❌ Недостаточно вопросов в базе данных.\n"
            "Запустите парсер или seed.py для загрузки вопросов.",
            reply_markup=back_to_menu_kb(),
        )
        return

    await state.update_data(
        questions=questions,
        current_idx=0,
        results=[],
    )

    if isinstance(source, CallbackQuery):
        bot = source.bot
        chat_id = source.message.chat.id
    else:
        bot = source.bot
        chat_id = source.chat.id

    await _show_question(bot, chat_id, state)


async def _show_question(bot: Bot, chat_id: int, state: FSMContext):
    """Показать текущий вопрос."""
    data = await state.get_data()
    questions = data["questions"]
    idx = data["current_idx"]
    q = questions[idx]
    total = len(questions)
    info = SUBJECTS[q["subject_code"]]

    text = (
        f"📝 <b>Вопрос {idx + 1}/{total}</b>  |  "
        f"Задание №{q['task_number']} ({info['emoji']} {info['name']})\n\n"
        f"{html_escape(q['text'])}\n\n"
        f"✏️ <i>Введите ваш ответ:</i>"
    )

    msg_id = data.get("bot_message_id")
    if msg_id:
        try:
            await bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=msg_id,
            )
            await state.set_state(TestStates.solving)
            return
        except Exception:
            await delete_by_id(bot, chat_id, msg_id)

    msg = await bot.send_message(chat_id, text)
    await state.update_data(bot_message_id=msg.message_id)
    await state.set_state(TestStates.solving)

#  Приём ответа

@router.message(StateFilter(TestStates.solving))
async def msg_answer(message: Message, state: FSMContext):
    await safe_delete(message)

    if not message.text:
        return

    user_answer = message.text.strip()
    data = await state.get_data()
    questions = data["questions"]
    idx = data["current_idx"]
    q = questions[idx]
    total = len(questions)

    correct_answer = q["answer"]
    is_correct = normalize_answer(user_answer) == normalize_answer(correct_answer)

    # Сохраняем попытку
    await db.add_attempt(message.from_user.id, q["id"], user_answer, is_correct)

    results = data.get("results", [])
    results.append({
        "question_id": q["id"],
        "task_number": q["task_number"],
        "is_correct": is_correct,
        "user_answer": user_answer,
        "correct_answer": correct_answer,
    })
    await state.update_data(results=results)

    # Формируем сообщение с результатом
    if is_correct:
        text = "✅ <b>Верно!</b>\n\n"
    else:
        text = (
            f"❌ <b>Неверно!</b>\n"
            f"Правильный ответ: <b>{html_escape(correct_answer)}</b>\n\n"
        )

    if q.get("explanation"):
        text += f"💡 {html_escape(q['explanation'])}\n\n"

    text += f"Прогресс: {idx + 1}/{total}"

    is_last = (idx + 1 >= total)
    kb = finish_kb() if is_last else next_question_kb()

    await show(message, state, text, reply_markup=kb)
    await state.set_state(TestStates.showing_result)

#  Далее / Завершить

@router.callback_query(F.data == "next", StateFilter(TestStates.showing_result))
async def cb_next(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    new_idx = data["current_idx"] + 1
    await state.update_data(current_idx=new_idx)
    await _show_question(callback.bot, callback.message.chat.id, state)


@router.callback_query(F.data == "finish")
async def cb_finish(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    results = data.get("results", [])

    total = len(results)
    correct = sum(1 for r in results if r["is_correct"])

    text = (
        f"📊 <b>Результаты теста</b>\n\n"
        f"📋 Всего вопросов: <b>{total}</b>\n"
        f"✅ Верных: <b>{correct}</b> ({pct(correct, total)}%)\n"
        f"❌ Неверных: <b>{total - correct}</b> ({pct(total - correct, total)}%)\n"
    )

    # Разбивка по заданиям
    by_task: dict[int, dict] = {}
    for r in results:
        tn = r["task_number"]
        if tn not in by_task:
            by_task[tn] = {"total": 0, "correct": 0}
        by_task[tn]["total"] += 1
        if r["is_correct"]:
            by_task[tn]["correct"] += 1

    if by_task:
        text += "\n📌 <b>По заданиям:</b>\n"
        for tn in sorted(by_task):
            t = by_task[tn]
            tp = float(pct(t["correct"], t["total"]))
            icon = "✅" if tp >= 70 else "⚠️" if tp >= 40 else "❌"
            text += f"  №{tn}: {icon} {t['correct']}/{t['total']} ({tp:.0f}%)\n"

    await show(callback, state, text, reply_markup=back_to_menu_kb())
    # Сохраняем bot_message_id, но очищаем тестовые данные
    msg_data = await state.get_data()
    bot_msg = msg_data.get("bot_message_id")
    await state.clear()
    if bot_msg:
        await state.update_data(bot_message_id=bot_msg)

#  Кнопки «Назад»

@router.callback_query(F.data.startswith("back:"))
async def cb_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    target = callback.data.split(":")[1]

    if target == "menu":
        bot_msg = (await state.get_data()).get("bot_message_id")
        await state.clear()
        if bot_msg:
            await delete_by_id(callback.bot, callback.message.chat.id, bot_msg)
        else:
            try:
                await callback.message.delete()
            except Exception:
                pass
        await callback.message.answer(
            "Выберите действие 👇", reply_markup=main_menu_kb()
        )

    elif target == "stats":
        await show(
            callback, state,
            "📊 <b>Статистика</b>\n\nВыберите раздел:",
            reply_markup=stats_subject_kb(),
        )

    elif target == "subject":
        await show(
            callback, state,
            "📝 <b>Новый тест</b>\n\nВыберите предмет:",
            reply_markup=test_subject_kb(),
        )
        await state.set_state(TestStates.choosing_subject)

    elif target == "mode":
        data = await state.get_data()
        subject = data.get("subject", "")
        info = SUBJECTS.get(subject, {})
        await show(
            callback, state,
            f"{info.get('emoji', '')} <b>{info.get('name', '')}</b>\n\nВыберите режим:",
            reply_markup=test_mode_kb(),
        )
        await state.set_state(TestStates.choosing_mode)

#  Вспомогательная: удаление сообщения с задержкой

async def _delete_later(bot: Bot, chat_id: int, message_id: int, delay: float = 2.0):
    await asyncio.sleep(delay)
    await delete_by_id(bot, chat_id, message_id)

#  Неизвестные сообщения (catch-all, регистрируется последним)

@router.message()
async def unknown_message(message: Message, state: FSMContext):
    await safe_delete(message)
    err = await message.answer("⚠️ Неизвестная команда. Используйте меню внизу 👇")
    asyncio.create_task(_delete_later(message.bot, message.chat.id, err.message_id))
