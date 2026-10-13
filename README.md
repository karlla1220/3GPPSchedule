# 3GPP Schedule Viewer

A GitHub Pages site that shows 3GPP RAN meeting schedules as a Gantt chart, one page per working group.
RAN1, RAN2 and RAN Plenary each have their own pipeline. Each pipeline downloads the chairs' schedule
documents from the 3GPP server, parses them (with rules and, where the documents are free-form, Gemini)
and returns a common `Schedule` model. One shared renderer turns that model into a static HTML page.

The site is rebuilt automatically by GitHub Actions whenever a chair uploads a new version.

## Features

**Viewer**

- One Gantt chart per day, with day tabs. Today's tab opens automatically.
- A NOW line during the meeting, drawn in the venue's time zone.
- Session cells show the title, duration, time range, agenda item (AI) and chair.
  RAN2 cells also show the raw cell text when there is room.
  Click a cell for the full details: group, chair, AI, time, room, notes and the agenda item tree.
- **Filter by agenda item.** Matching follows the AI hierarchy: selecting `10.5.4.1` also keeps `10.5.4.x` and `10.5.4` sessions.
  The filter is stored in the URL (`#filter=…`), so a link shares exactly what you see.
  It is also remembered per WG and meeting.
- **Pinch to zoom the grid** on phones, from "whole day fits the screen" up to 2×, with a detent at 100%.
- **RAN1 chairman agreements panel.** Clicking a RAN1 cell shows the matching section of the latest chair notes below the grid,
  with Word tables, math and figures preserved and changes since the previous version marked.
- The header links to the other WGs' meetings and shows the source files with their versions.
  The page refreshes itself every 5 minutes.

**Pipelines**

