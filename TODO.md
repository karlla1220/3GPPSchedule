# TODO

## Goal
`config.json` 이 addition: `extra_files` 키. 외부 URL 파일 httpx로 다운로드 (curl -OJL equivalent: redirect follow + Content-Disposition filename) → schedule/chair_notes 파이프라인 참조.

## Design (fixed, verified against codebase)

- **config.json**:
  ```json
  { "extra_files": [
      { "url": "https://...", "type": "schedule" },
      { "url": "https://...", "type": "schedule", "person_name": "Hiroki" },
      { "url": "https://...", "type": "chair_notes" }
  ] }
  ```
  - Entry: `{url (required), type (required: "schedule"|"chair_notes"), name?, person_name?, is_main?}`. invalid → warning + skip.
  - `is_main` 자동 구성: 생략 시 `person_name` 없으면 `true`(main), 있으면 `false`(vice-chair). 명시 bool은 항상 우선.
  - env override: `SCHEDULE_EXTRA_FILES` (JSON array) — env 우선.
- **저장 로직**: `downloads/extra_files/`를 Git에 커밋하고, `docs/.extra_files_state.json`에 URL → `{sha256, filename}`을 기록한다. 기록된 파일이 존재하고 SHA-256이 일치하면 check/build 모두 재다운로드하지 않는다. check에서 새로 받은 cache miss는 workflow artifact로 build job에 전달한다.
- **변경 감지**: 캐시가 없거나 해시가 불일치한 URL만 streaming GET으로 확인한다. ETag/Last-Modified/length는 사용하지 않는다 — ETSI는 ETag/Last-Modified를 제공하지 않음(실측: Content-Length만). 404/transport 에러 → 경고 후 **해당 URL 무시** (changed에 영향 없음, state에도 저장 안 됨).
  - build job: check artifact 또는 원격 download + successful URL들의 hash만 state로 저장 → stale URL은 자동 소거.
  - state: `docs/.extra_files_state.json` (commit).
  - state에 없는 새 URL(첫 등장/URL 교체) → changed (정상 동작 — ETSI wa.exe URL은 메시지마다 새로 생성됨).
- **--no-download**: don't download. `ref_in_manual` 스캔 다음 `find_local_schedule_sources(ref_dir=EXTRA_FILES_DIR)`, chair notes는 `find_chair_notes_docx(EXTRA_FILES_DIR)` — 기존 local 폴더 스캔 함수 재사용 (별도의 이름 헤uris틱 없음).
- **Schedule integration**: download는 **저장만** (folder scan이 주워먹도록). main.py가 type=schedule entry마다 local ScheduleSource(`folder_name=EXTRA_FILES_DIR.name`, `local_path=path`, is_main은 config auto-derive)를 만들어 `local_schedule_sources`에 합침 — meeting filter/dedup에서 local 우선 (existing).
- **Chair notes integration**: download branch는 type=chair_notes entry의 path 직접 사용; --no-download는 `find_chair_notes_docx(EXTRA_FILES_DIR)`. tz block에서 FTP download 전에 확인.
- **subagent + git worktree**: W1 config, W2 downloader, W3 main+check_update.

## Tasks

### 1. W1 — config.py + example + README (worktree: orca/w1-extrafiles-config) ✅ DONE (staged, 110 tests green)
- [x] `_normalize_extra_file()` — url+type validation, is_main auto-derive (없으면 True / person_name 있으면 False, 명시 bool 우선)
- [x] `load_config()`: `extra_files` JSON parse, `SCHEDULE_EXTRA_FILES` env override, return dict + docstring
- [x] `config.example.json` — extra_files example
- [x] README — extra_files section
- [x] `tests/test_config.py` — 7 tests (missing key, round-trip default, is_main auto-derive, invalid skip, mixed, env override, invalid env JSON)

### 2. W2 — downloader.py (worktree: orca/w2-extrafiles-downloader) ✅ DONE (90 tests green)
- [x] `download_external_files(extra_files, dest_dir=EXTRA_FILES_DIR) -> (list[(entry, Path)], dict{url: sha256})` — **다운로드만**. httpx.stream GET, follow_redirects, chunk write
  - filename: Content-Disposition (`filename="..."` / `filename*=UTF-8''...`) → URL path last segment (unquote) → entry name → `external_N`; sanitize
  - `_validate_downloaded_file` (+ <4KB error page)
  - `.zip` → `extract_document_from_zip` (zip + unpacked doc 둘 다 반환 list에)
  - `entry`를 path와 페어로 반환 — type 라우팅/ScheduleSource 생성은 main.py 책임 (단순화)
