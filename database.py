import os
import sqlite3
import aiosqlite

from config import DB_PATH, SUBJECTS


class Database:
    def __init__(self):
        self.db: aiosqlite.Connection | None = None

    async def connect(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self.db = await aiosqlite.connect(str(DB_PATH))
        self.db.row_factory = sqlite3.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()
        await self._migrate_tables()
        await self._ensure_subjects()

    async def _create_tables(self):
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS subjects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_id INTEGER NOT NULL REFERENCES subjects(id),
                task_number INTEGER NOT NULL,
                text TEXT NOT NULL,
                image_url TEXT,
                question_image_path TEXT,
                answer TEXT NOT NULL,
                explanation TEXT,
                solution_image_path TEXT,
                source_url TEXT,
                source_id TEXT
            );

            CREATE TABLE IF NOT EXISTS variants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_id INTEGER NOT NULL REFERENCES subjects(id),
                variant_number INTEGER NOT NULL,
                UNIQUE(subject_id, variant_number)
            );

            CREATE TABLE IF NOT EXISTS variant_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                variant_id INTEGER NOT NULL REFERENCES variants(id),
                question_id INTEGER NOT NULL REFERENCES questions(id),
                position INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                registered_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                question_id INTEGER NOT NULL REFERENCES questions(id),
                subject_id INTEGER NOT NULL REFERENCES subjects(id),
                task_number INTEGER NOT NULL,
                user_answer TEXT NOT NULL,
                is_correct INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_questions_subject_task
                ON questions(subject_id, task_number);
            CREATE INDEX IF NOT EXISTS idx_attempts_user_subject
                ON attempts(user_id, subject_id);
            CREATE INDEX IF NOT EXISTS idx_questions_source
                ON questions(subject_id, source_id);
        """)

    async def _migrate_tables(self):
        await self._ensure_column("questions", "question_image_path", "TEXT")
        await self._ensure_column("questions", "solution_image_path", "TEXT")

    async def _ensure_column(self, table_name: str, column_name: str, column_type: str):
        async with self.db.execute(f"PRAGMA table_info({table_name})") as cur:
            rows = await cur.fetchall()
        columns = {row["name"] for row in rows}
        if column_name in columns:
            return

        await self.db.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        )
        await self.db.commit()

    async def _ensure_subjects(self):
        for code, info in SUBJECTS.items():
            await self.db.execute(
                "INSERT OR IGNORE INTO subjects (code, name) VALUES (?, ?)",
                (code, info["name"]),
            )
        await self.db.commit()

    async def close(self):
        if self.db:
            await self.db.close()

    # ── Subjects ──

    async def get_subject_id(self, code: str) -> int | None:
        async with self.db.execute(
            "SELECT id FROM subjects WHERE code = ?", (code,)
        ) as cur:
            row = await cur.fetchone()
            return row["id"] if row else None

    async def get_question_id_by_source(
        self,
        subject_code: str,
        source_id: str,
    ) -> int | None:
        subject_id = await self.get_subject_id(subject_code)
        if subject_id is None:
            return None

        async with self.db.execute(
            "SELECT id FROM questions WHERE subject_id = ? AND source_id = ?",
            (subject_id, source_id),
        ) as cur:
            row = await cur.fetchone()
            return row["id"] if row else None

    async def get_existing_source_ids(
        self,
        subject_code: str,
        task_number: int | None = None,
    ) -> set[str]:
        subject_id = await self.get_subject_id(subject_code)
        if subject_id is None:
            return set()

        if task_number is None:
            query = """
                SELECT source_id FROM questions
                WHERE subject_id = ? AND source_id IS NOT NULL
            """
            params = (subject_id,)
        else:
            query = """
                SELECT source_id FROM questions
                WHERE subject_id = ? AND task_number = ? AND source_id IS NOT NULL
            """
            params = (subject_id, task_number)

        async with self.db.execute(query, params) as cur:
            rows = await cur.fetchall()
        return {row["source_id"] for row in rows if row["source_id"]}

    # ── Users ──

    async def get_or_create_user(
        self, telegram_id: int, username: str = None, first_name: str = None
    ) -> int:
        async with self.db.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return row["id"]

        await self.db.execute(
            "INSERT INTO users (telegram_id, username, first_name) VALUES (?, ?, ?)",
            (telegram_id, username, first_name),
        )
        await self.db.commit()

        async with self.db.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            return row["id"]

    # ── Questions ──

    async def add_question(
        self,
        subject_code: str,
        task_number: int,
        text: str,
        answer: str,
        explanation: str = None,
        image_url: str = None,
        question_image_path: str = None,
        solution_image_path: str = None,
        source_url: str = None,
        source_id: str = None,
    ) -> int | None:
        subject_id = await self.get_subject_id(subject_code)
        if subject_id is None:
            return None

        if source_id:
            async with self.db.execute(
                """SELECT id, text, answer, explanation, image_url,
                          question_image_path, solution_image_path, source_url
                   FROM questions
                   WHERE subject_id = ? AND source_id = ?""",
                (subject_id, source_id),
            ) as cur:
                existing = await cur.fetchone()
                if existing:
                    await self.db.execute(
                        """UPDATE questions
                           SET task_number = ?,
                               text = ?,
                               answer = ?,
                               explanation = ?,
                               image_url = ?,
                               question_image_path = ?,
                               solution_image_path = ?,
                               source_url = ?
                           WHERE id = ?""",
                        (
                            task_number,
                            text or existing["text"],
                            answer or existing["answer"],
                            explanation or existing["explanation"],
                            image_url or existing["image_url"],
                            question_image_path or existing["question_image_path"],
                            solution_image_path or existing["solution_image_path"],
                            source_url or existing["source_url"],
                            existing["id"],
                        ),
                    )
                    await self.db.commit()
                    return existing["id"]

        await self.db.execute(
            """INSERT INTO questions
               (
                   subject_id, task_number, text, answer, explanation, image_url,
                   question_image_path, solution_image_path, source_url, source_id
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                subject_id,
                task_number,
                text,
                answer,
                explanation,
                image_url,
                question_image_path,
                solution_image_path,
                source_url,
                source_id,
            ),
        )
        await self.db.commit()

        async with self.db.execute("SELECT last_insert_rowid()") as cur:
            row = await cur.fetchone()
            return row[0]

    async def get_question_by_id(self, question_id: int) -> dict | None:
        async with self.db.execute(
            """SELECT q.id, q.task_number, q.text, q.image_url,
                      q.question_image_path, q.answer, q.explanation,
                      q.solution_image_path, s.code AS subject_code, s.name AS subject_name
               FROM questions q
               JOIN subjects s ON q.subject_id = s.id
               WHERE q.id = ?""",
            (question_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
            return None

    async def get_random_questions(
        self, subject_code: str, count: int, task_number: int = None
    ) -> list[dict]:
        subject_id = await self.get_subject_id(subject_code)
        if subject_id is None:
            return []

        if task_number:
            query = """SELECT id FROM questions
                       WHERE subject_id = ? AND task_number = ?
                       ORDER BY RANDOM() LIMIT ?"""
            params = (subject_id, task_number, count)
        else:
            query = """SELECT id FROM questions
                       WHERE subject_id = ?
                       ORDER BY RANDOM() LIMIT ?"""
            params = (subject_id, count)

        async with self.db.execute(query, params) as cur:
            rows = await cur.fetchall()

        questions = []
        for row in rows:
            q = await self.get_question_by_id(row["id"])
            if q:
                questions.append(q)
        return questions

    async def get_variant_questions(
        self, subject_code: str, variant_number: int
    ) -> list[dict]:
        """Возвращает вопросы варианта. Сначала ищет готовый вариант в БД,
        при отсутствии генерирует детерминированный вариант из имеющихся вопросов."""
        subject_id = await self.get_subject_id(subject_code)
        if subject_id is None:
            return []

        # Ищем готовый вариант
        async with self.db.execute(
            "SELECT id FROM variants WHERE subject_id = ? AND variant_number = ?",
            (subject_id, variant_number),
        ) as cur:
            variant_row = await cur.fetchone()

        if variant_row:
            async with self.db.execute(
                """SELECT question_id FROM variant_questions
                   WHERE variant_id = ? ORDER BY position""",
                (variant_row["id"],),
            ) as cur:
                rows = await cur.fetchall()

            questions = []
            for row in rows:
                q = await self.get_question_by_id(row["question_id"])
                if q:
                    questions.append(q)
            if questions:
                return questions

        # Детерминированная генерация: по одному вопросу на задание
        task_count = SUBJECTS[subject_code]["task_count"]
        questions = []
        for task_num in range(1, task_count + 1):
            async with self.db.execute(
                """SELECT id FROM questions
                   WHERE subject_id = ? AND task_number = ?
                   ORDER BY id""",
                (subject_id, task_num),
            ) as cur:
                rows = await cur.fetchall()

            if rows:
                idx = (variant_number - 1) % len(rows)
                q = await self.get_question_by_id(rows[idx]["id"])
                if q:
                    questions.append(q)

        return questions

    async def get_available_task_numbers(self, subject_code: str) -> list[int]:
        subject_id = await self.get_subject_id(subject_code)
        if subject_id is None:
            return []

        async with self.db.execute(
            """SELECT DISTINCT task_number FROM questions
               WHERE subject_id = ? ORDER BY task_number""",
            (subject_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [row["task_number"] for row in rows]

    async def get_question_count(
        self, subject_code: str, task_number: int = None
    ) -> int:
        subject_id = await self.get_subject_id(subject_code)
        if subject_id is None:
            return 0

        if task_number:
            query = "SELECT COUNT(*) as cnt FROM questions WHERE subject_id = ? AND task_number = ?"
            params = (subject_id, task_number)
        else:
            query = "SELECT COUNT(*) as cnt FROM questions WHERE subject_id = ?"
            params = (subject_id,)

        async with self.db.execute(query, params) as cur:
            row = await cur.fetchone()
            return row["cnt"]

    # ── Attempts / Statistics ──

    async def add_attempt(
        self,
        telegram_id: int,
        question_id: int,
        user_answer: str,
        is_correct: bool,
    ):
        user_id = await self.get_or_create_user(telegram_id)
        question = await self.get_question_by_id(question_id)
        if not question:
            return

        subject_id = await self.get_subject_id(question["subject_code"])

        await self.db.execute(
            """INSERT INTO attempts
               (user_id, question_id, subject_id, task_number, user_answer, is_correct)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, question_id, subject_id, question["task_number"],
             user_answer, int(is_correct)),
        )
        await self.db.commit()

    async def get_overall_stats(self, telegram_id: int) -> dict:
        user_id = await self.get_or_create_user(telegram_id)

        async with self.db.execute(
            """SELECT COUNT(*) AS total, COALESCE(SUM(is_correct), 0) AS correct
               FROM attempts WHERE user_id = ?""",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
            total = row["total"]
            correct = row["correct"]
            return {"total": total, "correct": correct, "incorrect": total - correct}

    async def get_subject_stats(self, telegram_id: int, subject_code: str) -> dict:
        user_id = await self.get_or_create_user(telegram_id)
        subject_id = await self.get_subject_id(subject_code)

        # Общая по предмету
        async with self.db.execute(
            """SELECT COUNT(*) AS total, COALESCE(SUM(is_correct), 0) AS correct
               FROM attempts WHERE user_id = ? AND subject_id = ?""",
            (user_id, subject_id),
        ) as cur:
            row = await cur.fetchone()
            total = row["total"]
            correct = row["correct"]

        # По заданиям
        async with self.db.execute(
            """SELECT task_number,
                      COUNT(*) AS total,
                      COALESCE(SUM(is_correct), 0) AS correct
               FROM attempts
               WHERE user_id = ? AND subject_id = ?
               GROUP BY task_number
               ORDER BY task_number""",
            (user_id, subject_id),
        ) as cur:
            rows = await cur.fetchall()

        by_task = []
        for r in rows:
            by_task.append({
                "task_number": r["task_number"],
                "total": r["total"],
                "correct": r["correct"],
            })

        return {
            "total": total,
            "correct": correct,
            "incorrect": total - correct,
            "by_task": by_task,
        }

    # ── Variants (для парсера) ──

    async def create_variant(
        self, subject_code: str, variant_number: int, question_ids: list[int]
    ):
        subject_id = await self.get_subject_id(subject_code)
        if subject_id is None:
            return

        await self.db.execute(
            "INSERT OR IGNORE INTO variants (subject_id, variant_number) VALUES (?, ?)",
            (subject_id, variant_number),
        )
        await self.db.commit()

        async with self.db.execute(
            "SELECT id FROM variants WHERE subject_id = ? AND variant_number = ?",
            (subject_id, variant_number),
        ) as cur:
            row = await cur.fetchone()
            variant_id = row["id"]

        # Удаляем старые привязки
        await self.db.execute(
            "DELETE FROM variant_questions WHERE variant_id = ?", (variant_id,)
        )

        for pos, qid in enumerate(question_ids, 1):
            await self.db.execute(
                "INSERT INTO variant_questions (variant_id, question_id, position) VALUES (?, ?, ?)",
                (variant_id, qid, pos),
            )
        await self.db.commit()


db = Database()
