from aiogram.fsm.state import State, StatesGroup


class TestStates(StatesGroup):
    choosing_subject = State()
    choosing_mode = State()
    entering_variant = State()
    choosing_task_number = State()
    choosing_count = State()
    entering_custom_count = State()
    solving = State()
    showing_result = State()
