# gphotos-cleanup — diagrams

Part 1 is a set of simple whiteboard diagrams. Part 2 has the detailed ones, with
real class, method and file names.

Every diagram is Mermaid. GitHub renders Mermaid. VS Code needs the "Markdown Preview
Mermaid Support" extension. draw.io can import one with Arrange → Insert → Advanced → Mermaid.

---

# Part 1 — Simple (whiteboard versions)

## S1. The whole idea in one line

```mermaid
flowchart LR
    G[("Google Photos<br/>cloud")] -->|"1 · download"| L[("Local folder<br/>on your Mac")]
    L --> V{"2 · verify<br/>file exists and<br/>size > 0?"}
    V -->|"yes, and --delete"| T["3 · move cloud copy<br/>to Google Trash"]
    V -->|"no"| S["STOP<br/>never delete"]
```

> Talking point: the tool drives a **real Chrome window** the way a human would. It doesn't
> use a Google API. It clicks, presses Shift+D and scrolls.

## S2. The five files and their jobs

```mermaid
flowchart TB
    CLI["cli.py<br/><b>front door</b><br/>reads your flags"] --> APP["app.py<br/><b>the boss</b><br/>runs both phases"]
    APP --> BR["browser.py<br/><b>robot hands</b><br/>drives Chrome"]
    APP --> ST["state.py<br/><b>notebook</b><br/>remembers what's done"]
    APP --> VF["verify.py<br/><b>inspector</b><br/>is the file really there?"]
```

> Talking point: Only browser.py knows that Google Photos exists. If Google changes its web UI, browser.py is the only file you have to fix.

## S3. The conveyor belt (how it goes fast)

```mermaid
flowchart LR
    SC["Scroller<br/>one tab scrolls the grid<br/>and finds photo links"] --> Q[["Queue<br/>a short line of URLs"]]
    Q --> W1["Worker tab 1"]
    Q --> W2["Worker tab 2"]
    Q --> W3["Worker tab ..."]
    W1 & W2 & W3 --> D[("Disk + notebook")]
```

> Talking point: One tab scrolls and finds links while N tabs download at the same time. The queue holds at most 2 × N URLs, so the scroller can't run far ahead of the workers.

## S4. The safety gate

```mermaid
flowchart LR
    A["Download<br/>everything"] --> B{"Any failure?"}
    B -->|"yes"| C["Delete NOTHING"]
    B -->|"no"| D{"All files still<br/>on disk?"}
    D -->|"no"| C
    D -->|"yes"| E["Trash in the cloud,<br/>one by one"]
    E --> F["Google Trash keeps items<br/>for a while, so you can undo"]
```

> Talking point: Nothing is deleted unless every download succeeded, and every file is checked on disk again right before deleting. The separate --delete-all mode (D12) skips this gate on purpose.

---

# Part 2 — Detailed

## D1. Modules, external systems and files on disk

```mermaid
flowchart TB
    user(["You, in the terminal<br/>gphotos-cleanup --output ... --profile ..."])

    subgraph pkg["package gphotos_cleanup"]
        cli["cli.py<br/>build_parser() · main()<br/>_confirm_delete_all()"]
        app["app.py<br/>run() · _run() · _download_all() · _download_one()<br/>_delete_all() · _trash_everything() · _report_failure()<br/>RunResult · _Counters"]
        browser["browser.py<br/>GooglePhotosBrowser<br/>open_login_browser()"]
        state["state.py<br/>StateStore · MediaRecord"]
        verify["verify.py<br/>verify_file()"]
    end

    user --> cli
    cli -->|"--login"| browser
    cli -->|"run(...)"| app
    app --> browser
    app --> state
    app --> verify

    browser -->|"Playwright<br/>launch_persistent_context(channel=chrome)"| chrome["Installed Google Chrome<br/>with persistent profile dir<br/>(holds your login cookies)"]
    chrome <-->|"HTTPS"| gp[("photos.google.com")]

    subgraph out["--output directory"]
        media["IMG_0001.jpg, VID_0002.mp4, ...<br/>(the downloads)"]
        sj[".gphotos-cleanup-state.json"]
        fl[".gphotos-failures.log"]
        dbg[".gphotos-debug/*.png<br/>(screenshots on errors)"]
    end

    browser -->|"download.save_as()"| media
    browser -->|"_debug_hint()"| dbg
    state -->|"save(): tmp file + replace"| sj
    app -->|"_report_failure()"| fl
```

