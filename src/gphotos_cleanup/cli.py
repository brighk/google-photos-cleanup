from __future__ import annotations

import argparse
from pathlib import Path

from .app import run
from .browser import find_chrome, open_login_browser

DELETE_ALL_PHRASE = "trash everything"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gphotos-cleanup",
        description=(
            "Download Google Photos locally, verify them, and optionally "
            "move the corresponding cloud items to Trash."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Directory where downloaded media will be stored.",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        required=True,
        help="Persistent Chromium profile directory.",
    )
    parser.add_argument(
        "--chrome",
        type=Path,
        help=(
            "Path to Google Chrome (the binary, or Google Chrome.app on "
            "macOS). Found automatically if omitted."
        ),
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help=(
            "After all downloads verify successfully, move downloaded "
            "Google Photos items to Trash."
        ),
    )
    parser.add_argument(
        "--delete-all",
        action="store_true",
        help=(
            "Move EVERY item in the Google Photos library to Trash. Nothing "
            "is downloaded or checked first. Asks for confirmation unless "
            "--yes is given."
        ),
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the --delete-all confirmation prompt.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome headlessly. Log in once with --login first.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help=(
            "Number of photos processed at the same time, each in its own "
            "browser tab (default: 4, max: 16)."
        ),
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help=(
            "Open plain Chrome on the profile to sign in to Google, then "
            "exit when the window is closed. Does not download anything."
        ),
    )
    return parser


def _confirm_delete_all() -> bool:
    print(
        "--delete-all moves EVERY photo and video in your Google Photos "
        "library to Trash.\nNothing is downloaded or checked first. Google "
        "deletes trashed items permanently after a while."
    )
    try:
        answer = input(f'Type "{DELETE_ALL_PHRASE}" to continue: ')
    except EOFError:
        return False
    return answer.strip() == DELETE_ALL_PHRASE


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be between 1 and 16")
    if args.delete and args.delete_all:
        parser.error("use either --delete or --delete-all, not both")

    chrome = find_chrome(args.chrome)
    if args.login:
        open_login_browser(args.profile.expanduser().resolve(), chrome)
        return

    if args.delete_all and not args.yes and not _confirm_delete_all():
        print("Cancelled. Nothing was moved to Trash.")
        return

    result = run(
        output_dir=args.output.expanduser().resolve(),
        profile_dir=args.profile.expanduser().resolve(),
        chrome=chrome,
        delete=args.delete,
        headless=args.headless,
        workers=args.workers,
        delete_all=args.delete_all,
    )

    print()
    print(
        "summary: "
        f"downloaded={result.downloaded} "
        f"skipped={result.skipped} "
        f"failed={result.failed} "
        f"deleted={result.deleted}"
    )


if __name__ == "__main__":
    main()
