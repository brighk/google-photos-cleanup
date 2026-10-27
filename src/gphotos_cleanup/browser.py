from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

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


# Button labels are localized; the account's language wins over the browser's.
# We ask for English with ?hl=en, and also accept Bulgarian in case Google
# ignores that.
# Anchored: a trashed photo also shows a "30 days left until permanently
# deleted" button, which must never match.
TRASH_LABEL = re.compile(
    r"^\s*(move to trash|delete|преместване в кошчето|изтриване)\s*$", re.I
)
ALREADY_TRASHED = re.compile(r"until permanently deleted", re.I)
# Google's trash dialog ("Moving items to trash") confirms with "Got it".
TRASH_DIALOG_TEXT = re.compile(r"trash|кошче", re.I)
TRASH_CONFIRM_LABEL = re.compile(
    r"^\s*(got it|move to trash|delete|разбрах|преместване в кошчето"
    r"|изтриване)\s*$",
    re.I,
)
# Large videos: Google shows "Downloading..." while it prepares the file, and
# the browser download can start well after 15 s.
PREPARING_DOWNLOAD = re.compile(r"^\s*(downloading|изтегля)", re.I)
# The menu item's name includes its shortcut ("Download Shift+D").
DOWNLOAD_MENU_LABEL = re.compile(r"^\s*(download|изтегляне)\b", re.I)
DOWNLOAD_TIMEOUT_S = 300
MORE_LABEL = re.compile(r"more options|\bmore\b|още опции", re.I)

MACOS_CHROME_APP = "Google Chrome.app"
MACOS_CHROME_BINARY = "Contents/MacOS/Google Chrome"
MACOS_CHROME_BUNDLE_ID = "com.google.Chrome"
# The real binary first: /usr/bin/google-chrome* are wrapper scripts.
LINUX_CHROME_PATHS = ("/opt/google/chrome/chrome",)
LINUX_CHROME_NAMES = ("google-chrome-stable", "google-chrome")