> Talking point: The output folder holds four things: your files, the state notebook, a failure log and debug screenshots. The Chrome profile folder holds the Google login.

## D2. Class diagram

```mermaid
classDiagram
    direction LR

    class GooglePhotosBrowser {
        +Path profile_dir
        +Path output_dir
        +bool headless
        +BrowserContext context
        +Page page  «grid tab»
        -Playwright _playwright
        -set~Path~ _reserved
        +__aenter__() GooglePhotosBrowser
        +__aexit__()
        +new_item_page() Page
        +ensure_logged_in(timeout_s=300)
        +iter_media_urls() AsyncIterator~str~
        +download_media(page, media_url) Path
        +move_to_trash(page, media_url)
        +trash_all(on_trashed)
        -_open_item(page, media_url)
        -_click_menu_download(page, media_url)
        -_confirm_trash(page, what, gone)
        -_select_batch(page, limit, trashed) list~str~
        -_select_tile(page, media_url)
        -_trash_selection(page, count)
        -_open_more_menu(page)
        -_debug_hint(page) str
        -_reserve_download_path(download) Path
        -_require_page() Page
        -_on_photos(page)$ bool
        -_grid_at_bottom(page)$ bool
        -_assert_on_item(page, media_url)$
        -_reveal_toolbar(page)$
    }

    class StateStore {
        +Path path
        -dict~str,MediaRecord~ _records
        -_load()
        +save()
        +get(url) MediaRecord
        +put(record)
        +mark_deleted(url)
        +verified_not_deleted() list~MediaRecord~
        +all_records() list~MediaRecord~
    }

    class MediaRecord {
        <<frozen dataclass>>
        +str url
        +str local_path
        +bool verified
        +bool deleted = False
    }

    class RunResult {
        <<frozen dataclass>>
        +int downloaded
        +int skipped
        +int failed
        +int deleted
    }

    class Counters["_Counters"] {
        <<mutable dataclass>>
        +int downloaded
        +int skipped
        +int failed
        +int deleted
    }

    class app_py["app.py functions"] {
        <<module>>
        +run(output_dir, profile_dir, delete, headless, workers, delete_all) RunResult
        -_run(...) RunResult
        -_download_all(browser, state, counts, workers)
        -_trash_everything(browser, state, counts)
        -_download_one(browser, page, state, counts, media_url)
        -_delete_all(browser, state, counts, workers)
        -_report_failure(state, message)
    }

    class browser_py["browser.py functions"] {
        <<module>>
        +open_login_browser(profile_dir)
        -_click_lenient(locator)
        -_item_id(media_url) str
        -_with_english(url) str
    }

    class cli_py["cli.py"] {
        <<module>>
        +build_parser() ArgumentParser
        +main()
        -_confirm_delete_all() bool
    }

    class verify_py["verify.py"] {
        <<module>>
        +verify_file(path) bool
    }

    StateStore "1" o-- "*" MediaRecord : _records keyed by url
    app_py ..> GooglePhotosBrowser : async with
    app_py ..> StateStore : creates
    app_py ..> Counters : mutates
    app_py ..> RunResult : returns
    app_py ..> verify_py : calls
    cli_py ..> app_py : run()
    cli_py ..> browser_py : open_login_browser()
    GooglePhotosBrowser ..> browser_py : helpers
```

> Talking point: Two classes carry the logic: GooglePhotosBrowser does all the UI automation and StateStore does persistence. MediaRecord is immutable, so a change builds a new record. `$` marks static methods.

## D3. Main flow, from command to summary

