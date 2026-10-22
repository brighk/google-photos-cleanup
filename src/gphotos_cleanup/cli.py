from __future__ import annotations

import argparse
from pathlib import Path

from .app import run
from .browser import open_login_browser


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
        "--delete",
        action="store_true",
        help=(
            "After all downloads verify successfully, move downloaded "
            "Google Photos items to Trash."
        ),
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome headlessly. Log in once with --login first.",
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


def main() -> None:
    args = build_parser().parse_args()

    if args.login:
        open_login_browser(args.profile.expanduser().resolve())
        return

    result = run(
        output_dir=args.output.expanduser().resolve(),
        profile_dir=args.profile.expanduser().resolve(),
        delete=args.delete,
        headless=args.headless,
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
