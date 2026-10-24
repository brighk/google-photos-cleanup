from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from .browser import GooglePhotosBrowser
from .state import MediaRecord, StateStore
from .verify import verify_file


@dataclass(frozen=True)
class RunResult:
    downloaded: int
    skipped: int
    failed: int
    deleted: int


@dataclass
class _Counters:
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    deleted: int = 0


def run(
    *,
    output_dir: Path,
    profile_dir: Path,
    delete: bool,
    headless: bool,
    workers: int = 4,
) -> RunResult:
    return asyncio.run(
        _run(
            output_dir=output_dir,
            profile_dir=profile_dir,
            delete=delete,
            headless=headless,
            workers=workers,
        )
    )


async def _run(
    *,
    output_dir: Path,
    profile_dir: Path,
    delete: bool,
    headless: bool,
    workers: int,
) -> RunResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore(output_dir / ".gphotos-cleanup-state.json")
    counts = _Counters()

    async with GooglePhotosBrowser(
        profile_dir=profile_dir,
        output_dir=output_dir,
        headless=headless,
    ) as browser:
        await browser.ensure_logged_in()

        # The grid scroller feeds URLs into a bounded queue; each worker owns
        # one tab and downloads from it. All of this runs on one event loop
        # thread, so state and counters need no locking.
        queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=workers * 2)

        async def produce() -> None:
            async for media_url in browser.iter_media_urls():
                await queue.put(media_url)
            for _ in range(workers):
                await queue.put(None)

        async def download_worker() -> None:
            page = await browser.new_item_page()
            try:
                while (media_url := await queue.get()) is not None:
                    await _download_one(browser, page, state, counts, media_url)
            finally:
                await page.close()

        # TaskGroup cancels the other tasks if any of them raises.
        async with asyncio.TaskGroup() as group:
            group.create_task(produce())
            for _ in range(workers):
                group.create_task(download_worker())

        if delete:
            await _delete_all(browser, state, counts, workers)

    return RunResult(
        downloaded=counts.downloaded,
        skipped=counts.skipped,
        failed=counts.failed,
        deleted=counts.deleted,
    )


async def _download_one(
    browser: GooglePhotosBrowser,
    page,
    state: StateStore,
    counts: _Counters,
    media_url: str,
) -> None:
    previous = state.get(media_url)
    if previous is not None:
        local_path = Path(previous.local_path)
        if previous.verified and verify_file(local_path):
            counts.skipped += 1
            return

    try:
        local_path = await browser.download_media(page, media_url)
        verified = verify_file(local_path)
        state.put(
            MediaRecord(
                url=media_url,
                local_path=str(local_path),
                verified=verified,
            )
        )

        if not verified:
            counts.failed += 1
            print(f"[FAIL] verification failed: {media_url}")
            return

        counts.downloaded += 1
        print(f"[OK] downloaded: {local_path}")
    except Exception as exc:
        counts.failed += 1
        print(f"[FAIL] download {media_url}: {exc}")


async def _delete_all(
    browser: GooglePhotosBrowser,
    state: StateStore,
    counts: _Counters,
    workers: int,
) -> None:
    # Critical safety gate: no deletion if any download failed.
    if counts.failed:
        raise RuntimeError(
            f"{counts.failed} download(s) failed; refusing to delete cloud items."
        )

    records = state.verified_not_deleted()
    for record in records:
        local_path = Path(record.local_path)
        if not verify_file(local_path):
            raise RuntimeError(
                f"Local file is missing or empty: {local_path}. "
                "Refusing to continue deletion."
            )

    pending: asyncio.Queue[MediaRecord] = asyncio.Queue()
    for record in records:
        pending.put_nowait(record)

    async def delete_worker() -> None:
        page = await browser.new_item_page()
        try:
            while not pending.empty():
                record = pending.get_nowait()
                try:
                    await browser.move_to_trash(page, record.url)
                except Exception as exc:
                    # Stop everything on the first failure (TaskGroup
                    # cancels the other workers).
                    print(f"[FAIL] delete {record.url}: {exc}")
                    raise
                state.mark_deleted(record.url)
                counts.deleted += 1
                print(f"[OK] moved to trash: {record.url}")
        finally:
            await page.close()

    async with asyncio.TaskGroup() as group:
        for _ in range(min(workers, len(records))):
            group.create_task(delete_worker())