```mermaid
flowchart TD
    M["cli.main()"] --> P["build_parser().parse_args()"]
    P --> W{"1 ≤ --workers ≤ 16, and not<br/>both --delete and --delete-all?"}
    W -->|"no"| E0["parser.error"]
    W -->|"yes"| LG{"--login?"}
    LG -->|"yes"| OL["open_login_browser(profile)<br/>plain Chrome, no automation<br/>you sign in, then Cmd+Q"] --> X0(["exit"])
    LG -->|"no"| CF{"--delete-all<br/>without --yes?"}
    CF -->|"yes"| TP{"typed<br/>'trash everything'?"}
    TP -->|"no"| X1(["Cancelled, exit"])
    TP -->|"yes"| R
    CF -->|"no"| R["run() → asyncio.run(_run())"]

    R --> R1["mkdir output<br/>state = StateStore(output/.gphotos-cleanup-state.json)<br/>counts = _Counters()"]
    R1 --> AE["async with GooglePhotosBrowser<br/>__aenter__: start Playwright, launch Chrome<br/>with the profile, open grid tab, goto photos.google.com"]
    AE --> LI{"ensure_logged_in()<br/>URL starts with photos.google.com?"}
    LI -->|"yes"| MODE
    LI -->|"no, headless"| E1["RuntimeError: run --login first"]
    LI -->|"no, visible"| WL["goto login page<br/>poll every 1s, up to 300s"] --> MODE

    MODE{"--delete-all?"} -->|"yes"| TA["_trash_everything()<br/>browser.trash_all(): see D12"] --> AX
    MODE -->|"no"| PH1
    PH1["PHASE 1: _download_all()<br/>TaskGroup: produce() + N × download_worker()"] --> DQ{"--delete?"}
    DQ -->|"no"| AX
    DQ -->|"yes"| PH2["PHASE 2: _delete_all()"] --> AX
    AX["__aexit__: close Chrome, stop Playwright"] --> RR["RunResult(downloaded, skipped, failed, deleted)"]
    RR --> SUM(["print summary: downloaded=… skipped=… failed=… deleted=…"])
```

> Talking point: Both phases run inside one browser session: download everything, then optionally delete. --login is a separate mode that opens plain Chrome, because Google blocks sign-in from automated browsers.

## D4. Phase 1 concurrency: producer, queue, workers

```mermaid
flowchart LR
    subgraph grid["grid tab · browser.page"]
        PR["produce()<br/>async for url in browser.iter_media_urls()"]
    end

    PR -->|"await queue.put(url)<br/>blocks while full = backpressure"| Q[["asyncio.Queue<br/>maxsize = workers × 2"]]
    PR -.->|"after the last URL:<br/>put None once per worker"| Q

    subgraph workers["N workers, each with its own tab (new_item_page)"]
        W1["download_worker 1"]
        W2["download_worker 2"]
        WN["download_worker N"]
    end

    Q --> W1 & W2 & WN
    W1 & W2 & WN -->|"until it gets None"| D1["_download_one(browser, page, state, counts, url)"]
    D1 --> SH[("shared: StateStore + _Counters<br/>a single event-loop thread, so no locks")]

    TG["asyncio.TaskGroup<br/>if any task raises, the others are cancelled"] -.- PR
    TG -.- workers
```

> Talking point: This is the classic producer/consumer pattern on asyncio. None is the stop signal, one per worker. Everything runs on one thread, so the shared state needs no locks.

## D5. Finding photos: `iter_media_urls()` scroll loop

```mermaid
flowchart TD
    A["goto photos.google.com · wait 2s<br/>seen = set() · idle_rounds = 0<br/>step = 0.6 × viewport height"] --> B["collect hrefs of<br/>a[href*='/photo/'], a[href*='/video/']"]
    B --> C["for each href:<br/>urljoin to absolute URL<br/>if not in seen: add it and yield (goes to the queue)"]
    C --> N{"anything new<br/>this round?"}
    N -->|"yes"| R0["idle_rounds = 0"]
    N -->|"no"| R1["idle_rounds += 1"]
    R0 & R1 --> S{"idle_rounds ≥ 30<br/>OR idle_rounds ≥ 7 and _grid_at_bottom()?"}
    S -->|"yes"| END(["print 'Found N items' · stop"])
    S -->|"no"| SC["mouse.wheel(0, step) · wait 1.5s<br/>(small steps: the grid is virtualized,<br/>big jumps skip rows that never render)"]
    SC --> B
```

> Talking point: Google's grid is virtualized, so only rows near the screen exist in the HTML. The scroller moves 60% of a screen at a time and stops only after several rounds with nothing new.

## D6. One item: `_download_one()` decisions

