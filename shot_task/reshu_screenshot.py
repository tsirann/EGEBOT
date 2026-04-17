from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, sync_playwright


CaptureMode = Literal["task", "condition", "solution", "full-page"]

BROWSER_CANDIDATES = [
    ("Chrome", Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")),
    ("Edge", Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")),
    ("Edge", Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")),
]

BACKGROUND_BROWSER_ARGS = [
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]

BLOCKED_RESOURCE_TYPES = {
    "media",
    "websocket",
    "eventsource",
}

BLOCKED_URL_KEYWORDS = (
    "doubleclick",
    "googlesyndication",
    "googletagmanager",
    "google-analytics",
    "adservice",
    "adfox",
    "mc.yandex",
    "metrika",
    "top.mail.ru",
)

DEFAULT_TASK_SELECTORS = [
    ".prob_maindiv",
    "[class*='prob_maindiv']",
    ".problem_container",
    ".problem",
    ".task_container",
    ".task",
    "main",
    "article",
]

CONDITION_CONTENT_SELECTORS = [
    ".pbody",
    "[id^='body'].pbody",
    "[id^='body']",
    ".problemtext",
    ".problem_text",
    ".tasktext",
    ".task_text",
    ".problem-statement",
    ".problem_statement",
    ".question",
]

SOLUTION_CONTENT_SELECTORS = [
    ".solution .pbody",
    ".solution",
    ".solutions .pbody",
    ".solutions",
    "[id^='sol']",
    "[class*='solution']",
    "[class*='sol']",
]

STRIP_SELECTORS = [
    "button",
    "a.button",
    "a.btn",
    "a[role='button']",
    "form",
    "input",
    "textarea",
    "select",
    "nav",
    "footer",
    ".comment",
    ".comments",
    ".comment-form",
    ".social",
    ".share",
    ".like",
    ".likes",
    ".dislike",
    ".toolbar",
    ".tools",
    ".actions",
    ".controls",
    ".buttons",
    ".btn",
    ".menu",
    ".breadcrumbs",
    ".pagination",
    ".pager",
    ".report",
    ".complaint",
    ".advert",
    ".ads",
    ".ad",
    ".banner",
    ".sticky",
    ".modal",
    ".popup",
    ".spoiler",
    ".spoilers",
    ".toggle",
    ".dropdown",
    ".tabs",
    ".prob_nums",
    ".prob_nav",
    ".prob_links",
    ".prob_buttons",
    ".prob_actions",
    ".probmeta",
    ".prob_meta",
    ".problem-meta",
    ".problem_meta",
    ".task-meta",
    ".task_meta",
    ".meta",
    ".metadata",
    ".tags",
    ".taglist",
    ".tags_list",
    ".difficulty",
    ".complexity",
    ".actuality",
    ".relevance",
    ".codifier",
    ".codificator",
    ".fipi",
    ".icon",
    ".icons",
    ".top-icons",
    ".top_icons",
    ".panel",
    ".properties",
    ".props",
    ".minor",
]

CONDITION_STRIP_SELECTORS = [
    *STRIP_SELECTORS,
    ".answer",
    ".answers",
    ".solution",
    ".solutions",
    ".solve",
]

SOLUTION_STRIP_SELECTORS = [
    *STRIP_SELECTORS,
    ".answer",
    ".answers",
]

GUARD_MARKERS = (
    "ddos-guard",
    "проверка браузера",
    "пройдите ручную проверку",
    "не удалось проверить ваш браузер автоматически",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Открывает ссылку на задание Решу ЕГЭ и сохраняет скриншот."
    )
    parser.add_argument("url", help="Ссылка на задание или тест на ege.sdamgia.ru")
    parser.add_argument(
        "-o",
        "--output",
        help="Куда сохранить PNG. По умолчанию создается файл в папке screenshots/",
    )
    parser.add_argument(
        "--selector",
        help="Свой CSS-селектор блока, который нужно снять вместо автоопределения.",
    )
    parser.add_argument(
        "--full-page",
        action="store_true",
        help="Снимать всю страницу целиком, без поиска блока задания.",
    )
    parser.add_argument(
        "--condition-only",
        action="store_true",
        help="Оставить только условие задачи и картинки.",
    )
    parser.add_argument(
        "--solution-only",
        action="store_true",
        help="Оставить только решение задачи и картинки.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Запускать браузер без окна. Для Решу ЕГЭ обычно лучше не использовать.",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="Запускать обычный браузер вне экрана, чтобы он не мешал на рабочем столе.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=45,
        help="Максимум секунд на загрузку страницы. По умолчанию 45.",
    )
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=400,
        help="Сколько миллисекунд подождать после загрузки перед скриншотом. По умолчанию 400.",
    )
    return parser.parse_args()


def normalize_url(raw_url: str) -> str:
    url = raw_url.strip()
    if not url:
        raise ValueError("Пустая ссылка.")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    return url


def find_browser() -> tuple[str, Path]:
    for browser_name, browser_path in BROWSER_CANDIDATES:
        if browser_path.exists():
            return browser_name, browser_path
    searched = "\n".join(f"- {path}" for _, path in BROWSER_CANDIDATES)
    raise FileNotFoundError(
        "Не нашел установленный Chrome/Edge.\n"
        "Проверьте, что браузер установлен, или поправьте BROWSER_CANDIDATES в скрипте.\n"
        f"Проверял пути:\n{searched}"
    )


def build_output_path(url: str, output_arg: str | None, suffix: str | None = None) -> Path:
    if output_arg:
        output_path = Path(output_arg).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return output_path

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    task_id = query.get("id", [None])[0]

    slug_parts = [parsed.netloc or "task"]
    path_slug = re.sub(r"[^a-zA-Z0-9]+", "_", parsed.path).strip("_")
    if path_slug:
        slug_parts.append(path_slug)
    if task_id:
        slug_parts.append(task_id)
    if suffix:
        slug_parts.append(suffix)

    slug = "_".join(slug_parts) or "reshu_task"
    slug = re.sub(r"_+", "_", slug).strip("_").lower()

    output_dir = Path.cwd() / "screenshots"
    output_dir.mkdir(parents=True, exist_ok=True)
    return (output_dir / f"{slug}.png").resolve()


def mode_output_suffix(capture_mode: CaptureMode) -> str | None:
    if capture_mode == "condition":
        return "condition"
    if capture_mode == "solution":
        return "solution"
    if capture_mode == "full-page":
        return "full"
    return None


def is_guard_page(page: Page) -> bool:
    try:
        title = page.title().lower()
    except PlaywrightError:
        title = ""

    try:
        body_text = page.locator("body").inner_text(timeout=3000).lower()
    except PlaywrightError:
        body_text = ""

    haystack = f"{title}\n{body_text}"
    return any(marker in haystack for marker in GUARD_MARKERS)


def wait_for_manual_check(page: Page, headless: bool) -> None:
    if not is_guard_page(page):
        return

    if headless:
        raise RuntimeError(
            "Сайт показал страницу DDOS-GUARD в headless-режиме. Запустите без --headless."
        )

    print(
        "\nНа странице появилась проверка DDOS-GUARD.\n"
        "Пройдите ее в открывшемся окне браузера. Когда увидите само задание, нажмите Enter здесь."
    )

    while True:
        input()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except PlaywrightError:
            pass
        page.wait_for_timeout(1500)
        if not is_guard_page(page):
            return
        print("Пока еще вижу страницу проверки. Завершите ее в браузере и нажмите Enter еще раз.")


def wait_until_ready(page: Page, locator: Locator | None, wait_ms: int) -> None:
    try:
        page.wait_for_function(
            "() => document.readyState === 'interactive' || document.readyState === 'complete'",
            timeout=15000,
        )
    except PlaywrightError:
        pass

    if locator is not None:
        try:
            locator.evaluate(
                """
                async (node) => {
                    const images = Array.from(node.querySelectorAll('img, svg, canvas'));
                    await Promise.all(images.map((img) => {
                        if (!('complete' in img) || img.complete) {
                            return Promise.resolve();
                        }
                        return new Promise((resolve) => {
                            img.addEventListener('load', resolve, { once: true });
                            img.addEventListener('error', resolve, { once: true });
                        });
                    }));
                }
                """
            )
        except PlaywrightError:
            pass

    page.wait_for_timeout(wait_ms)


def should_block_request(url: str, resource_type: str) -> bool:
    if resource_type in BLOCKED_RESOURCE_TYPES:
        return True

    lowered_url = url.lower()
    return any(keyword in lowered_url for keyword in BLOCKED_URL_KEYWORDS)


def install_fast_routes(context: BrowserContext) -> None:
    def handle_route(route) -> None:
        request = route.request
        if should_block_request(request.url, request.resource_type):
            route.abort()
            return
        route.continue_()

    context.route("**/*", handle_route)


def prepare_page(page: Page) -> None:
    page.evaluate(
        """
        () => {
            const exactSelectors = [
                'header',
                'footer',
                '.adsbygoogle',
                '.adfox',
                '.cookie',
                '.cookies',
                '.cookie-banner',
                '.banner',
                '.popup',
                '.modal',
                '.sticky',
                '.topbn',
                '.botbn',
                '.leftbn',
                '.rightbn',
                '.ads',
                '.ad',
                'iframe[src*="ads"]',
                'iframe[src*="doubleclick"]'
            ];

            for (const selector of exactSelectors) {
                for (const node of document.querySelectorAll(selector)) {
                    node.remove();
                }
            }

            document.body.style.background = '#ffffff';
            window.scrollTo(0, 0);
        }
        """
    )


def find_task_locator(page: Page, custom_selector: str | None) -> tuple[Locator | None, str | None]:
    selectors: list[str] = []
    if custom_selector:
        selectors.append(custom_selector)
    selectors.extend(DEFAULT_TASK_SELECTORS)

    seen: set[str] = set()
    for selector in selectors:
        if selector in seen:
            continue
        seen.add(selector)

        locator = page.locator(selector)
        try:
            count = locator.count()
        except PlaywrightError:
            continue

        for index in range(min(count, 6)):
            candidate = locator.nth(index)
            try:
                if not candidate.is_visible(timeout=1000):
                    continue
                box = candidate.bounding_box()
            except PlaywrightError:
                continue

            if not box:
                continue

            width = box.get("width", 0)
            height = box.get("height", 0)
            if width < 360 or height < 120:
                continue
            if height > 5000:
                continue

            return candidate, selector

    return None, None


def _pick_content_locator(
    root_locator: Locator,
    selectors: list[str],
    min_text_length: int,
) -> tuple[Locator | None, str | None]:
    seen: set[str] = set()

    for selector in selectors:
        if selector in seen:
            continue
        seen.add(selector)

        locator = root_locator.locator(selector)
        try:
            count = locator.count()
        except PlaywrightError:
            continue

        for index in range(min(count, 8)):
            candidate = locator.nth(index)
            try:
                if not candidate.is_visible(timeout=1000):
                    continue
                box = candidate.bounding_box()
                payload = candidate.evaluate(
                    """
                    (node) => ({
                        textLength: (node.innerText || '').replace(/\\s+/g, ' ').trim().length,
                        images: node.querySelectorAll('img, svg, canvas').length
                    })
                    """
                )
            except PlaywrightError:
                continue

            if not box:
                continue

            width = box.get("width", 0)
            height = box.get("height", 0)
            text_length = payload.get("textLength", 0)
            image_count = payload.get("images", 0)

            if width < 200 or height < 10:
                continue
            if text_length < min_text_length and image_count == 0:
                continue

            return candidate, selector

    return None, None


def find_condition_locator(task_locator: Locator) -> tuple[Locator | None, str | None]:
    return _pick_content_locator(task_locator, CONDITION_CONTENT_SELECTORS, min_text_length=20)


def find_solution_locator(task_locator: Locator) -> tuple[Locator | None, str | None]:
    locator, selector = _pick_content_locator(
        task_locator,
        SOLUTION_CONTENT_SELECTORS,
        min_text_length=15,
    )
    if locator is not None:
        return locator, selector

    fallback = task_locator.locator(".pbody")
    try:
        if fallback.count() >= 2:
            candidate = fallback.nth(1)
            if candidate.is_visible(timeout=1000):
                return candidate, ".pbody:nth(1)"
    except PlaywrightError:
        pass

    return None, None


def isolate_content(page: Page, locator: Locator, root_id: str, strip_selectors: list[str]) -> Locator:
    locator.evaluate(
        """
        (node, config) => {
            const { rootId, shotId, stripSelectors } = config;
            const removeByTextPatterns = [
                'актуальность',
                'сложность',
                'уровень сложности',
                'раздел кодификатора',
                'кодификатор фипи',
                'раздел фипи',
                'пояснение',
                'показать пояснение',
                'спрятать пояснение',
                'показать решение',
                'спрятать решение',
                'скрыть решение',
                'решение скрыто',
                'подсказка',
            ];
            const oldRoot = document.getElementById(rootId);
            if (oldRoot) {
                oldRoot.remove();
            }

            const clone = node.cloneNode(true);

            for (const selector of stripSelectors) {
                for (const match of clone.querySelectorAll(selector)) {
                    match.remove();
                }
            }

            for (const element of clone.querySelectorAll('script, style, noscript, iframe')) {
                element.remove();
            }

            const normalizedText = (value) => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            const hasTaskMedia = (element) => element.querySelector('img, svg, canvas') !== null;
            const maybeRemove = new Set();

            for (const element of clone.querySelectorAll('*')) {
                const text = normalizedText(element.innerText);
                if (!text) {
                    continue;
                }

                const textLength = text.length;
                const linkCount = element.querySelectorAll('a').length;
                const buttonish =
                    element.matches('a, button, summary') ||
                    element.getAttribute('role') === 'button';

                if (
                    removeByTextPatterns.some((pattern) => text.startsWith(pattern)) &&
                    textLength <= 240 &&
                    !hasTaskMedia(element)
                ) {
                    maybeRemove.add(element);
                    continue;
                }

                if (
                    buttonish &&
                    removeByTextPatterns.some((pattern) => text.includes(pattern)) &&
                    textLength <= 240
                ) {
                    maybeRemove.add(element);
                    continue;
                }

                if (
                    linkCount >= 2 &&
                    textLength <= 120 &&
                    !hasTaskMedia(element)
                ) {
                    maybeRemove.add(element);
                }
            }

            for (const element of Array.from(maybeRemove)) {
                if (element.isConnected) {
                    element.remove();
                }
            }

            const root = document.createElement('div');
            root.id = rootId;
            root.style.position = 'absolute';
            root.style.left = '0';
            root.style.top = '0';
            root.style.width = '100%';
            root.style.padding = '24px';
            root.style.background = '#ffffff';
            root.style.boxSizing = 'border-box';
            root.style.zIndex = '2147483647';

            const container = document.createElement('div');
            container.id = shotId;
            container.style.maxWidth = '1200px';
            container.style.margin = '0 auto';
            container.style.background = '#ffffff';
            container.style.boxSizing = 'border-box';
            container.style.padding = '12px 18px';

            container.appendChild(clone);
            root.appendChild(container);

            for (const child of Array.from(document.body.children)) {
                child.style.display = 'none';
            }

            clone.style.background = '#ffffff';
            clone.style.width = '100%';
            clone.style.maxWidth = '100%';
            clone.style.boxSizing = 'border-box';
            clone.style.display = 'flow-root';
            clone.style.overflow = 'hidden';

            for (const element of clone.querySelectorAll('*')) {
                element.style.boxShadow = 'none';
            }

            for (const block of clone.querySelectorAll('.left_margin')) {
                block.style.marginLeft = '0';
            }

            for (const image of clone.querySelectorAll('img, svg, canvas')) {
                image.style.maxWidth = '100%';
            }

            document.body.style.background = '#ffffff';
            document.body.appendChild(root);
            window.scrollTo(0, 0);
        }
        """,
        {
            "rootId": root_id,
            "shotId": f"{root_id}-content",
            "stripSelectors": strip_selectors,
        },
    )
    page.wait_for_timeout(400)
    return page.locator(f"#{root_id}-content")


def take_screenshot(
    page: Page,
    output_path: Path,
    capture_mode: CaptureMode,
    selector: str | None,
    wait_ms: int,
) -> str:
    if capture_mode == "full-page":
        wait_until_ready(page, None, wait_ms)
        page.screenshot(path=str(output_path), full_page=True)
        return "full-page"

    locator, used_selector = find_task_locator(page, selector)
    if locator is None:
        wait_until_ready(page, None, wait_ms)
        page.screenshot(path=str(output_path), full_page=True)
        return "fallback-full-page"

    if capture_mode == "condition":
        content_locator, content_selector = find_condition_locator(locator)
        # For EGE tasks with a source text plus the actual question, the first `.pbody`
        # often contains only the passage. Capturing the whole task block and then
        # stripping answers/solutions keeps all condition parts together.
        target_locator = locator
        locator = isolate_content(
            page,
            target_locator,
            root_id="codex-condition-shot-root",
            strip_selectors=CONDITION_STRIP_SELECTORS,
        )
        base_selector = used_selector or content_selector or "auto"
        used_selector = f"condition-only:{base_selector}"
    elif capture_mode == "solution":
        content_locator, content_selector = find_solution_locator(locator)
        if content_locator is None:
            raise RuntimeError("Не удалось найти блок решения на странице.")
        locator = isolate_content(
            page,
            content_locator,
            root_id="codex-solution-shot-root",
            strip_selectors=SOLUTION_STRIP_SELECTORS,
        )
        base_selector = content_selector or used_selector or "auto"
        used_selector = f"solution-only:{base_selector}"

    wait_until_ready(page, locator, wait_ms)
    locator.screenshot(path=str(output_path))
    return used_selector or "custom"


def capture_screenshot(
    url: str,
    output_path: str | Path | None = None,
    *,
    selector: str | None = None,
    capture_mode: CaptureMode = "task",
    headless: bool = False,
    background: bool = False,
    timeout: int = 45,
    wait_ms: int = 400,
    profile_dir: str | Path | None = None,
) -> tuple[Path, str]:
    normalized_url = normalize_url(url)
    target_output = build_output_path(
        normalized_url,
        str(output_path) if output_path is not None else None,
        suffix=mode_output_suffix(capture_mode),
    )
    profile_path = Path(profile_dir or (Path.cwd() / ".browser-profile")).resolve()
    profile_path.mkdir(parents=True, exist_ok=True)

    browser_name, browser_path = find_browser()
    print(f"Браузер: {browser_name} ({browser_path})")
    print(f"Ссылка: {normalized_url}")
    print(f"Файл: {target_output}")

    try:
        with sync_playwright() as playwright:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                *BACKGROUND_BROWSER_ARGS,
            ]
            if background and not headless:
                launch_args.extend(
                    [
                        "--window-position=-2400,0",
                        "--window-size=1600,2200",
                    ]
                )

            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                executable_path=str(browser_path),
                headless=headless,
                viewport={"width": 1600, "height": 2200},
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                args=launch_args,
            )
            install_fast_routes(context)
            context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                """
            )

            page = context.pages[0] if context.pages else context.new_page()
            page.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                """
            )
            page.goto(normalized_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            wait_for_manual_check(page, headless)
            if not headless and not background:
                try:
                    page.bring_to_front()
                except PlaywrightError:
                    pass

            prepare_page(page)
            used_mode = take_screenshot(
                page,
                target_output,
                capture_mode,
                selector,
                wait_ms,
            )
            context.close()
            return target_output, used_mode
    except KeyboardInterrupt as exc:
        raise RuntimeError("Остановлено пользователем.") from exc


def resolve_capture_mode(args: argparse.Namespace) -> CaptureMode:
    enabled = int(args.full_page) + int(args.condition_only) + int(args.solution_only)
    if enabled > 1:
        raise ValueError("Используйте только один из режимов: --full-page, --condition-only, --solution-only.")
    if args.full_page:
        return "full-page"
    if args.condition_only:
        return "condition"
    if args.solution_only:
        return "solution"
    return "task"


def main() -> int:
    args = parse_args()

    try:
        capture_mode = resolve_capture_mode(args)
        output_path, used_mode = capture_screenshot(
            args.url,
            output_path=args.output,
            selector=args.selector,
            capture_mode=capture_mode,
            headless=args.headless,
            background=args.background,
            timeout=args.timeout,
            wait_ms=args.wait_ms,
        )
        print(f"Скриншот сохранен: {output_path}")
        print(f"Режим снимка: {used_mode}")
        return 0
    except Exception as exc:
        print(f"Ошибка во время работы: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
