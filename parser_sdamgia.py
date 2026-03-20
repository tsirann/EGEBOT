"""
Парсер ЕГЭ с сайта sdamgia.ru
НЕ РАБОТАЕТ!!!
Использование:
    python parser_sdamgia.py --subject russian --tasks 1,2,3 --count 20
    python parser_sdamgia.py --subject math_base --all --count 10
"""

import asyncio
import argparse
import logging
import re

import aiohttp
from bs4 import BeautifulSoup

from config import SUBJECTS
from database import db

logger = logging.getLogger(__name__)

SDAMGIA_BASES = {
    "russian": "https://rus-ege.sdamgia.ru",
    "math_base": "https://mathb-ege.sdamgia.ru",
    "math_profile": "https://math-ege.sdamgia.ru",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}


class SdamgiaParser:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(headers=HEADERS)
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def _fetch(self, url: str) -> str | None:
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning("GET %s → %s", url, resp.status)
                    return None
                return await resp.text()
        except Exception as e:
            logger.error("Ошибка при запросе %s: %s", url, e)
            return None

    # ── Получение ID задач со страницы каталога ──

    async def get_problem_ids(
        self, subject: str, topic_id: int, max_count: int = 50
    ) -> list[int]:
        """Парсит страницу каталога/темы и возвращает ID задач."""
        base = SDAMGIA_BASES[subject]
        url = f"{base}/test?theme={topic_id}"
        html = await self._fetch(url)
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        ids: list[int] = []

        # Ищем ссылки на задачи вида problem?id=XXXXX
        for link in soup.find_all("a", href=True):
            m = re.search(r"problem\?id=(\d+)", link["href"])
            if m:
                pid = int(m.group(1))
                if pid not in ids:
                    ids.append(pid)

        # Также ищем скрытые поля и атрибуты data-*
        for elem in soup.find_all(attrs={"data-id": True}):
            try:
                pid = int(elem["data-id"])
                if pid not in ids:
                    ids.append(pid)
            except (ValueError, KeyError):
                pass

        return ids[:max_count]

    # ── Парсинг одной задачи ──

    async def get_problem(self, subject: str, problem_id: int) -> dict | None:
        """Получает текст задачи, ответ и пояснение по ID."""
        base = SDAMGIA_BASES[subject]
        url = f"{base}/problem?id={problem_id}"
        html = await self._fetch(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")

        # Текст задачи — ищем в нескольких возможных контейнерах
        text_elem = (
            soup.find("div", class_="pbody")
            or soup.find("div", class_="condition")
            or soup.find("div", class_="problem_text")
        )

        if not text_elem:
            # Пробуем найти по структуре страницы
            content = soup.find("div", id="content")
            if content:
                nomarks = content.find_all("div", class_="nomark")
                text_elem = nomarks[0] if nomarks else None

        if not text_elem:
            logger.debug("Не найден текст задачи %s", problem_id)
            return None

        text = text_elem.get_text(separator="\n", strip=True)
        if len(text) < 10:
            return None

        # Ответ
        answer = self._extract_answer(soup)
        if not answer:
            logger.debug("Не найден ответ для задачи %s", problem_id)
            return None

        # Номер задания
        task_number = self._extract_task_number(soup)

        # Пояснение (укороченное)
        explanation = None
        sol = soup.find("div", class_="solution")
        if sol:
            explanation = sol.get_text(separator="\n", strip=True)[:500]

        # Картинки
        images = []
        if text_elem:
            for img in text_elem.find_all("img"):
                src = img.get("src", "")
                if src and not src.startswith("http"):
                    src = base + ("" if src.startswith("/") else "/") + src
                if src:
                    images.append(src)

        return {
            "id": problem_id,
            "text": text,
            "answer": answer,
            "task_number": task_number,
            "explanation": explanation,
            "images": images,
            "source_url": url,
        }

    def _extract_answer(self, soup: BeautifulSoup) -> str | None:
        # Способ 1: блок с классом answer
        ans_div = soup.find("div", class_="answer")
        if ans_div:
            raw = ans_div.get_text(strip=True)
            raw = re.sub(r"^[Оо]твет\s*[:：.\s]*", "", raw).strip()
            raw = raw.rstrip(".")
            if raw:
                return raw

        # Способ 2: ищем «Ответ: ...» в тексте решения
        for block in soup.find_all(["div", "p", "span"]):
            m = re.search(r"[Оо]твет\s*[:：]\s*(.+?)(?:\.|<br|$)", block.get_text())
            if m:
                return m.group(1).strip()

        return None

    def _extract_task_number(self, soup: BeautifulSoup) -> int | None:
        for elem in soup.find_all(["span", "div", "a", "td"]):
            txt = elem.get_text()
            m = re.search(r"(?:Задани[ея]|Задача|Номер|Тип)\s*(\d+)", txt)
            if m:
                num = int(m.group(1))
                if 1 <= num <= 30:
                    return num
        return None

    # ── Массовый парсинг ──

    async def parse_subject(
        self,
        subject: str,
        task_numbers: list[int] | None = None,
        per_task: int = 10,
        delay: float = 0.5,
    ) -> list[dict]:
        """Парсит задачи для предмета.

        Args:
            subject: код предмета (russian / math_base / math_profile)
            task_numbers: список номеров заданий (None = все)
            per_task: сколько задач на каждый номер
            delay: задержка между запросами (секунды)
        """
        info = SUBJECTS[subject]
        if task_numbers is None:
            task_numbers = list(range(1, info["task_count"] + 1))

        all_problems: list[dict] = []

        for task_num in task_numbers:
            logger.info("Парсинг %s, задание №%s ...", subject, task_num)

            ids = await self.get_problem_ids(subject, task_num, max_count=per_task * 3)
            if not ids:
                logger.warning("  Не найдены ID задач для темы %s", task_num)
                continue

            count = 0
            for pid in ids:
                if count >= per_task:
                    break

                problem = await self.get_problem(subject, pid)
                if problem and problem["answer"]:
                    if problem["task_number"] is None:
                        problem["task_number"] = task_num
                    all_problems.append(problem)
                    count += 1

                await asyncio.sleep(delay)

            logger.info("  Получено %s задач", count)

        return all_problems


async def save_problems(problems: list[dict], subject: str):
    """Сохраняет распарсенные задачи в БД."""
    saved = 0
    for p in problems:
        qid = await db.add_question(
            subject_code=subject,
            task_number=p["task_number"],
            text=p["text"],
            answer=p["answer"],
            explanation=p.get("explanation"),
            image_url=p["images"][0] if p.get("images") else None,
            source_url=p.get("source_url"),
            source_id=f"sdamgia_{p['id']}",
        )
        if qid:
            saved += 1
    return saved


async def main():
    parser = argparse.ArgumentParser(description="Парсер заданий ЕГЭ с sdamgia.ru")
    parser.add_argument(
        "--subject",
        required=True,
        choices=list(SUBJECTS.keys()),
        help="Предмет: russian, math_base, math_profile",
    )
    parser.add_argument(
        "--tasks",
        default=None,
        help="Номера заданий через запятую (по умолчанию: все)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Количество задач на каждый номер (по умолчанию: 10)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="parse_all",
        help="Парсить все предметы",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Задержка между запросами в секундах",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    await db.connect()

    subjects_to_parse = list(SUBJECTS.keys()) if args.parse_all else [args.subject]

    for subj in subjects_to_parse:
        task_numbers = None
        if args.tasks and not args.parse_all:
            task_numbers = [int(x.strip()) for x in args.tasks.split(",")]

        async with SdamgiaParser() as parser_inst:
            problems = await parser_inst.parse_subject(
                subj,
                task_numbers=task_numbers,
                per_task=args.count,
                delay=args.delay,
            )

        saved = await save_problems(problems, subj)
        logger.info(
            "Предмет %s: найдено %s задач, сохранено %s",
            subj, len(problems), saved,
        )

    await db.close()
    logger.info("Готово!")


if __name__ == "__main__":
    asyncio.run(main())