```mermaid
flowchart TD
    S["_download_one(url)"] --> G["previous = state.get(url)"]
    G --> DEL{"previous.deleted?"}
    DEL -->|"yes"| WARN["WARN: marked deleted but still in grid<br/>reset the record to deleted=False"] --> HAS
    DEL -->|"no"| HAS{"previous exists, verified,<br/>and verify_file(path) OK?"}
    HAS -->|"yes"| SKIP(["skipped += 1"])
    HAS -->|"no"| DL["browser.download_media(page, url)"]
    DL -->|"exception"| F2["failed += 1<br/>_report_failure → print + .gphotos-failures.log"]
    DL -->|"local_path"| V["verified = verify_file(local_path)"]
    V --> PUT["state.put(MediaRecord(url, path, verified))<br/>saved to JSON right away"]
    PUT --> OKQ{"verified?"}
    OKQ -->|"yes"| OK(["downloaded += 1 · print OK"])
    OKQ -->|"no"| F1["failed += 1<br/>_report_failure"]
```

> Talking point: Reruns are cheap because anything already verified on disk is skipped. Each result is written to the JSON right away, so a crash loses at most the items in flight.

## D7. `download_media()` sequence

```mermaid
sequenceDiagram
    autonumber
    participant A as app._download_one
    participant B as GooglePhotosBrowser
    participant P as Page (worker tab)
    participant G as Google Photos
    participant D as Disk

    A->>B: download_media(page, url)
    B->>P: goto(url + ?hl=en) · wait 1.5s
    B->>B: _assert_on_item: URL must contain the item id
    B->>P: _reveal_toolbar: move the mouse so the toolbar appears
    B->>P: waiter = wait_for_event("download", 300s)
    B->>P: keyboard.press("Shift+D")
    loop every 0.5s until the waiter is done
        opt 15s passed, menu not tried, and no "Downloading…" text
            B->>P: _click_menu_download: More menu, then Download
        end
    end
    G-->>P: file bytes
    P-->>B: Download object (or timeout → RuntimeError + _debug_hint)
    B->>B: _reserve_download_path: IMG.jpg, IMG-1.jpg, …<br/>no await inside, so workers can't pick the same name
    B->>D: download.save_as(destination)
    B->>B: release the reservation
    B-->>A: destination Path
```

> Talking point: Shift+D is Google Photos' own download shortcut. If no download starts within 15 s and Google isn't showing "Downloading…", the tool tries the More menu once. One waiter covers the whole attempt, so a late download is still caught.

## D8. Phase 2: `_delete_all()`

```mermaid
flowchart TD
    S(["--delete was given"]) --> G1{"counts.failed > 0?"}
    G1 -->|"yes"| X1["RuntimeError<br/>refusing to delete cloud items"]
    G1 -->|"no"| R["records = state.verified_not_deleted()"]
    R --> G2{"verify_file() OK for<br/>EVERY record?"}
    G2 -->|"no"| X2["RuntimeError<br/>local file missing or empty"]
    G2 -->|"yes"| Q["put all records into the pending queue"]
    Q --> TG["TaskGroup: min(workers, len(records)) × delete_worker()"]
    TG --> L{"pending empty?"}
    L -->|"yes"| DONE(["done"])
    L -->|"no"| NP["take a record · open a FRESH tab<br/>(a late click can't land on the next photo)"]
    NP --> MT["browser.move_to_trash(page, url)"]
    MT -->|"ok"| MD["close tab · state.mark_deleted(url)<br/>deleted += 1"] --> L
    MT -->|"exception"| X3["close tab · re-raise<br/>TaskGroup cancels every worker: fail fast"]
```

> Talking point: Two gates come before the first deletion: zero failures, and every file still on disk. After that, the first error stops all workers.

## D9. `move_to_trash()` and `_confirm_trash()`

