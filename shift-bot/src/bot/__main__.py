import asyncio
import logging
import re
from datetime import date, datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from .config import Settings
from .keyboards import (
    SKIP_COMMENT,
    employee_menu,
    manager_decision_keyboard,
    share_contact_keyboard,
    skip_comment_keyboard,
)
from .sheets import (
    SHIFT_STATUS_APPROVED,
    SHIFT_STATUS_DECLINED,
    Employee,
    SheetsGateway,
    ShiftInput,
)
from .states import ManagerComment, ShiftEdit, ShiftForm
from .utils import format_date, parse_hours, parse_user_date


class AuthorizationRegistry:
    def __init__(self) -> None:
        self._users: Dict[int, Employee] = {}

    def set_employee(self, telegram_id: int, employee: Employee) -> None:
        self._users[telegram_id] = employee

    def get_employee(self, telegram_id: int) -> Optional[Employee]:
        return self._users.get(telegram_id)


def sanitize_phone(raw: str | None) -> Optional[str]:
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", raw)
    if digits.startswith("0") and len(digits) == 10:
        digits = f"38{digits}"
    if digits.startswith("380"):
        return digits
    return digits or None


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.load()
    timezone = ZoneInfo(settings.timezone)
    sheet_link = f"https://docs.google.com/spreadsheets/d/{settings.sheet_id}"
    sheets = SheetsGateway(settings)
    await sheets.ensure_data_validations()
    auth_registry = AuthorizationRegistry()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    async def ensure_authorized(message: Message) -> Optional[Employee]:
        employee = auth_registry.get_employee(message.from_user.id)
        if employee:
            return employee
        await message.answer(
            "Спочатку авторизуйся, поділившись номером.",
            reply_markup=share_contact_keyboard(),
        )
        return None

    @dp.message(CommandStart())
    async def handle_start(message: Message, state: FSMContext) -> None:
        await state.clear()
        employee = auth_registry.get_employee(message.from_user.id)
        if employee:
            await message.answer(
                f"Вітаю, {employee.name}! Обери дію в меню.",
                reply_markup=employee_menu(employee.is_manager),
            )
        else:
            await message.answer(
                "Привіт! Поділись номером телефону, щоб пройти авторизацію.",
                reply_markup=share_contact_keyboard(),
            )

    @dp.message(F.contact)
    async def handle_contact(message: Message, state: FSMContext) -> None:
        await state.clear()
        contact = message.contact
        if contact.user_id and contact.user_id != message.from_user.id:
            await message.answer(
                "Надішли контакт саме зі свого аккаунту.",
                reply_markup=share_contact_keyboard(),
            )
            return
        phone = sanitize_phone(contact.phone_number)
        if not phone:
            await message.answer(
                "Не вдалося розпізнати номер. Спробуй ще раз.",
                reply_markup=share_contact_keyboard(),
            )
            return
        employee = await sheets.fetch_employee_by_phone(phone)
        if not employee:
            await message.answer(
                "Твого номеру немає в списку співробітників. Звернися до адміністратора.",
                reply_markup=share_contact_keyboard(),
            )
            return
        auth_registry.set_employee(message.from_user.id, employee)
        await message.answer(
            f"Вітаю, {employee.name}! Меню доступне нижче.",
            reply_markup=employee_menu(employee.is_manager),
        )

    @dp.message(F.text == "Добавить новую смену")
    async def request_shift_date(message: Message, state: FSMContext) -> None:
        employee = await ensure_authorized(message)
        if not employee:
            return
        await state.set_state(ShiftForm.date)
        await state.update_data(employee_name=employee.name, manager=employee.manager_name)
        await message.answer(
            "Вкажи дату зміни у форматі ДД.ММ.РРРР.",
            reply_markup=ReplyKeyboardRemove(),
        )

    @dp.message(ShiftForm.date)
    async def handle_shift_date(message: Message, state: FSMContext) -> None:
        shift_date = parse_user_date(message.text or "")
        if not shift_date:
            await message.answer("Невірний формат дати. Спробуй ще раз (ДД.ММ.РРРР).")
            return
        await state.update_data(shift_date=shift_date.isoformat())
        await state.set_state(ShiftForm.shift_hours)
        await message.answer("Скільки годин тривав твій робочий день? (число)")

    @dp.message(ShiftForm.shift_hours)
    async def handle_shift_hours(message: Message, state: FSMContext) -> None:
        hours = parse_hours(message.text or "")
        if hours is None:
            await message.answer("Вкажи кількість годин числом, наприклад 8 або 7.5.")
            return
        await state.update_data(shift_hours=hours)
        await state.set_state(ShiftForm.overtime_hours)
        await message.answer("Скільки годин овертайму? (0, якщо не було)")

    @dp.message(ShiftForm.overtime_hours)
    async def handle_overtime_hours(message: Message, state: FSMContext) -> None:
        hours = parse_hours(message.text or "")
        if hours is None:
            await message.answer("Вкажи годинник овертайму числом.")
            return
        await state.update_data(overtime_hours=hours)
        await state.set_state(ShiftForm.comment)
        await message.answer(
            "Додай коментар або натисни «Пропустити».",
            reply_markup=skip_comment_keyboard(),
        )

    @dp.message(ShiftForm.comment)
    async def handle_shift_comment(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        employee = await ensure_authorized(message)
        if not employee:
            return
        comment = "" if message.text == SKIP_COMMENT else (message.text or "")
        shift_date = date.fromisoformat(data["shift_date"])
        shift = ShiftInput(
            employee_name=employee.name,
            shift_date=shift_date,
            shift_hours=float(data["shift_hours"]),
            overtime_hours=float(data["overtime_hours"]),
            comment=comment,
            submitted_at=datetime.now(timezone),
            manager_name=employee.manager_name,
        )
        shift_id = await sheets.append_shift(shift)
        await state.clear()
        await message.answer(
            f"Зміна #{shift_id} збережена та очікує підтвердження.",
            reply_markup=employee_menu(employee.is_manager),
        )

    @dp.message(F.text == "Редактировать поданую смену")
    async def start_editing_shift(message: Message, state: FSMContext) -> None:
        employee = await ensure_authorized(message)
        if not employee:
            return
        await state.clear()
        shifts = await sheets.get_employee_shifts(
            employee.name, days_back=7, only_pending=True
        )
        if not shifts:
            await message.answer(
                "Нет заявок для редактирования. Доступно только для заявок, поданных не позже 7 днів тому та зі статусом «Очікує».",
                reply_markup=employee_menu(employee.is_manager),
            )
            return
        lines = [
            f"#{shift.shift_id} — {format_date(shift.shift_date)}"
            f"\nГодини: {shift.shift_hours}, Овертайм: {shift.overtime_hours}"
            f"\nКоментар: {shift.comment or '-'}"
            for shift in shifts
        ]
        await state.set_state(ShiftEdit.select_shift)
        await message.answer(
            "Доступні заявки для редагування:\n"
            + "\n\n".join(lines)
            + "\n\nВведи номер заявки, яку треба змінити.",
            reply_markup=ReplyKeyboardRemove(),
        )

    @dp.message(ShiftEdit.select_shift)
    async def handle_edit_selection(message: Message, state: FSMContext) -> None:
        employee = await ensure_authorized(message)
        if not employee:
            await state.clear()
            return
        text = (message.text or "").replace("#", "").strip()
        if not text.isdigit():
            await message.answer("Вкажи номер заявки числом.")
            return
        shift_id = int(text)
        record = await sheets.get_editable_shift(
            employee_name=employee.name,
            shift_id=shift_id,
            max_days_since_submission=7,
        )
        if not record:
            await message.answer(
                "Заявка недоступна для редагування. Переконайся, що вона очікує підтвердження і подана не пізніше 7 днів тому.",
            )
            return
        await state.update_data(
            edit_shift_id=shift_id,
            previous_shift_hours=record.shift_hours,
            previous_overtime_hours=record.overtime_hours,
            previous_comment=record.comment or "",
        )
        await state.set_state(ShiftEdit.date)
        await message.answer(
            f"Поточна дата зміни: {format_date(record.shift_date)}. Введи нову дату у форматі ДД.ММ.РРРР."
        )

    @dp.message(ShiftEdit.date)
    async def handle_edit_date(message: Message, state: FSMContext) -> None:
        shift_date = parse_user_date(message.text or "")
        if not shift_date:
            await message.answer("Невірний формат дати. Спробуй (ДД.ММ.РРРР).")
            return
        await state.update_data(edit_shift_date=shift_date.isoformat())
        data = await state.get_data()
        prev_hours_value = data.get("previous_shift_hours")
        prev_hours = prev_hours_value if prev_hours_value is not None else "-"
        await state.set_state(ShiftEdit.shift_hours)
        await message.answer(
            f"Скільки годин тривала зміна? Поточне значення: {prev_hours}."
        )

    @dp.message(ShiftEdit.shift_hours)
    async def handle_edit_shift_hours(message: Message, state: FSMContext) -> None:
        hours = parse_hours(message.text or "")
        if hours is None:
            await message.answer("Вкажи кількість годин числом.")
            return
        await state.update_data(edit_shift_hours=hours)
        data = await state.get_data()
        prev_overtime_value = data.get("previous_overtime_hours")
        prev_overtime = (
            prev_overtime_value if prev_overtime_value is not None else "-"
        )
        await state.set_state(ShiftEdit.overtime_hours)
        await message.answer(
            f"Скільки годин овертайму? Поточне значення: {prev_overtime}."
        )

    @dp.message(ShiftEdit.overtime_hours)
    async def handle_edit_overtime_hours(message: Message, state: FSMContext) -> None:
        hours = parse_hours(message.text or "")
        if hours is None:
            await message.answer("Вкажи годинник овертайму числом.")
            return
        await state.update_data(edit_overtime_hours=hours)
        data = await state.get_data()
        prev_comment = data.get("previous_comment") or "-"
        await state.set_state(ShiftEdit.comment)
        await message.answer(
            f"Поточний коментар: {prev_comment}\nВведи новий або натисни «{SKIP_COMMENT}».",
            reply_markup=skip_comment_keyboard(),
        )

    @dp.message(ShiftEdit.comment)
    async def finalize_shift_edit(message: Message, state: FSMContext) -> None:
        employee = await ensure_authorized(message)
        if not employee:
            await state.clear()
            return
        data = await state.get_data()
        try:
            shift_id = int(data["edit_shift_id"])
        except (KeyError, ValueError, TypeError):
            await state.clear()
            await message.answer(
                "Не вдалося знайти заявку. Спробуй ще раз.",
                reply_markup=employee_menu(employee.is_manager),
            )
            return
        comment = "" if message.text == SKIP_COMMENT else (message.text or "")
        try:
            shift_date = date.fromisoformat(data["edit_shift_date"])
        except (KeyError, ValueError):
            await state.clear()
            await message.answer(
                "Не вдалося зберегти зміну — повтори спробу.",
                reply_markup=employee_menu(employee.is_manager),
            )
            return
        shift = ShiftInput(
            employee_name=employee.name,
            shift_date=shift_date,
            shift_hours=float(data["edit_shift_hours"]),
            overtime_hours=float(data["edit_overtime_hours"]),
            comment=comment,
            submitted_at=datetime.now(timezone),
            manager_name=employee.manager_name,
        )
        updated = await sheets.update_shift_details(
            shift_id=shift_id,
            employee_name=employee.name,
            updated=shift,
            max_days_since_submission=7,
        )
        await state.clear()
        if not updated:
            await message.answer(
                "Не вдалося оновити заявку. Вона могла бути підтверджена або минуло більше 7 днів.",
                reply_markup=employee_menu(employee.is_manager),
            )
            return
        await message.answer(
            f"Заявка #{shift_id} оновлена. Очікує підтвердження.",
            reply_markup=employee_menu(employee.is_manager),
        )

    @dp.message(F.text == "Мои смены (7 дней)")
    async def handle_recent_shifts(message: Message) -> None:
        employee = await ensure_authorized(message)
        if not employee:
            return
        shifts = await sheets.get_employee_shifts(employee.name, days_back=7)
        if not shifts:
            await message.answer("За останні 7 днів немає поданих заявок.")
            return
        lines = [
            f"#{shift.shift_id} — {format_date(shift.shift_date)} | {shift.status}"
            f"\nГодини: {shift.shift_hours}, Овертайм: {shift.overtime_hours}"
            f"\nКоментар: {shift.comment or '-'}"
            for shift in shifts
        ]
        await message.answer("\n\n".join(lines))

    @dp.message(F.text == "Заявки в ожидании")
    async def handle_pending_shifts(message: Message) -> None:
        employee = await ensure_authorized(message)
        if not employee:
            return
        shifts = await sheets.get_employee_shifts(
            employee.name, days_back=7, only_pending=True
        )
        if not shifts:
            await message.answer("Немає заявок у статусі очікування за останні 7 днів.")
            return
        lines = [
            f"#{shift.shift_id} — {format_date(shift.shift_date)}"
            f"\nГодини: {shift.shift_hours}, Овертайм: {shift.overtime_hours}"
            f"\nКоментар: {shift.comment or '-'}"
            for shift in shifts
        ]
        await message.answer("\n\n".join(lines))

    @dp.message(F.text == "В очікуванні")
    async def handle_manager_pending(message: Message) -> None:
        employee = await ensure_authorized(message)
        if not employee or not employee.is_manager:
            await message.answer("Ця дія доступна лише керівникам.")
            return
        shifts = await sheets.get_pending_for_manager(employee.name)
        if not shifts:
            await message.answer("Немає заявок, що очікують підтвердження.")
            return
        for shift in shifts:
            text = (
                f"Заявка #{shift.shift_id}\n"
                f"Співробітник: {shift.employee_name}\n"
                f"Дата: {format_date(shift.shift_date)}\n"
                f"Години: {shift.shift_hours} | Овертайм: {shift.overtime_hours}\n"
                f"Коментар: {shift.comment or '-'}"
            )
            await message.answer(text, reply_markup=manager_decision_keyboard(shift.shift_id))

    @dp.callback_query(F.data.startswith("approve:") | F.data.startswith("decline:"))
    async def handle_manager_decision(callback: CallbackQuery, state: FSMContext) -> None:
        employee = auth_registry.get_employee(callback.from_user.id)
        if not employee or not employee.is_manager:
            await callback.answer("Немає прав.", show_alert=True)
            return
        action, shift_id_str = callback.data.split(":")
        await state.set_state(ManagerComment.comment)
        await state.update_data(
            manager_action=action,
            shift_id=int(shift_id_str),
        )
        await callback.message.answer(
            "Додай коментар до рішення (або натисни «Пропустити»).",
            reply_markup=skip_comment_keyboard(),
        )
        await callback.answer()

    @dp.message(ManagerComment.comment)
    async def finalize_manager_decision(message: Message, state: FSMContext) -> None:
        employee = await ensure_authorized(message)
        if not employee or not employee.is_manager:
            await state.clear()
            return
        data = await state.get_data()
        shift_id = int(data["shift_id"])
        action = data["manager_action"]
        comment = "" if message.text == SKIP_COMMENT else (message.text or "")
        status = (
            SHIFT_STATUS_APPROVED if action == "approve" else SHIFT_STATUS_DECLINED
        )
        updated, changed = await sheets.update_shift_status(
            shift_id=shift_id,
            status=status,
            manager_name=employee.name,
            comment=comment,
            approved_at=datetime.now(timezone),
        )
        await state.clear()
        if updated is None:
            await message.answer("Заявку не знайдено.")
            return
        if not changed:
            await message.answer(
                f"Заявка #{shift_id} вже має статус «{updated.status}».",
                reply_markup=employee_menu(employee.is_manager),
            )
            return
        await message.answer(
            f"Статус заявки #{shift_id} змінено на «{status}».",
            reply_markup=employee_menu(employee.is_manager),
        )

    @dp.message(F.text == "Мої співробітники")
    async def handle_subordinates(message: Message) -> None:
        employee = await ensure_authorized(message)
        if not employee or not employee.is_manager:
            await message.answer("Ця дія доступна лише керівникам.")
            return
        subordinates = await sheets.list_subordinates(employee.name)
        if not subordinates:
            await message.answer("У тебе поки немає підлеглих.")
            return
        lines = [
            f"{sub.name} — {sub.phone}\nСтавка зміни: {sub.shift_rate}, овертайм: {sub.overtime_rate}"
            for sub in subordinates
        ]
        await message.answer("\n\n".join(lines))

    @dp.message(F.text == "Переглянути таблицю")
    async def handle_sheet_link(message: Message) -> None:
        employee = await ensure_authorized(message)
        if not employee or not employee.is_manager:
            await message.answer("Ця дія доступна лише керівникам.")
            return
        await message.answer(f"Спільна таблиця: {sheet_link}")

    @dp.message(F.text == "Помощь")
    async def handle_help(message: Message) -> None:
        employee = auth_registry.get_employee(message.from_user.id)
        base_help = (
            "Доступні дії:\n"
            "• \"Добавить новую смену\" — подати нову заявку.\n"
            "• \"Редактировать поданую смену\" — редагувати заявку зі статусом «Очікує», подану не пізніше 7 днів тому.\n"
            "• \"Мои смены (7 дней)\" — переглянути останні заявки.\n"
            "• \"Заявки в ожидании\" — відкриті заявки за останні 7 днів.\n"
        )
        manager_help = (
            "Меню керівника:\n"
            "• \"В очікуванні\" — заявки співробітників, що потребують рішення.\n"
            "• \"Мої співробітники\" — список команди з контактами та ставками.\n"
            "• \"Переглянути таблицю\" — швидкий перехід до Google Sheets.\n"
            "Кнопки підтвердження/відхилення відкривають форму для коментаря."
        )
        text = base_help
        if employee and employee.is_manager:
            text = f"{base_help}\n{manager_help}"
        await message.answer(
            text,
            reply_markup=employee_menu(employee.is_manager) if employee else share_contact_keyboard(),
        )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
