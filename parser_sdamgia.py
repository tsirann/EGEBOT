"""
Парсер заданий СдамГИА через sdamgia-api.

Примеры:
    python parser_sdamgia.py --subject russian --tasks 1,2,3 --count 5
    python parser_sdamgia.py --subject math_profile --query "Найдите значение выражения" --count 3
    python parser_sdamgia.py --all --count 2 --skip-screenshots
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sdamgia import SdamGIA

from config import SUBJECTS
from database import db
from shot_task.reshu_screenshot import capture_screenshot

logger = logging.getLogger(__name__)

SDAMGIA_SUBJECTS = {
    "russian": "rus",
    "math_base": "mathb",
    "math_profile": "math",
}


@dataclass(slots=True)
class ScreenshotOptions:
    enabled: bool = True
    headless: bool = False
    background: bool = False
    timeout: int = 45
    wait_ms: int = 400
    profile_dir: Path | None = None


class SdamgiaParser:
    def __init__(
        self,
        *,
        screenshot_options: ScreenshotOptions,
        category_pages: int = 5,
        search_pages: int = 3,
        screenshot_root: Path | None = None,
    ):
        self.api = SdamGIA()
        self.screenshot_options = screenshot_options
        self.category_pages = max(1, category_pages)
        self.search_pages = max(1, search_pages)
        self.screenshot_root = (screenshot_root or (Path("data") / "screenshots")).resolve()
        self._catalog_cache: dict[str, dict[int, list[str]]] = {}

    async def _call_api(self, method_name: str, *args, **kwargs):
        def runner():
            method = getattr(self.api, method_name)
            with contextlib.redirect_stdout(io.StringIO()):
                return method(*args, **kwargs)

        return await asyncio.to_thread(runner)

    async def _capture_problem_screenshots(
        self,
        url: str,
        subject: str,
        task_number: int | None,
        problem_id: str,
    ) -> tuple[str | None, str | None]:
        if not self.screenshot_options.enabled:
            return None, None

        task_dir = f"task_{task_number}" if task_number is not None else "task_unknown"
        base_dir = (self.screenshot_root / subject / task_dir)
        base_dir.mkdir(parents=True, exist_ok=True)

        condition_path = (base_dir / f"{problem_id}_condition.png").resolve()
        solution_path = (base_dir / f"{problem_id}_solution.png").resolve()

        def capture(mode: str, output_path: Path) -> str | None:
            if output_path.exists():
                return str(output_path)

            try:
                capture_screenshot(
                    url,
                    output_path=output_path,
                    capture_mode=mode,  # type: ignore[arg-type]
                    headless=self.screenshot_options.headless,
                    background=self.screenshot_options.background,
                    timeout=self.screenshot_options.timeout,
                    wait_ms=self.screenshot_options.wait_ms,
                    profile_dir=self.screenshot_options.profile_dir,
                )
                return str(output_path)
            except Exception as exc:
                logger.warning("Не удалось сделать %s-скриншот для %s: %s", mode, url, exc)
                return None

        condition_result = await asyncio.to_thread(capture, "condition", condition_path)
        solution_result = await asyncio.to_thread(capture, "solution", solution_path)
        return condition_result, solution_result

    async def _get_catalog_map(self, subject: str) -> dict[int, list[str]]:
        if subject in self._catalog_cache:
            return self._catalog_cache[subject]

        raw_subject = SDAMGIA_SUBJECTS[subject]
        catalog = await self._call_api("get_catalog", raw_subject)
        task_map: dict[int, list[str]] = {}

        for topic in catalog:
            topic_id = self._parse_task_number(topic.get("topic_id"))
            if topic_id is None:
                continue
            task_map[topic_id] = [item["category_id"] for item in topic.get("categories", [])]

        self._catalog_cache[subject] = task_map
        return task_map

    async def get_random_problem_ids_for_task(
        self,
        subject: str,
        task_number: int,
        max_count: int,
        *,
        existing_problem_ids: set[str] | None = None,
    ) -> list[str]:
        raw_subject = SDAMGIA_SUBJECTS[subject]
        collected: list[str] = []
        seen_ids = set(existing_problem_ids or set())
        generated_ids: set[str] = set()
        max_attempts = max(max_count * 12, 30)

        for _ in range(max_attempts):
            try:
                test_id = await self._call_api("generate_test", raw_subject, {task_number: 1})
                ids = await self._call_api("get_test_by_id", raw_subject, test_id)
            except Exception as exc:
                logger.warning(
                    "Не удалось получить случайную задачу для %s №%s: %s",
                    subject,
                    task_number,
                    exc,
                )
                continue

            if not ids:
                continue

            for problem_id in ids:
                if problem_id in seen_ids or problem_id in generated_ids:
                    continue
                generated_ids.add(problem_id)
                collected.append(problem_id)
                if len(collected) >= max_count:
                    return collected

        return collected

    async def get_problem_ids_for_task(
        self,
        subject: str,
        task_number: int,
        max_count: int,
    ) -> list[str]:
        task_map = await self._get_catalog_map(subject)
        category_ids = task_map.get(task_number, [])
        if not category_ids:
            logger.warning("Для задания %s не нашли категорий в каталоге", task_number)
            return []

        raw_subject = SDAMGIA_SUBJECTS[subject]
        unique_ids: list[str] = []
        seen_ids: set[str] = set()

        for category_id in category_ids:
            for page in range(1, self.category_pages + 1):
                ids = await self._call_api("get_category_by_id", raw_subject, category_id, page)
                if not ids:
                    break

                page_added = 0
                for problem_id in ids:
                    if problem_id in seen_ids:
                        continue
                    seen_ids.add(problem_id)
                    unique_ids.append(problem_id)
                    page_added += 1
                    if len(unique_ids) >= max_count:
                        return unique_ids

                if page_added == 0:
                    break

        return unique_ids[:max_count]

    async def search_problem_ids(
        self,
        subject: str,
        query: str,
        max_count: int,
    ) -> list[str]:
        raw_subject = SDAMGIA_SUBJECTS[subject]
        unique_ids: list[str] = []
        seen_ids: set[str] = set()

        for page in range(1, self.search_pages + 1):
            ids = await self._call_api("search", raw_subject, query, page)
            if not ids:
                break

            page_added = 0
            for problem_id in ids:
                if problem_id in seen_ids:
                    continue
                seen_ids.add(problem_id)
                unique_ids.append(problem_id)
                page_added += 1
                if len(unique_ids) >= max_count:
                    return unique_ids

            if page_added == 0:
                break

        return unique_ids[:max_count]

    async def get_problem(self, subject: str, problem_id: str) -> dict[str, Any] | None:
        raw_subject = SDAMGIA_SUBJECTS[subject]
        payload = await self._call_api("get_problem_by_id", raw_subject, problem_id)
        if not payload:
            return None

        condition = payload.get("condition") or {}
        solution = payload.get("solution") or {}
        answer = (payload.get("answer") or "").strip()
        source_url = payload.get("url")
        task_number = self._parse_task_number(payload.get("topic"))

        if not answer or not source_url or task_number is None:
            logger.warning(
                "Пропускаю задачу %s: не хватает данных (answer/url/task_number)",
                problem_id,
            )
            return None

        question_image_path, solution_image_path = await self._capture_problem_screenshots(
            source_url,
            subject,
            task_number,
            str(problem_id),
        )

        return {
            "id": str(problem_id),
            "task_number": task_number,
            "text": (condition.get("text") or "").strip(),
            "answer": answer,
            "explanation": (solution.get("text") or "").strip() or None,
            "condition_images": condition.get("images") or [],
            "solution_images": solution.get("images") or [],
            "question_image_path": question_image_path,
            "solution_image_path": solution_image_path,
            "source_url": source_url,
        }

    @staticmethod
    def _parse_task_number(raw_value: Any) -> int | None:
        if raw_value is None:
            return None
        match = re.search(r"\d+", str(raw_value))
        if not match:
            return None
        return int(match.group())

    async def parse_subject(
        self,
        subject: str,
        *,
        task_numbers: list[int] | None = None,
        query: str | None = None,
        per_task: int = 10,
        delay: float = 0.5,
        skip_existing: bool = True,
    ) -> list[dict[str, Any]]:
        all_problems: list[dict[str, Any]] = []

        if query:
            logger.info("Поиск задач по запросу %r для %s", query, subject)
            existing_source_ids = (
                await db.get_existing_source_ids(subject) if skip_existing else set()
            )
            ids = await self.search_problem_ids(subject, query, max_count=per_task)
            for index, problem_id in enumerate(ids, start=1):
                source_id = f"sdamgia_{problem_id}"
                if source_id in existing_source_ids:
                    logger.info("  [%s/%s] Пропускаю задачу %s: уже есть в БД", index, len(ids), problem_id)
                    continue
                logger.info("  [%s/%s] Загружаю задачу %s", index, len(ids), problem_id)
                problem = await self.get_problem(subject, problem_id)
                if problem:
                    all_problems.append(problem)
                await asyncio.sleep(delay)
            return all_problems

        info = SUBJECTS[subject]
        if task_numbers is None:
            task_numbers = list(range(1, info["task_count"] + 1))

        for task_number in task_numbers:
            logger.info("Парсинг %s, задание №%s ...", subject, task_number)
            existing_source_ids = (
                await db.get_existing_source_ids(subject, task_number) if skip_existing else set()
            )
            existing_problem_ids = {
                source_id.removeprefix("sdamgia_")
                for source_id in existing_source_ids
                if source_id.startswith("sdamgia_")
            }
            ids = await self.get_random_problem_ids_for_task(
                subject,
                task_number,
                max_count=per_task * 4,
                existing_problem_ids=existing_problem_ids,
            )
            if len(ids) < per_task:
                fallback_ids = await self.get_problem_ids_for_task(
                    subject,
                    task_number,
                    max_count=per_task * 20,
                )
                random_id_set = set(ids)
                ids.extend(problem_id for problem_id in fallback_ids if problem_id not in random_id_set)
            if not ids:
                logger.warning("  Не нашли задач для задания №%s", task_number)
                continue

            saved_for_task = 0
            for problem_id in ids:
                if saved_for_task >= per_task:
                    break

                source_id = f"sdamgia_{problem_id}"
                if source_id in existing_source_ids:
                    logger.info("  Пропускаю задачу %s: уже есть в БД", problem_id)
                    continue

                problem = await self.get_problem(subject, problem_id)
                if not problem:
                    await asyncio.sleep(delay)
                    continue

                actual_task_number = problem.get("task_number")
                if actual_task_number is not None and actual_task_number != task_number:
                    logger.info(
                        "  Пропускаю задачу %s: API пометил ее как задание №%s, а не №%s",
                        problem_id,
                        actual_task_number,
                        task_number,
                    )
                    await asyncio.sleep(delay)
                    continue

                problem["task_number"] = task_number
                all_problems.append(problem)
                saved_for_task += 1
                existing_source_ids.add(source_id)
                await asyncio.sleep(delay)

            logger.info("  Получено %s задач", saved_for_task)

        return all_problems


async def save_problems(problems: list[dict[str, Any]], subject: str) -> dict[str, int]:
    stats = {
        "added": 0,
        "updated": 0,
    }
    for problem in problems:
        source_id = f"sdamgia_{problem['id']}"
        existing_id = await db.get_question_id_by_source(subject, source_id)
        question_id = await db.add_question(
            subject_code=subject,
            task_number=problem["task_number"],
            text=problem["text"] or f"Задача СдамГИА {problem['id']}",
            answer=problem["answer"],
            explanation=problem.get("explanation"),
            image_url=(problem.get("condition_images") or [None])[0],
            question_image_path=problem.get("question_image_path"),
            solution_image_path=problem.get("solution_image_path"),
            source_url=problem.get("source_url"),
            source_id=source_id,
        )
        if question_id:
            if existing_id is None:
                stats["added"] += 1
            else:
                stats["updated"] += 1
    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Парсер заданий ЕГЭ через sdamgia-api")
    parser.add_argument(
        "--subject",
        choices=list(SUBJECTS.keys()),
        help="Предмет: russian, math_base, math_profile",
    )
    parser.add_argument(
        "--tasks",
        default=None,
        help="Номера заданий через запятую (по умолчанию: все)",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Текстовый поисковый запрос по базе СдамГИА",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Количество задач на каждый номер или по поисковому запросу",
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
    parser.add_argument(
        "--category-pages",
        type=int,
        default=5,
        help="Сколько страниц категории просматривать при сборе по номеру задания",
    )
    parser.add_argument(
        "--search-pages",
        type=int,
        default=3,
        help="Сколько страниц поисковой выдачи просматривать при --query",
    )
    parser.add_argument(
        "--skip-screenshots",
        action="store_true",
        help="Не делать скриншоты условия и решения",
    )
    parser.add_argument(
        "--allow-existing",
        action="store_true",
        help="Не пропускать задачи, которые уже есть в БД",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Делать скриншоты в headless-режиме",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Открывать браузер для скриншотов вне экрана",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="Таймаут загрузки страницы для скриншота",
    )
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=400,
        help="Сколько миллисекунд ждать перед сохранением скриншота",
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    if not args.parse_all and not args.subject:
        raise SystemExit("Нужно указать --subject или использовать --all")
    if args.parse_all and args.query:
        raise SystemExit("Режим --all нельзя комбинировать с --query")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    await db.connect()

    subjects_to_parse = list(SUBJECTS.keys()) if args.parse_all else [args.subject]
    screenshot_options = ScreenshotOptions(
        enabled=not args.skip_screenshots,
        headless=args.headless,
        background=args.background,
        timeout=args.timeout,
        wait_ms=args.wait_ms,
    )
    parser = SdamgiaParser(
        screenshot_options=screenshot_options,
        category_pages=args.category_pages,
        search_pages=args.search_pages,
    )

    for subject in subjects_to_parse:
        task_numbers = None
        if args.tasks and not args.query:
            task_numbers = [int(item.strip()) for item in args.tasks.split(",") if item.strip()]

        problems = await parser.parse_subject(
            subject,
            task_numbers=task_numbers,
            query=args.query,
            per_task=args.count,
            delay=args.delay,
            skip_existing=not args.allow_existing,
        )
        save_stats = await save_problems(problems, subject)
        logger.info(
            "Предмет %s: найдено %s задач, добавлено %s, обновлено %s",
            subject,
            len(problems),
            save_stats["added"],
            save_stats["updated"],
        )

    await db.close()
    logger.info("Готово!")


if __name__ == "__main__":
    asyncio.run(main())