- Automatic selection of the current meeting and the latest document version. This covers draft vs. released files and `bis` meetings.
- The meeting folder on the 3GPP server is found by meeting ID, so config does not need editing for each meeting.
- Meeting dates, time zone and start/end times come from the 3GPP Portal.
- Change detection with HTTP/FTP revalidation. Only WGs whose inputs changed are rebuilt.
- HTTPS first, with an anonymous FTP fallback when the 3GPP web server fails.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- A Google Gemini API key ([Google AI Studio](https://aistudio.google.com/apikey)), used for:
  - parsing new RAN1 and RAN Plenary documents,
  - RAN2's fallback.
  
  Without a key, RAN2 uses its rule-based result.
  `--render-only` needs no key and no network.
- Optional: Java 21 for the pinned WMF→SVG converter used by the RAN1 agreements panel (see [Agreements panel](#chairman-agreements-panel)).

## Installation

```bash
git clone https://github.com/karlla1220/3GPPSchedule.git
cd 3GPPSchedule
uv sync
cp .env.example .env   # then set GEMINI_API_KEY
```

## Usage

```bash
# Full pipeline for all enabled WGs: download → parse → render
uv run python main.py

# One WG only; the other WGs keep their saved results
uv run python main.py --wg ran2

# Re-render HTML from saved schedule.json files (no API key, no network)
uv run python main.py --render-only

# Reuse downloaded inputs (Gemini may still be called if its cache misses)
uv run python main.py --no-download

# Build one WG from a local document
uv run python main.py --wg ran1 --local "path/to/RAN1 schedule.docx" --no-download
uv run python main.py --wg ran2 --local "tests/fixtures/ran2/R2_135_Schedule_v11.docx"
uv run python main.py --wg ran-plenary --local "tests/fixtures/ran_plenary/RAN#113 time plan v04.zip"

# Preview the static site
python3 -m http.server 8000 --directory docs
```

To check UI changes without touching `docs/`, render into a copy:

```bash
cp -R docs /tmp/out && uv run python main.py --render-only --output-dir /tmp/out
python3 -m http.server 8765 -d /tmp/out   # http://localhost:8765/ran1/
```

| Option | Description |
|---|---|
| (none) | Download, parse and render every enabled WG |
| `--wg <id>` | WG to rebuild: `ran1`, `ran2`, `ran-plenary` or `all` (default). The navigation of every WG is still regenerated |
| `--local <path>` | Build from a local DOCX (RAN1, RAN2) or timeplan ZIP (RAN Plenary). Requires a single `--wg` |
| `--no-download` | Use the latest downloaded inputs |
| `--render-only` | Render from saved `schedule.json`. No parsing or network access. Fails if the default WG has no saved result |
| `--rebuild-slots` | Clear the selected WG's interpretation state: RAN1 slot state, the RAN2 LLM cache, the RAN Plenary interpretation cache |
| `--output-dir <path>` | Site output folder (default `docs/`) |
| `--site-config <path>` | Site config file (default `site.json`) |

If a WG fails, it keeps its last good `schedule.json`. The other WGs still build, and `main.py` exits with an error code.

`uv run python -m working_groups.ran1.pipeline --output out/schedule.html` still renders RAN1 alone as a single HTML file.
Its default output is `docs/ran1/index.html`, and it does not write the agreements panel data.

### Tests

```bash
uv run pytest -q
```

Run the tests yourself before merging. The scheduled deploy workflow does not run them.
CI runs the tests only in `network-check.yml`.
That workflow runs on manual dispatch and on pushes that touch the 3GPP transport code, the certificates or their tests.
Tests use the real documents in `tests/fixtures/` and fake Gemini responses. They need no network or API key.

## Configuration

### Environment

| Variable | Use |
|---|---|
| `GEMINI_API_KEY` | Gemini API key (RAN1, RAN2 fallback, RAN Plenary) |
| `SCHEDULE_MEETING_SYNC` | RAN1: overrides `meeting_sync` |
| `SCHEDULE_MEETING_SPECIFIC_OVERRIDE` | RAN1: overrides `meeting_specific_override` (JSON array or comma-separated URLs; legacy name `SCHEDULE_MEETING_SPECIFIC`) |
| `SCHEDULE_EXTRA_FOLDERS` | RAN1: JSON array that replaces `extra_folders` |
| `SCHEDULE_EXTRA_FILES` | RAN1: JSON array that replaces `extra_files` |

### `site.json`

```json
{
  "default_wg": "ran1",
  "working_groups": [
    {"id": "ran1", "label": "RAN1", "enabled": true},
    {"id": "ran2", "label": "RAN2", "enabled": true},
    {"id": "ran-plenary", "label": "RAN Plenary", "enabled": true}
  ],
  "presentation": { "...": "see Page header below" }
}
```

- `working_groups[].enabled` (default `true`) decides whether a WG takes part.
  A disabled WG is not imported, checked, parsed or shown in the navigation. Its saved output is kept.
- `default_wg` must be an enabled WG.

### Per-WG `config.json`

Each WG has `working_groups/<wg>/config.json`. `config.example.json` next to it shows every key.

- `meeting_specific` is written by the pipeline (see [Meeting folder discovery](#meeting-folder-discovery)). Do not edit it by hand.
- To pin a meeting folder, use `meeting_specific_override`.
- RAN2 and RAN Plenary also accept a `meetings` map (dates, location, time zone per meeting) and `local_agenda` for offline builds.

## Project structure

```text
main.py / build.py             # local CLI: run WG pipelines, save results, assemble the site
ci.py / check_update.py        # CI: per-WG change detection, plan hand-off, build, commit paths
site.json                      # enabled WGs, default WG, page presentation
working_groups/
  registry.py                  # WG id → lifecycle module (lazy import)
  ran1/
    lifecycle.py               # check / prepare / reset / build adapter
    pipeline.py                # build_schedule(options) → Schedule
    downloader.py / check_update.py / config.py / config.json
    parser.py                  # DOCX table structure, merged cells, room colors
    session_parser.py          # Gemini parsing of time slots (prompts/)
    merger.py / slot_state.py  # multi-source merge, incremental slot state
    agenda_descriptions.py     # agenda item titles and hierarchy
    agenda_labels.py           # AI inference for cells without an AI number
    agreements.py              # chair-note sections per AI for the agreements panel
  ran2/
    lifecycle.py / sources.py  # current meeting via Portal dates, schedule + agenda.csv revalidation
    document.py                # tables, merged cells, tracked changes, text outside the table
    sessions.py                # rule-based cell splitting; title/AI/chair inference
    llm.py / prompts/          # Gemini fallback for uncertain cells and unknown layouts
    pipeline.py / config.json
  ran_plenary/
    lifecycle.py / sources.py  # latest timeplan + agenda selection and revalidation
    document.py                # paragraphs, runs, font colors, strike-through, source ids
    interpreter.py / prompts/  # Gemini interpretation and evidence validation
    pipeline.py / config.json
shared/
  lifecycle.py                 # CheckResult, BuildOptions, WorkingGroup contract
  schedule.py                  # common Schedule model, JSON load/save
  site_config.py               # site.json validation
  renderer.py                  # Gantt chart HTML
  page.py                      # header: navigation, sources, notices, support, creator line
  navigation.py                # WG links, meeting status, default WG selection
  remote_files.py              # 3GPP listings/files, conditional revalidation, .cache/3gpp/
  ftp_transport.py             # HTTPS with anonymous FTP fallback
  meeting_folders.py           # archive meeting folder discovery
  portal_meetings.py           # 3GPP Portal GetMeetings, time zone mapping
  topic_references.py          # RAN Plenary topic list formatting
  docx_html.py / docx_math.py  # OOXML → HTML, OMML → MathML (agreements panel)
  agreement_assets.py          # content-addressed agreement pages + manifest
  metafile_images.py / wmf2svg.py   # WMF/EMF rendering (Java backend optional)
  vendor/                      # vendored OMML converter
templates/                     # schedule.html/.css/.js, agreements-panel.js, support-note.js, favicon.svg
scripts/                       # network check, CA bundle, agreements preview and verification, WMF setup
tests/                         # pytest suite; fixtures/ holds real 3GPP documents
certificates/                  # Sectigo intermediate for 3GPP HTTPS
.github/                       # deploy / pages / network-check workflows, shared cache action
```

Generated and runtime folders:

```text
docs/                          # published site (committed)
  index.html                   # redirects to the ongoing WG
  .build_state.json            # input hashes of the last successful build
  ran1/                        # index.html, schedule.json, .schedule_state.json, slot_state/,
                               # agenda_item_description.json, agenda_label_map/, agreements/,
                               # agreements-panel.js, .extra_files_state.json
  ran2/                        # index.html, schedule.json, .schedule_state.json
  ran-plenary/                 # index.html, schedule.json, .schedule_state.json
downloads/                     # downloaded inputs (only downloads/ran1/extra_files/ is committed)
.cache/                        # 3gpp/ originals, per-WG LLM/parser caches, meeting-folders.json (not committed)
ref_in_manual/ran1/            # optional: manually supplied RAN1 documents (create when needed)
```

Related documents (in Korean):

- [PARSING_ARCHITECTURE.md](PARSING_ARCHITECTURE.md): how RAN1 parsing works.
- [AGREEMENTS_VERIFICATION.md](AGREEMENTS_VERIFICATION.md): agreements panel verification and limits.
- `CLAUDE.md` and `.claude/rules/`: UI design rules and per-component conventions.

## Architecture

### WG pipelines and the common model

WGs are independent. A WG never imports another WG's modules.
The shared runner only calls each WG's `build_schedule(options)` and receives a `shared.schedule.Schedule`.

- `DaySchedule.timeline` carries each day's start and end, slot interval, tick interval, breaks and time blocks.
- Rooms are identified by `RoomInfo.id` / `Session.room_ids`. RAN1's older column numbers are still accepted.
  A session that spans several rooms must use adjacent rooms.
- `Schedule.starts_on` / `ends_on` are ISO dates and `timezone` is the venue's IANA zone.
  `starts_at` / `ends_at` hold the real start and end times with a UTC offset (e.g. `2026-08-24T09:00:00+02:00`).
- `Schedule.source_labels` maps source file names to short labels for the header (`Main v03 · Hiroki v01`).
- Optional fields are read as empty from older snapshots:
  - `topic_references`, `topic_preamble`, `supplements`,
  - `Session.room_scope`, `notes`, `inferred_agenda_items`.

### Lifecycle contract (`shared/lifecycle.py`)

| Member | Responsibility of the WG |
|---|---|
| `check_updates() → CheckResult` | Report changed / unchanged / error with reasons. New files go only to `.ci/transfers/<wg>/` |
| `prepare_build()` | On the fresh build runner, reuse the files handed over from check |
| `build_schedule(BuildOptions) → Schedule` | WG-specific parsing and merging |
| `reset_cache()` | Clear the WG's caches and incremental state for a forced build |
| `input_paths` | Code, config and manual inputs. Content-hashed for change detection |
| `persistent_paths` | Committed outputs, sources and state. Restored to the pre-build state on failure |
| `cache_paths` | Regenerable caches (`.cache/<wg>/`) |

### Adding a WG

1. Create a WG folder with `lifecycle.py` and its processing code.
2. Register the ID and lifecycle module in `working_groups/registry.py`.
3. Add the WG to `site.json` and enable it.

The workflow YAML needs no per-WG job, provided the WG uses the same runtime and credentials.
Use `shared/remote_files.py` and `shared/portal_meetings.py` for all 3GPP access (see [Shared infrastructure](#shared-infrastructure)).

## Site behaviour

- **Root redirect.** `docs/index.html` sends visitors to the WG whose saved meeting matches an `ONGOING` meeting in the Portal.
  - If several are ongoing, `default_wg` wins, then the `working_groups` order.
  - If none is ongoing, the visitor goes to `default_wg`.
  - Demo schedules and stale meetings are never auto-selected.
  - When the Portal is unreachable, or with `--render-only` / `--no-download`, the saved venue dates decide.
  - CI also detects redirect changes caused by meeting status and re-renders without document changes.
- **Navigation.** WG links are plain relative links (`../ran1/`), so direct access, reload and back all work under the GitHub Pages project path.
  The selected meeting is centred; other meetings are grey links on the same line.
- **NOW line.**
  - It is on by default while the browser's clock is within `starts_at ≤ now ≤ ends_at`, unless you turned it off for that meeting.
  - It is off outside that window, and for schedules without valid times or for demos.
  - The page re-checks at the start and end boundaries, every minute, and when the tab becomes visible again.
- **Remembered state.**
  - The day tab and grid zoom are kept in `sessionStorage`.
  - The NOW toggle and the filter are kept in `localStorage`, per WG and meeting.

## Working groups

### RAN1

RAN1 publishes a main schedule from the chair, plus detailed schedules from the vice-chairs (e.g. Hiroki, Sorour) in separate folders.
The pipeline merges all of them.

```
Meetings_3GPP_SYNC/RAN1/Inbox/
├── Chair_notes/     → main schedule (room layout)
├── Hiroki_notes/    → vice-chair detailed schedule
└── Sorour_notes/    → vice-chair detailed schedule
         ↓
    discover_schedule_sources()    # find schedule files (sync folder + meeting folder)
    download_all_schedules()       # download all sources (ZIPs extracted)
         ↓
    parse_docx(main, max_tables=2) # main table structure
    parse_docx(vc, max_tables=None)
         ↓
    lookup_timezone_reference()    # Portal meeting → IANA zone + dates (no LLM)
         ↓
    collect_time_slot_data()       # per (day, time block), de-duplicated
         ↓
    parse_time_slots()             # one Gemini call per slot, sources cross-referenced
    normalize_group_headers() / fill_missing_groups()
         ↓
    agenda descriptions, AI inference, agreements → render
```

Vice-chair tables can have a different structure. They often carry details such as AI numbers (`9.1.1`, `10.3.2`)
that the main schedule lacks. The LLM maps those details onto the right room of the main schedule using the document context.
Slots are parsed concurrently. See [PARSING_ARCHITECTURE.md](PARSING_ARCHITECTURE.md) for the split between deterministic code and LLM.

#### Incremental merge (`docs/ran1/slot_state/`)

Each `(day, time block)` merge result is saved as `docs/ran1/slot_state/{Day}_{TB:02d}.json`.
On the next run, each source's hash is compared with the previous snapshot:

- **STALE**: unchanged. If every source is stale, the previous merge is reused without an LLM call.
- **FRESH / NEW / REMOVED**: changed. The LLM gets only the changed sources' text plus the previous merge (baseline).
  Unchanged sources are left out, so stale details from another source cannot undo a consolidation.

The files are committed. Delete one file to rebuild only that slot from scratch.
Use `--rebuild-slots` to clear them all.

#### Meeting selection

The schedule file is chosen by **meeting ID order plus saved state**, not by upload time alone.

- Regular meetings are ordered by name: `RAN1#124 < RAN1#124bis < RAN1#125`. A late `RAN1#124` upload does not override `124bis`.
- Spellings are normalised into one ID: `RAN1#126bis`, `RAN1#126-bis`, `RAN1#126 bis`, `RAN1#126b` and `TSGR1_126b` are all `ran1#126bis`.
  - A suffix counts only as a separate token (the `b` in `RAN1#126 breakout` is not one).
  - The displayed name comes from the ID.
- The previous `meeting_id` (in `docs/ran1/.schedule_state.json`) is a hint, not a pin.
  If `ran1#125` appears, the build switches to it.
- Irregular meetings (`AH`, `adhoc`, `e`) cannot be ordered by name and are chosen by upload time.
- Within a meeting, the order is **released over draft → version → upload time**.
  - The chair restarts at `v00` when dropping `Draft`, so `… v00` beats `Draft … v03`.
  - `Draft` must be a separate word (`drafting` is not).
  - The same order (`downloader._release_key`) is used for schedules, chair notes, vice-chair schedules, local files and `check_update.py`.
- Auxiliary sources follow the selected main meeting. An old meeting's file uploaded late cannot override the current meeting.

#### Reference files follow the selected meeting

The meeting number comes from the selected main schedule's file name. Other files are used only when they match it.
The decision uses names and paths only, with no LLM.

- **Meeting folder.** The pipeline finds folders such as `tsg_ran/WG1_RL1/TSGR1_126b/` and reads their `Inbox/` and `Agenda/` (see [Meeting folder discovery](#meeting-folder-discovery)).
  - It looks for the earliest unfinished meeting in the Portal and the last built meeting.
  - The built meeting's folder stays until the next meeting's files replace it.
  - If discovery fails, the build continues with the sync folder only.
- **Agenda.** A folder whose name maps to another meeting (`TSGR1_126b` → `ran1#126bis`) is ignored, so the next meeting's agenda never mixes in.
  - Agendas are cached per meeting in `downloads/ran1/Agenda/<meeting>/`.
  - If `docs/ran1/agenda_item_description.json` points to another meeting, it is not attached and is regenerated on the next build that can download.
- **Chair notes and vice-chair schedules.** Only files with the current meeting ID are used.
- **Sync folders appear and disappear with meetings.**
  - A missing chair-notes folder (403) is not a failure.
  - If chair notes vanish briefly from the listing, the published agreements stay.
  - When merging sync and mirror listings, the version beats upload time. A higher version that merely disappears is not a change (`check_update._only_lost_versions`).
  - If a file changes between check and build, the build refreshes the listing and retries once.
- **A meeting change always rebuilds.** If the saved `meeting_id` or `files` refer to another meeting, `check_update.py` reports a change.
  Portal metadata is looked up for the meeting about to be selected.

#### Time zone and meeting dates

- The time zone comes from the Portal row where **both the RAN1 meeting number and the TB id match**.
  - `StartTimeZone` is a display string, so known strings plus `Country` are mapped in code. For example, the European string with `NL` becomes `Europe/Amsterdam`.
  - DST is handled by `ZoneInfo`.
  - The zone is never guessed from a bare GMT offset.
- This path needs no document download or LLM. It also stores `starts_on` / `ends_on` / `starts_at` / `ends_at`.
- When the Portal fails, the zone is unknown or the meeting is out of range, the saved Portal reference for the same meeting is reused.
- Otherwise there is a document fallback:
  - A direct Agenda DOCX is used first. Failing that, the venue is taken from the current chair notes and Gemini decides the zone.
  - If the schedule appears before any reference, the build saves `UTC` with `timezone_status: "pending_timezone_ref"`.
    `check_update.py` rebuilds when a reference appears.
  - Agenda ZIP/CSV files are used for agenda descriptions only, never as a time zone reference.

`docs/ran1/.schedule_state.json` stores:

- the selected `files` per source with upload times,
- `meeting_id`,
- `timezone`, `timezone_status` (`resolved`, `pending_timezone_ref`, `detection_failed`),
- `timezone_ref`: a Portal reference, or the Agenda/chair-notes identifier for the document fallback.

#### External files (`extra_files`, `extra_folders`)

`working_groups/ran1/config.json` can list individual remote files and extra folders:

```json
{
  "extra_folders": [{"url": "https://.../custom/", "person_name": "Alice"}],
  "extra_files": [
    {"url": "https://list.etsi.org/scripts/wa.exe?A3=ind2608C&...", "type": "schedule"},
    {"url": "https://example.org/hiroki_notes.docx", "type": "schedule", "person_name": "Hiroki"},
    {"url": "https://example.org/chair_notes.docx", "type": "chair_notes"}
  ]
}
```

- `type`:
  - `schedule` files join the source set (main, or vice-chair when `person_name` is given).
  - `chair_notes` files are used for venue/time zone detection.
- `is_main` defaults to `true` without `person_name` and `false` with it.
- `name` is a file name fallback.
- Files are downloaded like `curl -OJL` into `downloads/ran1/extra_files/` and committed.
  URL, file name and SHA-256 are recorded in `docs/ran1/.extra_files_state.json`.
- External hosts such as ETSI send no ETag/Last-Modified headers, so change detection uses the committed file's content hash.
  - While the hash matches, neither check nor build downloads the file.
  - An ETSI `wa.exe` URL is new for each message, so a new URL is what signals a change.
  - To force a re-download, delete the cached file or its state entry.
  - Public 3GPP URLs are revalidated through the shared cache instead.
- Files fetched by check are passed to build as a workflow artifact.

#### Chairman agreements panel

Clicking a RAN1 cell shows the section of the latest chair notes for that cell's AI, below the grid.
The panel updates its AI tabs without scrolling, and only the active AI's HTML is fetched.

- The section keeps Agreements, Working assumptions, Proposals and untitled content in order.
  - Long TDoc lists (5+ rows) fold into `▸ N TDocs`.
  - Short context rows such as LS, FL summaries and CR numbers stay.
- Word formatting, tables (at Word's column widths, scaled to fit), math (OMML → MathML) and WMF/EMF figures are preserved.
  Bullets are unified by depth.
- Parts added since the previous chair notes are marked with a green bar. Tabs with changes get a dot.
  The header shows the chair-notes version the panel reflects.
- Cells without an AI number (`MIMO`, `AI/ML`) open the agenda items Gemini inferred from the cell name.
  - Inferred items are used only for the filter and the panel; they never appear as the cell's AI.
  - Judgements are kept per meeting in `docs/ran1/agenda_label_map/<meeting>.jsonl` and never asked again.
  - To correct a judgement, append a line with the same name.
- With Java 21 and `uv run python scripts/setup_wmf2svg.py`, a pinned headless converter renders WMF math. Otherwise a Python renderer is used.
  See [AGREEMENTS_VERIFICATION.md](AGREEMENTS_VERIFICATION.md) for previews and limits.

### RAN2

The RAN2 chairs upload `R2_<meeting>_Schedule_v<NN>.docx` to the `Agenda/` folder each meeting
(`R2_135_Schedule_v11.docx`, `R2_133b_Schedule_v14.docx`, `R2_131bis_Schedule v19.docx`).
The document is hand-written and its layout changes with the author, so the parser **infers conservatively**.

- It assumes only a few common conventions.
- Anything that does not fit is shown verbatim at the bottom of the page, rather than made into sessions or failing the build.
- Where the rules are unsure, Gemini re-reads the cell, and only answers confirmed by the source text are used.

The parser was checked against the real RAN2#131bis–#135bis documents.

#### Source selection

- **Two folders are read together:**
  - `Meetings_3GPP_SYNC/RAN2/Agenda/`, the live copy during the meeting,
  - `tsg_ran/WG2_RL2/TSGR2_<meeting>/Agenda/`, which holds the pre-meeting v00 and `agenda.csv`.

  They do not replace each other: the archive gets the versions from before and after the meeting, and in-meeting versions appear only in the sync folder.
- **Meeting folders.** Archive folders are queried only for the next two unfinished meetings in the Portal and the last built meeting.
  - They are found by meeting ID (see [Meeting folder discovery](#meeting-folder-discovery)).
  - A folder's `Agenda` is opened only when the folder listing contains it.
  - A meeting folder that does not exist yet is treated as empty.
- **The sync folder comes and goes.** On 2026-10-11 its `Agenda/` was removed the day before #135bis.
  - A missing folder (403/404) is normal (`sources.sync_listing`).
  - Only 5xx or connection failures count as failures. Even then the page is built from the meeting folder.
  - During the meeting (Portal dates), such a failure is reported as a RAN2 error.
- **Versions never go backwards within a meeting (`sources.built_copy`).**
  - If the built version disappears and the visible one is the same or lower, the built copy is restored from `.cache/3gpp/`.
  - A higher version is used immediately.
- **The current meeting is the earliest unfinished one in the Portal.**
  - An ongoing #135bis is not abandoned for an early #136 v00.
  - If no unfinished meeting has a schedule, the highest meeting (`#135 < #135bis < #136`) is kept.
- Within a meeting, the highest version wins. On a tie, the sync copy wins.
- `agenda.csv` from the same meeting supplies AI descriptions and legend groups. It is optional.
- Dates, time zone and times come from `config.json` `meetings`, then the Portal (TB 380), then the last successful state for that meeting.
  With none of them the build fails rather than guessing UTC.

#### Reading the table (`document.py`)

- **Which table.** The table with the most time ranges in its first column is the schedule.
  - The row above holds room names (`Brk 1 room` → `Breakout 1`).
  - Full-width rows are days or notes for the day.
- **Tracked changes** are read as accepted: insertions in, deletions and strike-through out. python-docx's `.text` misses `<w:ins>`, so it is not used.
- **Merged cells.** Vertical merges are identified from the XML. A cell spanning two time slots becomes one block.
- **Rows that do not fit.** Rows without a readable time, and rows before the first day, go to "Notes in the schedule table".
  When a day header is blank and the time goes backwards, the next day is assumed and noted.
- **Meeting.** The meeting comes from the file name. The title paragraph (`RAN2-135 Session Schedule`) is used only if the file name has none.

#### Cell rules (`sessions.py`)

- **Splitting.** Leading time markers split a cell: `@12:30`, `@8:30-9:30`, `From 15:30:`, `11:00-12:00 [004] ...`.
  - `8.10 NR20 MIMO` (no colon) is text, and so is a time far from the slot.
  - Times inside sentences (`end by 18:30`) are not interpreted.
- **Headings.** If the first marker equals the cell start, the lines above it are that part's heading.
  - Content after a blank line following an ended part continues from that end time.
  - Headings are not moved to other times.
- **Several rows for one room and slot.** Timed blocks are placed first, and untimed rows fill the remaining time. A room shows one block at a time.
- **AI numbers.** `[8.3]` is an AI. `[6.0.2.1] - [6.0.2.12]` expands to a range.
  - Time budgets after a title (`[0]`, `[1.5]`) and offline numbers (`[004]`, `[xxx]`, `[POST133bis]`) are not AIs.
  - Bare numbers count only on number-only lines (`6.0.2.4, 5.1.3.2`).
- **Chair.** Only a person's name in parentheses is the chair (`(Kyeongin)`, `(Erlin, Kyeongin)`).
  - Companies (`(vivo)`) are not.
  - `CB Kyeongin` stays in the title.
- **Title.** Up to three of the following are joined with ` / `:
  - non-sub-item AI lines,
  - lines with a chair in parentheses,
  - a short first line.

  Otherwise the first line is used.
- **Group (legend color).** It is the top-level item of `agenda.csv` (`6GR Rel-20`, `NR Rel-19`).
  - Offline sessions are `Offline`.
  - Breaks are gaps between slots, named from the document's Breaks paragraph.
- **Raw text is kept.** Every source line stays as the session's notes, in the cell when there is room and in the popup otherwise. A wrong guess loses nothing.

#### LLM fallback (`llm.py`)

Rules come first. Gemini (`model` in `config.json`, default `gemini-3.8-flash`) is used in two cases only.
Without `GEMINI_API_KEY`, or with `llm_fallback: false`, the rule result is used as is.

- **Uncertain cells.** Cells where the rules found any of the following are sent in one request, with the reason and the rule result:
  - times or durations inside sentences,
  - a heading directly above a time marker,
  - an unreadable marker (`@ TBD`),
  - a part with no line to name it.

  In RAN2#131bis–#135bis documents this was 1–23 of 50–63 cells.
- **Unknown layout.** If no table has time ranges in its first column, or no session results, the whole document (paragraphs and cells with ids) is sent.
  The bottom of the page then says so and shows the source text.

Answers are used only when the source text confirms them, checked cell by cell:

- Every non-blank line of the cell is quoted by exactly one session.
- Times are cell boundaries or times written in the cell (5-minute grid, no overlap). Even splits and similar guesses are rejected.
- Title words appear in the quoted lines, chairs are names written in the cell (not companies), and AIs are numbers in the quoted lines.

Failed cells are asked once more with the errors. If they still fail, or the request fails, the rule result is kept.
A failed whole-document interpretation fails the build and keeps the last good result.

- Answers are cached in `.cache/ran2/` by input, prompt and model hash.
- `--rebuild-slots` and `force-deploy` clear this cache.
- A summary is kept in `docs/ran2/.schedule_state.json` under `llm`.

#### Information outside the grid

Content outside the schedule table is shown below the grid under "Additional information", in document order (`Schedule.supplements`).

- Short bold paragraphs are subheadings.
- Tab-aligned paragraphs become tables (Breaks, List of Offline Face to Face discussions).
- Full-width notes inside the table (`Colorful Polo Day`) and rows that could not be placed go to "Notes in the schedule table" with their day.

#### State

- `docs/ran2/.schedule_state.json` records the last successful schedule and `agenda.csv`: URL, SHA-256, HTTP validators, meeting, version and Portal metadata.
- `downloads/ran2/inputs/` holds the last originals (not committed). Check hands its downloads to build via `.ci/transfers/ran2/`.

The real documents (#131bis v19, #135 v11, #135bis v00) and their `agenda.csv` files are in `tests/fixtures/ran2/`.

### RAN Plenary

RAN Plenary reads the chair's `RAN#<n> time plan v<version>.zip`/`.docx` from the Chair folder.

- **Selection.** The order is meeting number → numeric version → listing metadata.
  - It never falls back to an older meeting or version.
  - A ZIP must contain exactly one DOCX of the same meeting and version.
- **The sync Chair folder comes and goes.**
  - Sometimes the folder is missing (403), has no timeplan, or has only versions lower than the built one.
  - In those cases the last built input is restored from `.cache/3gpp/` without error (`sources.built_bundle`).
  - Only a WG that has never been built fails.
- **`agenda.csv`.** It comes from the meeting folder (`tsg_ran/TSG_RAN/TSGR_<n>/Agenda/`) first, then the sync `agenda_url`, then the same meeting's last build.
  An agenda from another meeting is never inherited.

The parser keeps the cell structure rather than flattening it to text. That includes:

- room notes,
- per-run font colors (with theme/style inheritance),
- line breaks and strike-through,
- source ids.

Gemini reads the whole document and interprets venues, per-day room availability and sessions.
Code then validates missing source text, guessed times, room overlaps and availability windows.
On a validation failure the errors are sent back once. If it still fails, the previous result is kept.

- **Breaks.** Meetings during breaks are kept, and break names appear on the time axis.
- **Room scope.** Black general topics use `room_scope="unassigned"` and span every room column for the day. Common blocks are `shared`.
  Neither means every room is actually booked.
- **Main room.** Explicit `Main:` / `in RAN main room` text goes to Main even in black.
  Red emphasis on opening and closing sessions is not a room marker.
- **AIs.** They come only from explicit numbers in the timeplan or from a topic table entry clearly linked to the session.
  `agenda.csv` supplies descriptions. AIs without evidence stay empty.
- **Topic table.** The second, untimed table is an ordered list of topics, documents and AIs (`Schedule.topic_references`).
  - The paragraphs right before it are shown verbatim (`Schedule.topic_preamble`).
  - Highlights, hyperlinks, emphasis and strike-through are kept.
  - Agenda descriptions are shown as an indented tree with their parent items.
  - Formatting-only changes are applied without a new LLM call.

**Dates and time zone.**

- **Portal lookup.**
  - It matches the RAN meeting number (not "ongoing").
  - Known Portal zone strings are mapped in code.
  - Unknown strings are resolved by Gemini from the venue and country, then validated with `ZoneInfo`.
- **Priority.**
  - Explicit config wins.
  - On a Portal failure, only the same meeting's earlier successful metadata is reused.
  - A new meeting without dates fails the build rather than guessing UTC.

For offline or past meetings, set them in `config.json` (full example in `config.example.json`):

```json
{
  "local_agenda": "tests/fixtures/ran_plenary/agenda.csv",
  "meetings": {
    "113": {"starts_on": "2026-09-14", "ends_on": "2026-09-17",
            "location": "Madrid", "country": "ES", "timezone": "Europe/Madrid"}
  }
}
```

**State and caches.**

- `docs/ran-plenary/.schedule_state.json` records the successful timeplan and agenda: URL, meeting, version, SHA-256, validators, date and zone metadata.
- `downloads/ran-plenary/inputs/` holds verifiable originals (not committed).
- `.cache/ran-plenary/` holds LLM results and evidence, keyed by the hash of:
  - structure,
  - colors,
  - room notes,
  - prompt,
  - model.
- A color-only change triggers re-interpretation.
- An agenda-only change reuses the timeplan result and re-attaches descriptions.

The real v04 ZIP, agenda and the reviewed Gemini result are in `tests/fixtures/ran_plenary/`.

## Shared infrastructure

### Remote files (`shared/remote_files.py`)

All 3GPP file access and the per-URL disk cache (`.cache/3gpp/`) go through this module.
WGs only choose files and interpret documents. A new WG uses `get_listing()`, `fetch_file()` or `download()`.
It does not add its own HTTP/FTP retries, and it does not skip a download based on the file name alone.
Portal access is shared through `shared/portal_meetings.py`.

- **HTTP.** A conditional GET uses the cached ETag/Last-Modified. A 304 reuses the cache.
  Responses without validators are downloaded again, so an overwrite under the same name is never missed.
- **FTP.** The cache is verified by SHA-256, then SIZE/MDTM are compared. If they are unchanged, the file is not fetched again (no RETR).
- **Failures.** A failed download never overwrites the previous cache or a WG's successful state. A failed remote check is never reported as "unchanged".
- **Listings.** Each listing URL is fetched once per run. Failed listings are not remembered, and `ci.py check` starts with a clean memo.
- **Hand-off.** Build reuses the listings and Portal rows that check fetched (`.ci/listings.json`, `portal_meetings` in `.ci/plan.json`), so it builds exactly what check saw.
- **Absent vs. failed.** 3GPP's IIS answers 403 for missing paths. `remote_files.is_absent` treats 403, 404 and FTP 550 as "absent". 5xx and connection errors are "failed".
- **Cache is optional.** If the Actions cache is missing or expired, normal downloads restore it.

### HTTPS and FTP fallback (`shared/ftp_transport.py`)

Fallback applies only to public 3GPP file URLs (`www.3gpp.org/ftp/…`, `ftp.3gpp.org`) without credentials, query or fragment.
The Portal API and external hosts are never sent to FTP.

- **Retryable failures** are transport errors and HTTP 500, 502–504 and 520–526. A 200 response carrying an outage page also counts.
  HTTPS is tried twice, 1 s apart, then anonymous passive FTP once (`ftp://ftp.3gpp.org`, same path).
  WGs do not add retries of their own.
- **A 403 from the 3GPP web application firewall** goes straight to FTP without retrying HTTPS.
- **Any other 401/403/404** is returned unchanged as an HTTP response, and callers decide whether it means "absent" (see above).
- **Within one run, a failed HTTPS location goes straight to FTP.** For 5xx or a firewall 403 this applies to the folder.
  For connection or TLS failures it applies to the whole host. The next run starts with HTTPS again.
- **One FTP connection per run** is reused, with one reconnect after an idle disconnect.
- **Limits.** Files are capped at 64 MiB, a silent socket times out after 20 s, and a transfer after 180 s.
  Size and modification time are compared before and after the transfer, to reject truncated files or files that changed mid-transfer.
- **Listings.** Only the Microsoft FTP LIST format is supported.
- **Logging.** State keeps the HTTPS URL, and FTP use is logged. FTP is plaintext and sends no credentials or headers.
- **User-Agent.** Every 3GPP request uses `3GPPSchedule/1.0 (+https://github.com/karlla1220/3GPPSchedule)`.

**Certificates.** The 3GPP server sometimes omits its intermediate certificate.
CI runs `scripts/prepare_ca_bundle.py`, which adds the verified Sectigo intermediate to certifi's bundle and sets `SSL_CERT_FILE`.
TLS and hostname verification stay on. See [certificates/README.md](certificates/README.md).

To verify the transport locally without deploying:

```bash
uv run python scripts/prepare_ca_bundle.py /tmp/3gpp-ca-bundle.pem
SSL_CERT_FILE=/tmp/3gpp-ca-bundle.pem uv run python scripts/check_3gpp_network.py
```

The **Verify 3GPP network recovery** workflow does the same check:

- It injects HTTP 526, downloads real listings and files over FTP, and compares them with HTTPS by SHA-256.
- It changes no schedule data and no site files.

### Meeting folder discovery (`shared/meeting_folders.py`)

Each WG reads two kinds of folder:

- the sync folder, with live versions during the meeting,
- the archive meeting folder, with versions from before and after the meeting and `agenda.csv`.

The meeting folder is found by meeting ID, so nothing needs editing when the meeting changes. For each meeting:

1. `meeting_specific_override` in the WG's `config.json`. It is set by hand and applies to that meeting only.
2. The Portal's `MtgDocURL`. It is already in the per-run Portal list, so no request is needed.
   - `ftp.3gpp.org` is rewritten to `www.3gpp.org/ftp`.
   - Subfolders such as `Docs/` are lifted to the meeting folder.
   - A URL naming another meeting is ignored.
3. The folder recorded by a previous run (`meeting_specific`).
4. A direct lookup of the folder name built from the ID: RAN1 `TSGR1_126b` / `TSGR1_126bis`, RAN2 `TSGR2_135bis` / `TSGR2_135b`, RAN Plenary `TSGR_113`.
5. A search of the WG root listing, which also accepts suffixes such as `-e`.
   The listing has hundreds of entries, so it is fetched at most once per meeting every 6 hours (timestamp in `.cache/meeting-folders.json`).

- **Found folders are recorded in `meeting_specific` and committed.**
  - Each check rewrites the list with that run's findings: the next meetings and the built meeting.
  - When the list changes, that WG is built and the list is committed with the result. The check reason reads `meeting folders now …`.
  - Meetings with an override are discovered and recorded too, but only the override is used. Maintainers compare the two to decide whether to keep the override.
  - RAN2 and RAN Plenary builds reuse the list check wrote, via `.ci/plan.json`.
  - A local checkout without the files records nothing.
- **Effect on hashes and caches.**
  - The list is excluded from the WG input fingerprint (`ci.py`), so recording it is not a "config change" and does not invalidate check's plan.
  - `meeting_specific` and `meeting_specific_override` are excluded from `config_hash`, so parser and LLM caches survive a folder change.
- **Missing is normal.** A folder that does not exist yet (IIS 403, FTP 550) is not recorded and is looked up again on the next run.
  5xx or connection errors are errors.

### Caches

"Cache" here means three different things:

1. **GitHub Actions cache: 3GPP originals and WG parser/LLM results (`.cache/`).**
   - The [shared cache action](.github/actions/wg-cache/action.yml) restores and saves the whole `.cache/` directory for both check and build.
   - `.cache/3gpp/` stores originals per URL with file name, size, SHA-256, ETag, Last-Modified and FTP modification time.
   - `.cache/<wg>/` stores per-WG results.
   - The key is `wg-cache-v2-<OS>-<run_id>-<run_attempt>-<job>`.
     Build prefers the same run's check cache, then the latest earlier cache, then legacy `wg-cache-v1-`.
   - Check saves the cache even when nothing changed.
2. **Git-committed inputs.**
   - These are `downloads/ran1/extra_files/` with its `.extra_files_state.json`, and the `meeting_specific` lists.
   - They survive runner changes and cache expiry, at the cost of repository size.
3. **uv package cache** (`setup-uv` `enable-cache: true`). It holds Python dependencies only.

`force-deploy` clears the shared 3GPP cache, then calls every enabled WG's `reset_cache()`.

## Deployment (GitHub Actions)

`ci.py` is the production entry point. It uses the same WG registry, site config and renderer as `main.py`.

```text
repository_dispatch (cronjob_trigger) / workflow_dispatch
  → ci.py check   per-WG check, reasons and errors recorded
  → .ci/plan.json + .ci/transfers/<wg>/ passed as an artifact
  → ci.py build   prepare and build only changed WGs, assemble the site
  → deploy to Pages only when published files changed
  → commit successful WGs' outputs and state ("Update WG schedules [skip ci]")
```

- **Schedule.** The schedule is set by an external scheduler that sends `repository_dispatch`. The workflows contain no cron.
- **Environment.** Check and build use the `github-pages` environment.
  `deploy.yml` and `pages.yml` share the `pages` concurrency group, so a new run never cancels a running parse or deploy.
- **Java.** The build job installs Java 21 and the WMF converter.

| `action` input | Behaviour |
|---|---|
| `check-build-deploy` (default) | Check enabled WGs, build only those that changed, deploy if published output changed |
| `build-deploy` | Skip checks and build all enabled WGs |
| `force-deploy` | Clear every enabled WG's caches, then build everything |
| `deploy-only` | Deploy the current `docs/` without parsing |

Rebuild triggers:

- A WG is rebuilt when its documents, Portal metadata, code or config change.
- A change to the shared model or dependencies rebuilds every enabled WG.
- A change to the shared screen (templates, renderer) or to `default_wg` only re-renders from saved schedules.
- Navigation changes caused by meeting dates are detected too.

Run the same steps locally from the repository root:

```bash
uv run python ci.py check
uv run python ci.py build
uv run python ci.py build --action build-deploy
uv run python ci.py build --action force-deploy
```

`ci.py check/build` collects per-WG failures and prints `has_errors`. The workflow fails on it, so check the report when you run it yourself.
Config or plan errors exit abnormally. Build rejects a plan if code or config changed after the plan was made.

### State and failure handling

- `.ci/plan.json`: `build_ids`, per-WG `changed/unchanged/error`, reasons and input hashes.
- `.ci/build-report.json`: per-WG `built/skipped/failed`, `site_changed`, `has_errors` and the paths to commit.
- `docs/.build_state.json`: input hashes of the last successful build.
- **Failed WG.**
  - Its declared persistent paths are restored, so partial slot or document state is never recorded as success.
  - Other WGs' results are still deployed, and the workflow is marked failed at the end.
  - A new WG with no previous result is left out of the navigation. A missing default WG fails the site build.
- **Unchanged schedule.** If only document metadata changed, the generation time is kept to avoid a needless deploy.
  If only state changed, it is committed without deploying.
- **Failed Pages deploy.** The new state is not committed, so the next run detects the same change again.
- **RAN1.** A build fails if every LLM retry fails or a selected source cannot be downloaded. Empty slots or missing vice-chair data are never saved as success.

### Repository settings

- Set `GEMINI_API_KEY` in the `github-pages` environment (or repository secrets).
- Settings → Pages → Source: **GitHub Actions**.
- `pages.yml` deploys the committed `docs/`. It runs on manual dispatch, or on a push to `main` touching `docs/**` (bot commits carry `[skip ci]`).

## Page header (`site.json` `presentation`)

`presentation` applies to every WG page:

```json
"presentation": {
  "creator": "Duckhyun Bae",
  "creator_url": "https://www.linkedin.com/in/duckhyun-bae/",
  "creator_bio": "RAN1 delegate from LGE",
  "contact_email": "duckhyun.bae@lge.com",
  "disclaimer": "Personal side project · Not affiliated with LG Electronics or 3GPP",
  "feedback_url": "https://github.com/karlla1220/3GPPSchedule/issues",
  "support_title": "Proposal 1: Buy a coffee for Duckhyun",
  "support_links": [
    {"label": "Patreon", "url": "https://www.patreon.com/duckhyunbae"},
    {"label": "GitHub Sponsors", "url": "https://github.com/sponsors/karlla1220"}
  ]
}
```

```
                    RAN2#135bis   📅 RAN1#126bis   RAN#113        ☕ Proposal 1: Buy a coffee for Duckhyun
                                                                     on Patreon or GitHub Sponsors
Created by Duckhyun Bae (duckhyun.bae@lge.com), RAN1 delegate from LGE
Personal side project · Not affiliated with LG Electronics or 3GPP · Feedback
```

- `creator_url` links the name, and `feedback_url` links `Feedback`. An empty value hides that link or line.
- **Support links.** `support_links` are joined in order as `A or B` (`A, B or C`). `support_title` is the first line (default `Support this tool`).
  - At 1024px and below, the note moves to one centred line under the meeting title.
  - Only `https://` URLs are accepted.
  - The repository's Sponsor button comes from `.github/FUNDING.yml`.
- **End-of-meeting note.** With `support_links` set, a short thank-you appears under the notice from noon (venue time) on the last day of the meeting.
  - Dismissing it hides it for that meeting (`localStorage`).
  - It is not shown for demos or for meetings with an unknown end date.
- **Contact.** `contact_email` is shown in parentheses after the creator's name. If `contact_name` differs from `creator`, a separate `Contact:` line is used.
- **Notice.**
  - Without `notice`, the default notice is shown. An empty string hides it.
  - Demo meetings always show a demo notice.
- **Safety.** Email addresses and URLs are validated at build time, and all text is HTML-escaped.

`shared/page.py`'s `render_header()` builds:

- the WG navigation,
- the sources and generation time,
- the notice and the end-of-meeting note,
- the creator lines.

`shared/renderer.py` combines the header with the schedule body. A new layout can reuse `render_header(schedule, presentation=..., schedules=..., groups=...)`.

WG parsers return schedule data only. Changing `presentation` only re-renders HTML in CI and never re-downloads or re-parses documents.
The tab icon is `templates/favicon.svg`, embedded as a data URI.

## Feedback

Send an email to duckhyun.bae@lge.com or open an [issue](https://github.com/karlla1220/3GPPSchedule/issues).

## License

MIT