```mermaid
flowchart TD
    O["_open_item: goto url?hl=en<br/>assert URL has the item id · reveal toolbar"] --> AT{"'…until permanently deleted'<br/>visible?"}
    AT -->|"yes: trashed by an earlier run"| RET(["return: counts as success"])
    AT -->|"no"| DB{"visible button named<br/>Move to trash / Delete?"}
    DB -->|"yes"| C1["_assert_on_item · click"]
    DB -->|"no"| MM["_open_more_menu()<br/>find menuitem Move to trash"]
    MM -->|"not found"| XE["RuntimeError + screenshot"]
    MM -->|"found"| C2["_assert_on_item · click"]
    C1 & C2 --> CT

    subgraph CT["_confirm_trash: up to 3 dialogs"]
        direction TB
        D0["wait for a visible dialog mentioning trash<br/>(5s for the first, 3s for later ones)"] --> D1{"dialog appeared?"}
        D1 -->|"no, item left the URL"| DR(["return"])
        D1 -->|"no, timeout on the first"| DX["RuntimeError"]
        D1 -->|"no, timeout on a later one"| DR
        D1 -->|"yes"| D2["click its confirm button<br/>Got it / Move to trash"]
        D2 --> D3["wait ≤ 10s for that dialog to close"] --> D0
    end

    CT --> FIN{"within 10s, URL no longer<br/>contains the item id?"}
    FIN -->|"yes"| OK(["trashed ✔"])
    FIN -->|"no"| XF["RuntimeError<br/>trash not confirmed"]
```

> Talking point: Before every click the tool checks that the tab still shows the right photo. A click returning proves nothing. Success means the viewer has left the photo.

## D10. Life of a `MediaRecord` across runs

```mermaid
stateDiagram-v2
    state "not in state file" as None
    state "verified=False" as Unverified
    state "verified=True, deleted=False" as Verified
    state "deleted=True" as Deleted

    [*] --> None : URL found in grid
    None --> Verified : download and verify OK
    None --> Unverified : file missing or empty
    None --> None : download raised, logged only
    Unverified --> Verified : next run retries
    Verified --> Verified : next run, file OK, skipped
    Verified --> Verified : next run, file gone, re-download
    Verified --> Deleted : --delete, move_to_trash OK
    Deleted --> Verified : still seen in grid, so reset and re-check
    Deleted --> [*]
```

> Talking point: The JSON file is the memory between runs. If a record marked deleted shows up in the grid again, the trash never happened, so the item goes back into the pipeline.

## D11. Safety layers (a good closing slide)

```mermaid
mindmap
  root((Never lose a photo))
    Opt-in deletion
      only with the --delete flag
      never empties Trash
    All-or-nothing gate
      one failed download means zero deletions
    Re-verify before deleting
      every file exists and size is above 0
    Right-photo checks
      URL must hold the item id before each click
      already-trashed countdown is detected
    Proof it worked
      viewer must leave the item after confirming
    Fresh tab per delete
      a late click cannot hit the next photo
    Crash-safe notebook
      JSON written to a temp file, then renamed
      rerun skips finished items
    Diagnosable failures
      failures log
      screenshots and visible button labels
```

> Talking point: Close the video with this one. Every design choice exists so that no photo is trashed before a good local copy exists.

## D12. `--delete-all`: `trash_all()` in batches from the grid

```mermaid
flowchart TD
    S(["--delete-all, confirmed"]) --> L["_select_batch(): iter_media_urls()<br/>reloads the grid and scrolls from the top"]
    L --> N{"next URL?"}
    N -->|"grid exhausted"| CHK
    N -->|"url"| AG{"already trashed<br/>in an earlier batch?"}
    AG -->|"yes"| XA["RuntimeError<br/>trash didn't take, stop"]
    AG -->|"no"| ST["_select_tile(): hover the tile<br/>nearest box with ONE checkbox and ONE item<br/>click it · wait for aria-checked=true"]
    ST --> FULL{"200 ticked?"}
    FULL -->|"no"| N
    FULL -->|"yes"| CHK{"anything ticked?"}
    CHK -->|"no: grid is empty"| DONE(["done"])
    CHK -->|"yes"| CNT{"Google's 'N selected'<br/>equals our count?"}
    CNT -->|"no"| XC["RuntimeError<br/>nothing trashed"]
    CNT -->|"yes"| TR["_trash_selection(): click Move to trash<br/>_confirm_trash() clicks through dialogs<br/>wait ≤ 60s for the selection bar to close"]
    TR --> REC["on_trashed(batch):<br/>mark_deleted() for URLs in the state file<br/>deleted += batch size"]
    REC --> L
```

> Talking point: This mode skips every download check on purpose. It ticks items in the grid instead of opening each one, so a batch of 200 costs one trash click. Two checks remain: Google's selected count must match what we ticked, and a photo that shows up again after being trashed stops the run instead of looping forever.
