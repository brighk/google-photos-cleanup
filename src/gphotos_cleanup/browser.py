from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urljoin

from playwright.sync_api import (
    BrowserContext,
    Download,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

PHOTOS_URL = "https://photos.google.com/"
LOGIN_URL = (
    "https://accounts.google.com/ServiceLogin?continue=" + PHOTOS_URL
)


CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def open_login_browser(profile_dir: Path) -> None:
    """
    Launch plain Chrome (no automation attached) on the tool's profile so the
    user can sign in to Google. Blocks until the window is closed.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            CHROME_PATH,
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            LOGIN_URL,
        ],
        check=False,
    )


class GooglePhotosBrowser:
    def __init__(
        self,
        profile_dir: Path,
        output_dir: Path,
        *,
        headless: bool = False,
    ) -> None:
        self.profile_dir = profile_dir
        self.output_dir = output_dir
        self.headless = headless
        self._playwright = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        # Separate tab for opening single items, so the grid in self.page
        # keeps its scroll position while iter_media_urls is running.
        self.item_page: Page | None = None

    def __enter__(self) -> "GooglePhotosBrowser":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = sync_playwright().start()
        # Google refuses sign-in from Playwright's bundled Chromium, so use the
        # installed Chrome and drop the flags that mark it as automated.
        # Dropping --use-mock-keychain lets it read cookies saved by a plain
        # Chrome session (see open_login_browser).
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            channel="chrome",
            headless=self.headless,
            accept_downloads=True,
            ignore_default_args=["--enable-automation", "--use-mock-keychain"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        pages = self.context.pages
        self.page = pages[0] if pages else self.context.new_page()
        self.page.goto(PHOTOS_URL, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)
        self.item_page = self.context.new_page()
        self.page.bring_to_front()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.context is not None:
            self.context.close()
        if self._playwright is not None:
            self._playwright.stop()

    def ensure_logged_in(self, timeout_s: int = 300) -> None:
        page = self._require_page()

        # Google may redirect after a short delay.
        page.wait_for_timeout(1500)
        if self._on_photos(page):
            return

        # Logged-out visitors land on accounts.google.com or the marketing
        # page at www.google.com/photos/about/.
        if self.headless:
            raise RuntimeError(
                "Google login is required. Run once with --login, sign in, "
                "close Chrome, then execute the command again."
            )

        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print(
            f"Not logged in. Log in to Google in the browser window "
            f"(waiting up to {timeout_s}s)..."
        )
        deadline = time.monotonic() + timeout_s
        while not self._on_photos(page):
            if time.monotonic() > deadline:
                raise RuntimeError("Timed out waiting for Google login.")
            page.wait_for_timeout(1000)
        page.wait_for_timeout(2000)

    @staticmethod
    def _on_photos(page: Page) -> bool:
        return page.url.startswith(PHOTOS_URL)

    def iter_media_urls(self) -> Iterator[str]:
        """
        Scroll the main Photos view and yield unique media item URLs.

        Google Photos currently exposes individual media links whose href contains
        '/photo/' or '/video/'. The UI is virtualized, so we keep scrolling until
        several consecutive scrolls discover nothing new.
        """
        page = self._require_page()
        page.goto(PHOTOS_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        seen: set[str] = set()
        idle_rounds = 0

        while idle_rounds < 6:
            links = page.locator('a[href*="/photo/"], a[href*="/video/"]')
            count = links.count()
            discovered = 0

            for idx in range(count):
                href = links.nth(idx).get_attribute("href")
                if not href:
                    continue

                # Hrefs are relative, e.g. "./photo/AF1Qip...".
                href = urljoin(page.url, href)

                if href in seen:
                    continue

                seen.add(href)
                discovered += 1
                yield href

            if discovered == 0:
                idle_rounds += 1
            else:
                idle_rounds = 0

            page.bring_to_front()
            size = page.viewport_size or {"width": 1280, "height": 720}
            page.mouse.move(size["width"] / 2, size["height"] / 2)
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(1200)

    def download_media(self, media_url: str) -> Path:
        page = self._open_item(media_url)

        # Shift+D is Google Photos' own download shortcut; it doesn't depend
        # on toolbar selectors. Fall back to the More menu if it does nothing.
        try:
            with page.expect_download(timeout=15_000) as download_info:
                page.keyboard.press("Shift+D")
            download = download_info.value
        except PlaywrightTimeoutError:
            self._open_more_menu(page)
            download_item = self._menu_item(page, "Download")
            if download_item.count() == 0:
                raise RuntimeError(
                    f"Could not find Download menu item for {media_url}. "
                    + self._debug_hint(page)
                )
            with page.expect_download(timeout=60_000) as download_info:
                download_item.first.click()
            download = download_info.value

        destination = self._safe_download_path(download)
        download.save_as(str(destination))
        return destination

    def move_to_trash(self, media_url: str) -> None:
        page = self._open_item(media_url)

        # Prefer a directly exposed trash/delete button if present.
        direct = page.get_by_role(
            "button",
            name=re.compile(r"(move to trash|delete)", re.I),
        ).filter(visible=True)
        if direct.count() > 0:
            direct.first.click()
        else:
            self._open_more_menu(page)
            item = self._menu_item(page, "Move to trash")
            if item.count() == 0:
                item = self._menu_item(page, "Delete")
            if item.count() == 0:
                raise RuntimeError(
                    f"Could not find trash/delete action for {media_url}. "
                    + self._debug_hint(page)
                )
            item.first.click()

        self._confirm_trash(page)

    def _confirm_trash(self, page: Page) -> None:
        candidates = [
            page.get_by_role(
                "button",
                name=re.compile(r"move to trash", re.I),
            ),
            page.get_by_role(
                "button",
                name=re.compile(r"delete", re.I),
            ),
        ]

        for candidate in candidates:
            try:
                if candidate.count() > 0:
                    candidate.last.click(timeout=3000)
                    page.wait_for_timeout(600)
                    return
            except PlaywrightTimeoutError:
                continue

    def _open_item(self, media_url: str) -> Page:
        page = self._require_item_page()
        page.goto(media_url, wait_until="domcontentloaded")
        # Background tabs get throttled and the viewer toolbar only appears
        # after mouse movement.
        page.bring_to_front()
        page.wait_for_timeout(1500)
        self._reveal_toolbar(page)
        return page

    @staticmethod
    def _reveal_toolbar(page: Page) -> None:
        size = page.viewport_size or {"width": 1280, "height": 720}
        page.mouse.move(size["width"] / 2, size["height"] / 2)
        page.mouse.move(size["width"] / 2, 40, steps=5)
        page.wait_for_timeout(300)

    def _open_more_menu(self, page: Page) -> None:
        self._reveal_toolbar(page)
        candidates = [
            page.get_by_role(
                "button",
                name=re.compile(r"more options", re.I),
            ).filter(visible=True),
            page.get_by_role(
                "button",
                name=re.compile(r"\bmore\b", re.I),
            ).filter(visible=True),
            page.locator('[aria-label*="More"]').filter(visible=True),
        ]

        for candidate in candidates:
            try:
                if candidate.count() > 0:
                    candidate.first.click(timeout=3000)
                    page.wait_for_timeout(300)
                    return
            except PlaywrightTimeoutError:
                continue

        raise RuntimeError(
            "Could not find the Google Photos More menu. "
            + self._debug_hint(page)
        )

    def _debug_hint(self, page: Page) -> str:
        """Save a screenshot and list visible button labels for diagnosis."""
        debug_dir = self.output_dir / ".gphotos-debug"
        debug_dir.mkdir(exist_ok=True)
        shot = debug_dir / f"{int(time.time() * 1000)}.png"
        try:
            page.screenshot(path=str(shot))
        except Exception:
            shot = None

        labels: list[str] = []
        buttons = page.locator('[role="button"], button').filter(visible=True)
        for idx in range(min(buttons.count(), 40)):
            label = buttons.nth(idx).get_attribute("aria-label")
            if label and label not in labels:
                labels.append(label)

        return (
            f"Visible buttons: {labels}. "
            + (f"Screenshot: {shot}" if shot else "Screenshot failed.")
        )

    def _menu_item(self, page: Page, text: str) -> Locator:
        pattern = re.compile(rf"^{re.escape(text)}$", re.I)
        item = page.get_by_role("menuitem", name=pattern)
        if item.count() > 0:
            return item
        return page.get_by_text(pattern)

    def _safe_download_path(self, download: Download) -> Path:
        suggested = Path(download.suggested_filename).name
        if not suggested:
            suggested = f"google-photo-{int(time.time() * 1000)}"

        candidate = self.output_dir / suggested
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        index = 1
        while True:
            alternate = self.output_dir / f"{stem}-{index}{suffix}"
            if not alternate.exists():
                return alternate
            index += 1

    def _require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("Browser is not running")
        return self.page

    def _require_item_page(self) -> Page:
        if self.item_page is None:
            raise RuntimeError("Browser is not running")
        return self.item_page