- [x] `check_external_files(extra_files, state=None, cache_dir=…, staging_dir=None) -> tuple[bool, dict]` — per-URL GET stream (chunk별 sha256), state의 hash와 비교 → changed. `staging_dir`가 있으면 cache miss 파일을 workflow artifact용으로 저장한다. 404/transport 에러 → 경고 후 skip (결과에 영향 없음). state 없는 새 URL → changed. 반환 dict는 successful URL들만 포함 (stale 자동 소거)
- [x] `load_external_files_state` / `save_external_files_state` (`docs/.extra_files_state.json`)
- [x] `tests/test_downloader.py` — filename detection (CD, URL fallback, sanitize), pair routing, hash state compare (hash differ → changed, 404 무시, new URL → changed), mock httpx

### 3. W3 — main.py + check_update.py (worktree: orca/w3-extrafiles-main, W1+W2 통합) ✅ DONE (157 tests green, 전체 스위트)
- [x] main.py: before discover, `download_external_files(cfg["extra_files"])` (not no_download) → `save_external_files_state({"files": state})`; type=schedule은 main이 local ScheduleSource로 만들어 `local_schedule_sources` 합침, type=chair_notes는 `extra_chair_notes_paths`에 저장
- [x] main.py --no-download: `find_local_schedule_sources(ref_dir=EXTRA_FILES_DIR)` (ref_in_manual 다음) + `find_chair_notes_docx(EXTRA_FILES_DIR)`
- [x] main.py tz block: `extra_chair_notes_paths` (filename/version/hash-stable) FTP download보다 우선
- [x] check_update.py: `check_external_files` (sha256 compare) 결과를 FTP 변경 여부와 합산; cache miss 파일은 workflow artifact용 staging 디렉터리에 저장; 일시적인 FTP/URL 오류는 별도로 경고하고 다음 정상 확인을 허용
- [x] local main meeting은 `preferred` floor와 별도로 exact lock 처리; check/build가 ref_in_manual 및 local extra schedule의 회의를 다르게 선택하지 않도록 `meeting_source`를 state에 저장
- [x] `extra_files`를 config에서 모두 제거한 경우 stale `.extra_files_state.json`도 변경으로 감지하고, 다음 성공 빌드에서 비움
- [x] `tests/test_main.py` — 3 wiring tests (schedule merge, tz priority, --no-download scan)
- [x] all merge + full test suite green (157 passed)

## Interface (fixed, from W1/W2/W3)
- `load_config()["extra_files"]` → `[{url, type, name, person_name, is_main}]` (is_main auto-derive)
- `download_external_files(extra_files, dest_dir=EXTRA_FILES_DIR) -> (list[(entry, Path)], dict{url: {sha256, filename}})` — 다운로드만, 라우팅 없음; legacy hash-only state도 읽음
- `check_external_files(extra_files, state=None, cache_dir=…, staging_dir=None) -> (bool, dict{"files": {url: {sha256, filename}}, "config": [...]})` — filename/hash와 routing metadata 비교; optional staging
- `load_external_files_state(state_path=EXTRA_FILES_STATE_PATH) -> {"files": {url: sha256}}` / `save_external_files_state(state, state_path=…)`
- `EXTRA_FILES_DIR = DOWNLOADS_DIR/"extra_files"`, `EXTRA_FILES_STATE_PATH = Path("docs/.extra_files_state.json")`

## Notes
- `local_path` source: `_filter_sources_to_meeting` always keep, `_dedup_sources` local 우세 — existing logic reuse.
- `.gitignore`: 일반 `downloads/`는 무시하되 `downloads/extra_files/`만 커밋한다. `*.tmp`는 계속 무시한다.
- 캐시 miss/불일치 시에만 check job이 헤더가 아닌 전 본체를 streaming GET한다 (수백 KB 범위).
- Error (404/삭제/transport) URL → warning + ignore; changed에도 state에도 영향 없음. ETSI wa.exe URL은 메시지마다 새로 생성되므로 config 갱신 = 새 URL = state 미존재 → changed(정상).
- 동일 URL의 원격 본문이 URL 변경 없이 바뀌는 경우에는 커밋된 캐시만으로 감지할 수 없다. ETSI `wa.exe`는 메시지마다 새 URL을 생성하므로 URL 변경을 주된 변경 신호로 사용한다.
