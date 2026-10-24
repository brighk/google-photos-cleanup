from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import urljoin

from playwright.async_api import (
    BrowserContext,
    Download,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
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
        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        # Grid page used by iter_media_urls. Single items are opened in
        # separate pages (new_item_page) so the grid keeps its scroll position.
        self.page: Page | None = None
        # Destinations picked for downloads still in flight, so two concurrent
        # downloads named e.g. IMG_0001.jpg don't pick the same file.
        self._reserved: set[Path] = set()

    async def __aenter__(self) -> "GooglePhotosBrowser":
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        # Google refuses sign-in from Playwright's bundled Chromium, so use the
        # installed Chrome and drop the flags that mark it as automated.
        # Dropping --use-mock-keychain lets it read cookies saved by a plain
        # Chrome session (see open_login_browser).
        self.context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            channel="chrome",
            headless=self.headless,
            accept_downloads=True,
            ignore_default_args=["--enable-automation", "--use-mock-keychain"],
            args=["--disable-blink-features=AutomationControlled"],
        )
        pages = self.context.pages
        self.page = pages[0] if pages else await self.context.new_page()
        await self.page.goto(PHOTOS_URL, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(2000)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self.context is not None:
            await self.context.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def new_item_page(self) -> Page:
        if self.context is None:
            raise RuntimeError("Browser is not running")
        return await self.context.new_page()

    async def ensure_logged_in(self, timeout_s: int = 300) -> None:
        page = self._require_page()

        # Google may redirect after a short delay.
        await page.wait_for_timeout(1500)
        if self._on_photos(page):
            return

        # Logged-out visitors land on accounts.google.com or the marketing
        # page at www.google.com/photos/about/.
        if self.headless:
            raise RuntimeError(
                "Google login is required. Run once with --login, sign in, "
                "close Chrome, then execute the command again."
            )

        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print(
            f"Not logged in. Log in to Google in the browser window "
            f"(waiting up to {timeout_s}s)..."
        )
        deadline = time.monotonic() + timeout_s
        while not self._on_photos(page):
            if time.monotonic() > deadline:
                raise RuntimeError("Timed out waiting for Google login.")
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(2000)

    @staticmethod
    def _on_photos(page: Page) -> bool:
        return page.url.startswith(PHOTOS_URL)

    async def iter_media_urls(self) -> AsyncIterator[str]:
        """
        Scroll the main Photos view and yield unique media item URLs.

        Google Photos currently exposes individual media links whose href contains
        '/photo/' or '/video/'. The UI is virtualized, so we keep scrolling until
        several consecutive scrolls discover nothing new.
        """
        page = self._require_page()
        await page.goto(PHOTOS_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        seen: set[str] = set()
        idle_rounds = 0

        while idle_rounds < 6:
            links = page.locator('a[href*="/photo/"], a[href*="/video/"]')
            hrefs = await links.evaluate_all(
                "els => els.map(e => e.getAttribute('href'))"
            )
            discovered = 0

            for href in hrefs:
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

            size = page.viewport_size or {"width": 1280, "height": 720}
            await page.mouse.move(size["width"] / 2, size["height"] / 2)
            await page.mouse.wheel(0, 5000)
            await page.wait_for_timeout(1200)

    async def download_media(self, page: Page, media_url: str) -> Path:
        await self._open_item(page, media_url)

        # Shift+D is Google Photos' own download shortcut; it doesn't depend
        # on toolbar selectors. Fall back to the More menu if it does nothing.
        try:
            async with page.expect_download(timeout=15_000) as download_info:
                await page.keyboard.press("Shift+D")
            download = await download_info.value
        except PlaywrightTimeoutError:
            await self._open_more_menu(page)
            download_item = await self._menu_item(page, "Download")
            if await download_item.count() == 0:
                raise RuntimeError(
                    f"Could not find Download menu item for {media_url}. "
                    + await self._debug_hint(page)
                )
            async with page.expect_download(timeout=60_000) as download_info:
                await download_item.first.click()
            download = await download_info.value

        destination = self._reserve_download_path(download)
        try:
            await download.save_as(str(destination))
        finally:
            self._reserved.discard(destination)
        return destination

    async def move_to_trash(self, page: Page, media_url: str) -> None:
        await self._open_item(page, media_url)

        # Prefer a directly exposed trash/delete button if present.
        direct = page.get_by_role(
            "button",
            name=re.compile(r"(move to trash|delete)", re.I),
        ).filter(visible=True)
        if await direct.count() > 0:
            await direct.first.click()
        else:
            await self._open_more_menu(page)
            item = await self._menu_item(page, "Move to trash")
            if await item.count() == 0:
                item = await self._menu_item(page, "Delete")
            if await item.count() == 0:
                raise RuntimeError(
                    f"Could not find trash/delete action for {media_url}. "
                    + await self._debug_hint(page)
                )
            await item.first.click()

        await self._confirm_trash(page)

    async def _confirm_trash(self, page: Page) -> None:
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
                if await candidate.count() > 0:
                    await candidate.last.click(timeout=3000)
                    await page.wait_for_timeout(600)
                    return
            except PlaywrightTimeoutError:
                continue

    async def _open_item(self, page: Page, media_url: str) -> None:
        await page.goto(media_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        # The viewer toolbar only appears after mouse movement.
        await self._reveal_toolbar(page)

    @staticmethod
    async def _reveal_toolbar(page: Page) -> None:
        size = page.viewport_size or {"width": 1280, "height": 720}
        await page.mouse.move(size["width"] / 2, size["height"] / 2)
        await page.mouse.move(size["width"] / 2, 40, steps=5)
        await page.wait_for_timeout(300)

    async def _open_more_menu(self, page: Page) -> None:
        await self._reveal_toolbar(page)
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
                if await candidate.count() > 0:
                    await candidate.first.click(timeout=3000)
                    await page.wait_for_timeout(300)
                    return
            except PlaywrightTimeoutError:
                continue

        raise RuntimeError(
            "Could not find the Google Photos More menu. "
            + await self._debug_hint(page)
        )

    async def _debug_hint(self, page: Page) -> str:
        """Save a screenshot and list visible button labels for diagnosis."""
        debug_dir = self.output_dir / ".gphotos-debug"
        debug_dir.mkdir(exist_ok=True)
        shot: Path | None = debug_dir / f"{int(time.time() * 1000)}.png"
        try:
            await page.screenshot(path=str(shot))
        except Exception:
            shot = None

        labels: list[str] = []
        buttons = page.locator('[role="button"], button').filter(visible=True)
        for idx in range(min(await buttons.count(), 40)):
            label = await buttons.nth(idx).get_attribute("aria-label")
            if label and label not in labels:
                labels.append(label)

        return (
            f"Visible buttons: {labels}. "
            + (f"Screenshot: {shot}" if shot else "Screenshot failed.")
        )

    async def _menu_item(self, page: Page, text: str) -> Locator:
        pattern = re.compile(rf"^{re.escape(text)}$", re.I)
        item = page.get_by_role("menuitem", name=pattern)
        if await item.count() > 0:
            return item
        return page.get_by_text(pattern)

    def _reserve_download_path(self, download: Download) -> Path:
        # No await in here, so picking and reserving a name is atomic with
        # respect to other workers.
        suggested = Path(download.suggested_filename).name
        if not suggested:
            suggested = f"google-photo-{int(time.time() * 1000)}"

        candidate = self.output_dir / suggested
        stem = candidate.stem
        suffix = candidate.suffix
        index = 1
        while candidate.exists() or candidate in self._reserved:
            candidate = self.output_dir / f"{stem}-{index}{suffix}"
            index += 1

        self._reserved.add(candidate)
        return candidate

    def _require_page(self) -> Page:
        if self.page is None:
            raise RuntimeError("Browser is not running")
        return self.page
