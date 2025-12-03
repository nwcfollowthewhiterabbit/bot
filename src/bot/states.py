from aiogram.fsm.state import State, StatesGroup


class ShiftForm(StatesGroup):
    date = State()
    shift_hours = State()
    overtime_hours = State()
    comment = State()


class ManagerComment(StatesGroup):
    comment = State()


class ShiftEdit(StatesGroup):
    select_shift = State()
    date = State()
    shift_hours = State()
    overtime_hours = State()
    comment = State()
