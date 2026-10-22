# Google Photos Cleanup

A deliberately small tool that does one thing:

1. Download photos/videos from Google Photos to a local directory.
2. Verify that downloaded files exist and are non-empty.
3. Optionally move the downloaded cloud items to Google Photos Trash.

## Important

This tool automates the Google Photos web UI with Playwright. Web UI selectors can change over time.

Deletion is intentionally opt-in. Without `--delete`, the tool only downloads and verifies.

It does **not** empty Google Photos Trash.

## Install

```bash
python -m venv .venv
source .venv/bin/activate

pip install -e .
playwright install chromium
```

## First login

Google blocks sign-in from automated browsers ("This browser or app may not be
secure"), so log in once with plain Google Chrome (must be installed):

```bash
gphotos-cleanup \
  --output ~/Pictures/google-photos \
  --profile ~/.config/gphotos-cleanup/chromium \
  --login
```

Sign in to Google in the Chrome window that opens, then quit Chrome (Cmd+Q).
The session is saved in the profile directory and reused by later runs.

## Download only

```bash
gphotos-cleanup \
  --output ~/Pictures/google-photos \
  --profile ~/.config/gphotos-cleanup/chromium
```

## Download, verify, then move downloaded items to Trash

```bash
gphotos-cleanup \
  --output ~/Pictures/google-photos \
  --profile ~/.config/gphotos-cleanup/chromium \
  --delete
```

## Behavior

The tool:

- opens Google Photos
- walks visible media items
- opens each item
- downloads it through the UI
- stores it locally
- verifies the resulting file exists and is non-empty
- records the item URL in a local state file
- if `--delete` is provided and every attempted download verified, reopens each recorded item and moves it to Trash

State is stored in:

```text
<output>/.gphotos-cleanup-state.json
```

This lets a rerun skip items that were already downloaded.

## Limitations

Google Photos uses a dynamic, virtualized interface. The exact selectors used by this project may need adjustment if Google changes the UI.

The program intentionally does not:

- deduplicate
- preserve albums
- rewrite EXIF metadata
- empty Trash
- provide synchronization
- expose multiple operating modes beyond download and optional delete