def find_chrome(override: Path | None = None) -> str:
    """
    Path to the installed Google Chrome. Login and automated runs must use
    the same Chrome, since they share one profile. Only Google Chrome, not
    Chromium: on macOS the profile's cookies are encrypted with a keychain
    entry that is specific to the browser.
    """
    if override is not None:
        path = override.expanduser()
        if path.is_dir():
            # Accept the .app bundle itself on macOS.
            path = path / MACOS_CHROME_BINARY
        if not path.is_file():
            raise SystemExit(f"Chrome not found at {path}.")
        return str(path)

    if sys.platform == "darwin":
        candidates = [
            Path("/Applications") / MACOS_CHROME_APP,
            Path.home() / "Applications" / MACOS_CHROME_APP,
        ]
        # Spotlight finds Chrome installed anywhere else.
        try:
            found = subprocess.run(
                ["mdfind", f"kMDItemCFBundleIdentifier == '{MACOS_CHROME_BUNDLE_ID}'"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
            candidates += [Path(line) for line in found.splitlines() if line]
        except (OSError, subprocess.SubprocessError):
            pass
        for app in candidates:
            if (app / MACOS_CHROME_BINARY).is_file():
                return str(app / MACOS_CHROME_BINARY)
    else:
        for path in LINUX_CHROME_PATHS:
            if Path(path).is_file():
                return path
        for name in LINUX_CHROME_NAMES:
            if found := shutil.which(name):
                return found

    raise SystemExit(
        "Google Chrome not found. Install it, or pass its path with --chrome."
    )


def open_login_browser(profile_dir: Path, chrome: str) -> None:
    """
    Launch plain Chrome (no automation attached) on the tool's profile so the
    user can sign in to Google. Blocks until the window is closed.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            chrome,
            f"--user-data-dir={profile_dir}",
            # Match Playwright's default so later runs can decrypt the cookies
            # (on Linux, plain Chrome would otherwise use the system keyring).
            "--password-store=basic",
            "--no-first-run",
            "--no-default-browser-check",
            LOGIN_URL,
        ],
        check=False,
    )


async def _click_lenient(locator: Locator) -> None:
    """
    Click, tolerating Playwright timing out after the click was dispatched
    (seen on Google's dialog buttons). Callers verify the outcome themselves.
    """
    try:
        await locator.click(timeout=5000)
    except PlaywrightTimeoutError:
        pass


def _item_id(media_url: str) -> str:
    """The AF1Qip... id at the end of a /photo/ or /video/ URL."""
    return urlsplit(media_url).path.rstrip("/").rsplit("/", 1)[-1]


def _with_english(url: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["hl"] = "en"
    return urlunsplit(parts._replace(query=urlencode(query)))


class GooglePhotosBrowser:
    def __init__(
        self,
        profile_dir: Path,
        output_dir: Path,
        *,
        chrome: str,
        headless: bool = False,
    ) -> None:
        self.profile_dir = profile_dir
        self.output_dir = output_dir
        self.chrome = chrome
        self.headless = headless
        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        # Grid page used by iter_media_urls. Single items are opened in
        # separate pages (new_item_page) so the grid keeps its scroll position.
        self.page: Page | None = None
        # Destinations picked for downloads still in flight, so two concurrent
        # downloads named e.g. IMG_0001.jpg don't pick the same file.
        self._reserved: set[Path] = set()
        # Item pages whose renderer crashed. A crashed page fails every later
        # call, so workers replace it (see replace_if_crashed).
        self._crashed: set[Page] = set()

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
            executable_path=self.chrome,
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
        page = await self.context.new_page()
        # Playwright tags handlers with an attribute, so a builtin like
        # set.add can't be passed directly.
        page.on("crash", lambda crashed: self._crashed.add(crashed))
        return page

    def is_crashed(self, page: Page) -> bool:
        return page in self._crashed

    async def close_item_page(self, page: Page) -> None:
        self._crashed.discard(page)
        await page.close()

    async def replace_if_crashed(self, page: Page) -> Page:
        if not self.is_crashed(page):
            return page
        await self.close_item_page(page)
        return await self.new_item_page()

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
        '/photo/' or '/video/'. The grid is virtualized: only rows near the
        viewport exist in the DOM, so we scroll less than one screen at a time
        (bigger jumps skip rows that never render) and stop only once the
        scroll container is at the bottom and nothing new has appeared for a
        while.
        """
        page = self._require_page()
        await page.goto(PHOTOS_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        seen: set[str] = set()
        idle_rounds = 0
        size = page.viewport_size or {"width": 1280, "height": 720}
        step = int(size["height"] * 0.6)

        while True:
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

            # At the bottom, give lazy loading ~10s to append more rows.
            # Away from the bottom, a long idle stretch (~45s) means loading
            # is stuck, so give up rather than loop forever.
            if idle_rounds >= 30 or (
                idle_rounds >= 7 and await self._grid_at_bottom(page)
            ):
                print(f"Found {len(seen)} items in the Photos grid.")
                return

            await page.mouse.move(size["width"] / 2, size["height"] / 2)
            await page.mouse.wheel(0, step)
            await page.wait_for_timeout(1500)

    @staticmethod
    async def _grid_at_bottom(page: Page) -> bool:
        # The grid scrolls inside its own container, not the window, so check
        # the tallest scrollable element.
        return await page.evaluate(
            """() => {
                let best = document.scrollingElement;
                for (const el of document.querySelectorAll('*')) {
                    const overflow = getComputedStyle(el).overflowY;
                    if ((overflow === 'auto' || overflow === 'scroll')
                        && el.scrollHeight > el.clientHeight
                        && el.scrollHeight > best.scrollHeight) {
                        best = el;
                    }
                }
                return best.scrollTop + best.clientHeight >= best.scrollHeight - 50;
            }"""
        )

    async def download_media(self, page: Page, media_url: str) -> Path:
        await self._open_item(page, media_url)

        # One waiter for the whole attempt, so a download that starts late
        # (from Shift+D or the menu) is still caught.
        waiter = asyncio.ensure_future(
            page.wait_for_event("download", timeout=DOWNLOAD_TIMEOUT_S * 1000)
        )
        try:
            # Shift+D is Google Photos' own download shortcut; it doesn't
            # depend on toolbar selectors.
            await page.keyboard.press("Shift+D")
            fallback_at = time.monotonic() + 15
            used_menu = False
            while not waiter.done():
                await asyncio.sleep(0.5)
                if used_menu or time.monotonic() < fallback_at:
                    continue
                preparing = page.get_by_text(PREPARING_DOWNLOAD).filter(
                    visible=True
                )
                if await preparing.count() > 0:
                    # Google is still preparing it; keep waiting.
                    continue
                # Nothing happened: try the More menu once.
                used_menu = True
                await self._click_menu_download(page, media_url)
            try:
                download = waiter.result()
            except PlaywrightTimeoutError:
                raise RuntimeError(
                    f"No download started within {DOWNLOAD_TIMEOUT_S}s for "
                    f"{media_url}. " + await self._debug_hint(page)
                ) from None
        finally:
            if not waiter.done():
                waiter.cancel()
            elif not waiter.cancelled():
                # Retrieve it so a failure here (e.g. the page crashed) isn't
                # also logged as "Task exception was never retrieved".
                waiter.exception()

        destination = self._reserve_download_path(download)
        try:
            await download.save_as(str(destination))
        finally:
            self._reserved.discard(destination)
        return destination

    async def _click_menu_download(self, page: Page, media_url: str) -> None:
        await self._open_more_menu(page)
        item = page.get_by_role("menuitem", name=DOWNLOAD_MENU_LABEL).filter(
            visible=True
        )
        if await item.count() == 0:
            raise RuntimeError(
                f"Could not find Download menu item for {media_url}. "
                + await self._debug_hint(page)
            )
        await _click_lenient(item.first)

    async def move_to_trash(self, page: Page, media_url: str) -> None:
        """
        Move one item to Trash. Returns only when the trash was confirmed;
        raises otherwise, so the caller never marks an untrashed item deleted.
        """
        await self._open_item(page, media_url)
        item_id = _item_id(media_url)

        # Trashed items still open at their normal URL, with a countdown
        # instead. Seen when a previous run trashed it but stopped before
        # recording it.
        trashed = page.get_by_text(ALREADY_TRASHED).filter(visible=True)
        if await trashed.count() > 0:
            return

        # Prefer a directly exposed trash/delete button if present.
        direct = page.get_by_role("button", name=TRASH_LABEL).filter(
            visible=True
        )
        # Re-check right before acting: the URL check in _open_item is the
        # only proof the viewer shows this item, and later checks rely on it.
        self._assert_on_item(page, media_url)
        if await direct.count() > 0:
            await _click_lenient(direct.first)
        else:
            await self._open_more_menu(page)
            item = page.get_by_role("menuitem", name=TRASH_LABEL).filter(
                visible=True
            )
            if await item.count() == 0:
                raise RuntimeError(
                    f"Could not find trash/delete action for {media_url}. "
                    + await self._debug_hint(page)
                )
            self._assert_on_item(page, media_url)
            await _click_lenient(item.first)

        await self._confirm_trash(page, media_url, item_id)

        # After trashing, the viewer moves to the next item or back to the
        # grid. Still showing this item means the trash didn't happen.
        try:
            await page.wait_for_function(
                "id => !location.href.includes(id)", arg=item_id, timeout=10_000
            )
        except PlaywrightTimeoutError:
            raise RuntimeError(
                f"Trash was not confirmed for {media_url}: the photo is still "
                "open after confirming. " + await self._debug_hint(page)
            ) from None

    async def _confirm_trash(
        self, page: Page, media_url: str, item_id: str
    ) -> None:
        """
        Click through Google's trash dialogs. Currently there are two in a
        row: a "Moving items to trash" notice ("Got it", possibly shown only
        once) and a "Remove from your Google Account?" box ("Move to trash").
        Stops as soon as the item leaves the viewer; move_to_trash then
        double-checks that.
        """
        dialog = (
            page.locator('[role="dialog"], [role="alertdialog"]')
            .filter(visible=True)
            .filter(has_text=TRASH_DIALOG_TEXT)
        )
        for step in range(3):
            # The first dialog must show up (or the item must close); after
            # that, give a follow-up dialog a few seconds to appear.
            deadline = time.monotonic() + (5 if step == 0 else 3)
            while await dialog.count() == 0:
                if item_id not in page.url:
                    return
                if time.monotonic() > deadline:
                    if step == 0:
                        raise RuntimeError(
                            "Neither a trash dialog appeared nor did the "
                            f"photo close for {media_url}. "
                            + await self._debug_hint(page)
                        )
                    return
                await page.wait_for_timeout(200)

            current = dialog.first
            text = await current.inner_text()
            confirm = current.get_by_role("button", name=TRASH_CONFIRM_LABEL)
            if await confirm.count() == 0:
                raise RuntimeError(
                    f"Could not find the confirm button in the trash dialog "
                    f"for {media_url}. " + await self._debug_hint(page)
                )
            await _click_lenient(confirm.last)

            # Wait for this dialog to go away (the next one, if any, has
            # different text).
            try:
                await page.wait_for_function(
                    """text => ![...document.querySelectorAll(
                        '[role=dialog],[role=alertdialog]')].some(e =>
                        e.getClientRects().length && e.innerText === text)""",
                    arg=text,
                    timeout=10_000,
                )
            except PlaywrightTimeoutError:
                raise RuntimeError(
                    f"Trash dialog for {media_url} stayed open after "
                    "confirming. " + await self._debug_hint(page)
                ) from None

        raise RuntimeError(
            f"Too many trash dialogs for {media_url}. "
            + await self._debug_hint(page)
        )

    async def _open_item(self, page: Page, media_url: str) -> None:
        await page.goto(_with_english(media_url), wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        # Never act on a page that isn't this item (e.g. a redirect).
        self._assert_on_item(page, media_url)
        # The viewer toolbar only appears after mouse movement.
        await self._reveal_toolbar(page)

    @staticmethod
    def _assert_on_item(page: Page, media_url: str) -> None:
        if _item_id(media_url) not in page.url:
            raise RuntimeError(
                f"Expected {media_url} but the browser is on {page.url}."
            )

    @staticmethod
    async def _reveal_toolbar(page: Page) -> None:
        size = page.viewport_size or {"width": 1280, "height": 720}
        await page.mouse.move(size["width"] / 2, size["height"] / 2)
        await page.mouse.move(size["width"] / 2, 40, steps=5)
        await page.wait_for_timeout(300)

    async def _open_more_menu(self, page: Page) -> None:
        await self._reveal_toolbar(page)
        menu = page.locator('[role="menu"]').filter(visible=True)
        candidates = [
            page.get_by_role("button", name=MORE_LABEL).filter(visible=True),
            page.locator('[aria-label*="More"]').filter(visible=True),
        ]

        # Judge success by the menu being open, not by the click returning:
        # Playwright can time out on a click that Google did act on (seen
        # while a video was playing). Re-check before each click, since
        # clicking the button again would close an open menu.
        for candidate in candidates:
            if await menu.count() > 0:
                return
            if await candidate.count() == 0:
                continue
            await _click_lenient(candidate.first)
            try:
                await menu.first.wait_for(state="visible", timeout=3000)
                return
            except PlaywrightTimeoutError:
                continue

        if await menu.count() > 0:
            return
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
