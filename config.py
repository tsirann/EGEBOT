import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Не задан BOT_TOKEN в файле .env")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "ege_bot.db"

SUBJECTS = {
    "russian": {
        "name": "Русский язык",
        "emoji": "📝",
        "task_count": 26,
        "sdamgia": "rus-ege",
    },
    "math_base": {
        "name": "Базовая математика",
        "emoji": "📐",
        "task_count": 21,
        "sdamgia": "mathb-ege",
    },
    "math_profile": {
        "name": "Профильная математика",
        "emoji": "📊",
        "task_count": 12,
        "sdamgia": "math-ege",
    },
}

MAX_VARIANTS = 20
MAX_CUSTOM_QUESTIONS = 50
