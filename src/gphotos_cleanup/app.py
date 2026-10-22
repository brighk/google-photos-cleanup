from __future__ import annotations

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


def run(
    *,
    output_dir: Path,
    profile_dir: Path,
    delete: bool,
    headless: bool,
) -> RunResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    state = StateStore(output_dir / ".gphotos-cleanup-state.json")

    downloaded = 0
    skipped = 0
    failed = 0
    deleted = 0

    with GooglePhotosBrowser(
        profile_dir=profile_dir,
        output_dir=output_dir,
        headless=headless,
    ) as browser:
        browser.ensure_logged_in()

        for media_url in browser.iter_media_urls():
            previous = state.get(media_url)
            if previous is not None:
                local_path = Path(previous.local_path)
                if previous.verified and verify_file(local_path):
                    skipped += 1
                    continue

            try:
                local_path = browser.download_media(media_url)
                verified = verify_file(local_path)
                state.put(
                    MediaRecord(
                        url=media_url,
                        local_path=str(local_path),
                        verified=verified,
                    )
                )

                if not verified:
                    failed += 1
                    print(f"[FAIL] verification failed: {media_url}")
                    continue

                downloaded += 1
                print(f"[OK] downloaded: {local_path}")
            except Exception as exc:
                failed += 1
                print(f"[FAIL] download {media_url}: {exc}")

        if delete:
            if failed:
                raise RuntimeError(
                    f"{failed} download(s) failed; refusing to delete cloud items."
                )

            records = state.verified_not_deleted()
            for record in records:
                local_path = Path(record.local_path)
                if not verify_file(local_path):
                    raise RuntimeError(
                        f"Local file is missing or empty: {local_path}. "
                        "Refusing to continue deletion."
                    )

            for record in records:
                try:
                    browser.move_to_trash(record.url)
                    state.mark_deleted(record.url)
                    deleted += 1
                    print(f"[OK] moved to trash: {record.url}")
                except Exception as exc:
                    print(f"[FAIL] delete {record.url}: {exc}")
                    raise

    return RunResult(
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        deleted=deleted,
    )
