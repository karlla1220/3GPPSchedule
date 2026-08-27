# 3GPP Schedule Viewer

3GPP FTP 서버에서 최신 회의 스케줄 DOCX 파일을 다운로드하고, Gemini API로 비정형 테이블 텍스트를 파싱하여 **CSS Grid 기반 간트차트 스타일의 정적 HTML 페이지**를 생성합니다.

## 주요 기능

- 3GPP FTP에서 최신 스케줄 DOCX 자동 다운로드 (ZIP 내 문서 자동 추출 지원)
- **다중 소스 스케줄 통합**: Chair_notes 외 부의장(Hiroki, Sorour 등) 폴더의 스케줄도 자동 탐색·다운로드
- **미팅 우선순위 인식**: 정규 미팅은 `RAN1#124 < RAN1#124bis < RAN1#125` 순으로 비교하고, 비정규 미팅(AH/e/기타)은 업로드 시각 기준으로 판단
- `python-docx`로 테이블 구조 추출 및 병합 셀 처리 (TextBox 색상 기반 방 매칭)
- Gemini API를 사용한 비정형 텍스트 → 구조화 세션 데이터 변환 (결과 캐싱)
- **다중 소스 크로스레퍼런스**: 같은 시간대의 여러 스케줄 테이블을 하나의 LLM 호출로 통합하여 가장 상세한 세션 정보(AI 번호 등) 도출
- **회의 시간대 자동 감지**: Agenda DOCX 또는 Chair notes DOCX/DOCM의 OOXML에서 개최지 정보를 추출하여 IANA 타임존 자동 설정
- 요일별 탭 전환, 오늘 날짜 자동 선택되는 단일 HTML 간트차트 생성 (그룹별 색상, 자동 새로고침)
- GitHub Actions를 통한 자동 빌드 및 GitHub Pages 배포 (평일 5분 간격 변경 감지)

## 요구사항

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) 패키지 매니저
- Google Gemini API 키 ([Google AI Studio](https://aistudio.google.com/apikey)에서 발급)

## 설치

```bash
# 저장소 클론
git clone https://github.com/<your-username>/3GPPSchedule.git
cd 3GPPSchedule

# 의존성 설치
uv sync
```

## 환경 설정

프로젝트 루트에 `.env` 파일을 생성하고 필요한 환경 변수를 설정합니다:

```bash
cp .env.example .env
```

```dotenv
GEMINI_API_KEY=your-api-key-here
SCHEDULE_CONTACT_NAME=Your Name
SCHEDULE_CONTACT_EMAIL=your.email@example.com
```

- `GEMINI_API_KEY`: Gemini API 키 ([Google AI Studio](https://aistudio.google.com/apikey)에서 발급)
- `SCHEDULE_CONTACT_NAME`: 생성된 HTML에 표시될 담당자 이름
- `SCHEDULE_CONTACT_EMAIL`: 생성된 HTML에 표시될 담당자 이메일

## 사용법

### 전체 파이프라인 (다운로드 → 파싱 → HTML 생성)

```bash
uv run python main.py
```

3GPP FTP에서 최신 스케줄 파일을 다운로드하고, 파싱 후 `docs/index.html`을 생성합니다.

### 로컬 DOCX 파일 사용

```bash
uv run python main.py --local "Chair_notes/RAN1#124 online and offline schedules - v00.docx"
```

이미 다운로드된 DOCX 파일을 직접 지정하여 HTML을 생성합니다.

### 다운로드 건너뛰기

```bash
uv run python main.py --no-download
```

FTP 다운로드를 건너뛰고 `downloads/Chair_notes/` 폴더에 있는 가장 최신 로컬 파일을 사용합니다.

### 출력 경로 지정

```bash
uv run python main.py --output output/schedule.html
```

기본 출력 경로는 `docs/index.html`입니다.

## CLI 옵션 요약

| 옵션 | 설명 |
|---|---|
| (없음) | FTP 다운로드 → 파싱 → HTML 생성 전체 파이프라인 |
| `--local <path>` | 지정한 로컬 DOCX 파일로 HTML 생성 |
| `--no-download` | 다운로드 없이 최신 로컬 파일 사용 |
| `--output <path>` | HTML 출력 경로 (기본: `docs/index.html`) |
| `--rebuild-slots` | `docs/slot_state/` 전체 삭제 후 모든 시간 슬롯을 cold 경로로 재빌드 |

## 프로젝트 구조

```
main.py             # CLI 진입점, 전체 파이프라인 오케스트레이션
downloader.py       # 3GPP FTP에서 스케줄 DOCX 다운로드 (다중 폴더 탐색, ZIP 자동 추출)
parser.py           # python-docx로 DOCX 테이블 구조 추출 (TextBox 색상 매칭, 회의 장소 추출)
merger.py           # 다중 소스 스케줄 데이터를 (day, time_block) 단위로 수집·통합
session_parser.py   # Gemini API로 셀 텍스트 → 세션 데이터 파싱 (타임존 감지, 방 매칭, 그룹 정규화)
models.py           # 데이터 모델 (Session, DaySchedule, Schedule, ScheduleSource 등)
generator.py        # 스케줄 데이터 기반 동적 마크업 생성
templates/          # 페이지 골격, CSS, JavaScript 소스 템플릿
check_update.py     # FTP 변경 감지 (GitHub Actions cron용, 다중 폴더 비교)
.env.example        # 환경 변수 템플릿
pyproject.toml      # 프로젝트 의존성 (uv)
docs/
  index.html        # 생성된 정적 사이트 (GitHub Pages 배포용)
  .schedule_state.json  # FTP 변경 감지 상태 캐시
.github/workflows/
  deploy.yml        # 스케줄 빌드 및 배포 워크플로우 (변경 감지 + 빌드 + Pages 배포)
  pages.yml         # docs/ 변경 시 GitHub Pages 자동 배포
```

## 다중 소스 통합 파이프라인

```
FTP Inbox/
├── Chair_notes/     → 메인 스케줄 (방 레이아웃 기준)
├── Hiroki_notes/    → 부의장 상세 스케줄
└── Sorour_notes/    → 부의장 상세 스케줄
         ↓
    discover_schedule_sources()    # Inbox/ 폴더 탐색, 스케줄 파일 발견
    download_all_schedules()       # 모든 소스 다운로드 (ZIP 자동 추출)
         ↓
    parse_docx(main, max_tables=2)         # 메인 테이블 구조 추출
    parse_docx(vc, max_tables=None)        # 부의장 전체 테이블 추출
         ↓
    extract_meeting_location()     # Chair notes에서 개최지 추출
    get_timezone_from_location()   # Gemini로 IANA 타임존 결정
         ↓
    collect_time_slot_data()       # (day, time_block)별 데이터 수집 + 중복 제거
         ↓
    parse_time_slots()             # 시간대별 1회 Gemini 호출 → 통합 세션 리스트
    normalize_group_headers()      # 그룹명 정규화
    fill_missing_groups()          # 누락된 그룹 이름 보완
         ↓
    save_html()                    # CSS Grid 간트차트 생성
```

부의장 스케줄은 메인 스케줄과 다른 테이블 구조를 가질 수 있으며, AI 번호(예: 9.1.1, 10.3.2) 같은 상세 정보를 포함합니다. 시스템은 LLM과 문서 컨텍스트를 활용하여 부의장 상세 정보를 메인 스케줄의 올바른 방에 매핑합니다.

## 점진적 머지(Incremental Merge)와 `docs/slot_state/`

각 `(요일, 시간 블록)` 슬롯의 머지 결과는 `docs/slot_state/{Day}_{TB:02d}.json` 으로 저장됩니다. 다음 실행에서 각 소스의 해시를 직전 스냅샷과 비교해 다음 중 하나로 분류합니다.

- **STALE** — 이전과 동일한 내용. 모든 소스가 STALE이면 LLM 호출 없이 직전 머지 결과를 그대로 재사용합니다.
- **FRESH / NEW / REMOVED** — 변경이 감지됨. LLM에는 *변경된 소스의 원문* 과 *직전 머지 결과(baseline)* 만 전달되며, 변경되지 않은 STALE 소스의 원문은 프롬프트에서 제외됩니다. 이렇게 하면 한 소스가 항목을 합쳐버린 변경(consolidation)을 다른 소스의 오래된 상세 정보가 되살리는 회귀를 막을 수 있습니다.

이 파일들은 깃으로 추적되므로 GitHub 웹 UI에서 한 파일만 지우면 해당 슬롯만 다음 실행에서 cold 경로로 재빌드되고, 나머지 슬롯은 그대로 점진적/숏-서킷 경로를 탑니다. 누적된 carry-forward 오류 등으로 전체를 다시 빌드해야 한다면 `python main.py --rebuild-slots` 로 디렉터리 전체를 비울 수 있습니다.

## 미팅 선택 규칙

스케줄 파일 선택은 단순 업로드 시각 기준이 아니라, **미팅 ID 우선순위 + 상태 캐시**를 함께 사용합니다.

- 정규 plenary 미팅은 이름 순서를 해석합니다.
     - 예: `RAN1#124 < RAN1#124bis < RAN1#125`
     - 따라서 `RAN1#124` 파일이 더 늦게 올라와도, `RAN1#124bis`가 이미 존재하면 `124bis`가 우선입니다.
- 이전 실행의 `meeting_id`는 `docs/.schedule_state.json`에 저장되며, 다음 실행에서 **현재 미팅 힌트**로 사용됩니다.
- 다만 이 힌트는 고정값이 아닙니다.
     - 캐시에 `ran1#124bis`가 있어도 FTP에서 `ran1#125`가 나타나면, 새 실행은 `125`로 자동 전환하고 state도 갱신합니다.
- `AH`, `adhoc`, `e` 등 **비정규 미팅**은 이름만으로 전체 순서를 확정할 수 없으므로 업로드 시각을 기준으로 선택합니다.
- Vice-chair/Hiroki/Sorour 같은 보조 소스도 가능한 한 현재 메인 미팅에 맞춰 정렬되며, 같은 폴더 안의 오래된 미팅 파일이 늦게 업로드되어도 현재 미팅을 덮어쓰지 못합니다.

## 외부 파일 참조 (`extra_files`)

`config.json`의 `extra_files`에는 폴더가 아닌 **개별 원격 파일 URL**을 나열할 수 있습니다.

```json
{
  "extra_files": [
    {"url": "https://list.etsi.org/scripts/wa.exe?A3=ind2608C&...", "type": "schedule"},
    {"url": "https://example.org/hiroki_notes.docx", "type": "schedule", "person_name": "Hiroki"},
    {"url": "https://example.org/chair_notes.docx", "type": "chair_notes"}
  ]
}
```

각 엔트리의 필드:

- `url` (필수): 다운로드 대상 파일 URL
- `type` (필수): `schedule` 또는 `chair_notes`
  - `schedule` → 로컬 제공 스케줄 소스(`ScheduleSource`)로 주입되어 main/vice-chair 중복 제거 규칙에 참여합니다. `is_main: true`면 메인 스케줄로, `person_name`을 주면 해당 부의장 소스로 취급됩니다.
  - `chair_notes` → 다운로드된 Chair notes 문서는 미팅 개최지/타임존 감지에 사용됩니다.
- `name` (선택): 파일명 폴백 — `Content-Disposition`, URL 경로에 파일명이 없을 때만 사용
- `person_name` (선택): 명시 시 부의장(vice-chair) 소스
- `is_main` (선택): 생략 시 **자동 구성** — `person_name`이 있으면 `false`, 없으면 `true`. 명시한(bool) 값은 항상 우선

빌드 시 `curl -OJL`과 등가 동작(redirect follow + Content-Disposition 기반 파일명)으로 `downloads/extra_files/`에 저장하고, 해당 파일도 Git에 커밋합니다. 파일명은 `Content-Disposition` → URL 경로 마지막 세그먼트 → `name` 필드 → 생성 순서로 결정됩니다. `docs/.extra_files_state.json`에는 URL별 다운로드 파일명과 SHA-256을 기록합니다.

기록된 URL의 파일명이 존재하고 로컬 파일의 SHA-256이 기록값과 같으면 check와 build 모두 네트워크 다운로드를 생략합니다. 상태가 없거나 파일이 삭제·변조된 경우에만 원격 파일을 다시 받아 캐시를 복구합니다.

CI 변경 감지는 각 URL에 대응하는 커밋된 파일의 **콘텐츠 sha256**을 `docs/.extra_files_state.json`과 비교해 동작합니다(`check_update.py`). 헤더(ETag/Last-Modified) 비교는 ETSI가 해당 헤더를 제공하지 않아 사용하지 않으며, `ref_in_manual`과 동일한 콘텐츠 해시 방식을 따릅니다.

CI에서 외부 파일 요청은 다음과 같이 동작합니다.

- `check` job은 먼저 커밋된 `downloads/extra_files/`의 파일을 기록된 SHA-256과 비교합니다. 일치하면 원격 URL을 요청하지 않습니다.
- URL이 새로 추가되었거나 캐시 파일이 없거나 해시가 다르면 `check` job이 원격 파일을 다운로드해 변경 여부를 확인하고 build를 트리거합니다.
- 변경이 감지되면 `check` job과 별도의 새 runner에서 `build` job이 실행됩니다. check에서 새로 받은 파일은 workflow artifact로 build job에 전달되므로 build에서 다시 다운로드하지 않으며, 성공한 build는 `docs/`와 `downloads/extra_files/`를 함께 커밋합니다.
- 이후 workflow의 check/build는 커밋된 파일과 해시가 일치하는 동안 외부 파일을 다시 다운로드하지 않습니다.
- 동일 URL의 원격 본문이 URL 변경 없이 바뀌는 경우에는 로컬 캐시만으로 알 수 없습니다. ETSI `wa.exe` URL은 메시지마다 새로 생성되므로 새 URL이 이 변경을 감지하는 기준입니다. 원격 재확인이 필요하면 해당 캐시 파일 또는 상태 항목을 삭제하고 build를 실행합니다.
- build 실패 시 새 상태와 캐시 파일은 커밋되지 않아 다음 실행에서 이전 상태를 기준으로 다시 확인합니다.

### 캐시의 종류와 저장 위치

이 프로젝트에서 `cache`라는 표현은 서로 다른 세 가지를 가리킬 수 있습니다.

1. **GitHub Actions 서비스 캐시 — LLM 결과**
     - [deploy.yml](.github/workflows/deploy.yml)의 `actions/cache@v4`가 `.cache/` 디렉터리를 GitHub Actions 캐시 서비스에 저장합니다.
     - `session_parser.py`의 Gemini/LLM 결과 재사용을 위한 캐시이며, `extra_files` 원문 파일과는 관계가 없습니다.
     - `check` job에서는 사용하지 않고 `build-and-deploy` job에서만 복원합니다.
     - 캐시 키는 실행별 `llm-cache-${{ github.run_id }}`이고, `llm-cache-` prefix로 이전 실행의 최근 캐시를 복원합니다. 새 실행이 끝나면 post-job 단계에서 새 키로 저장됩니다.
     - `force-deploy`는 복원 직후 `.cache/`를 삭제하므로 LLM 결과를 재생성합니다.

2. **Git 저장소에 커밋되는 캐시 — `extra_files` 원문**
     - `downloads/extra_files/`의 실제 다운로드 파일과 `docs/.extra_files_state.json`의 URL·파일명·SHA-256 기록이 여기에 해당합니다.
     - [deploy.yml](.github/workflows/deploy.yml)의 새 runner는 `actions/cache`에서 이 파일을 복원하는 것이 아니라 `actions/checkout`으로 Git 커밋에서 가져옵니다.
     - 캐시 miss가 발생한 현재 workflow 안에서는 check job이 받은 파일과 상태를 `actions/upload-artifact`로 build job에 한 번 전달합니다. 이 artifact는 job 간 전달용이며 장기 보관용 캐시는 아닙니다.
     - Python 코드가 checkout된 파일의 SHA-256을 상태 기록과 비교합니다. 일치하면 네트워크 요청 없이 check/build 모두 파일을 재사용합니다.
     - 이 캐시는 Git commit history에 포함되므로 runner가 바뀌거나 Actions 캐시가 만료되어도 유지됩니다. 대신 DOCX 파일이 Git 저장소 용량을 차지합니다.
     - `force-deploy`는 이 디렉터리와 상태 파일도 삭제한 뒤 build하므로 원격 `extra_files`를 다시 다운로드합니다.

3. **GitHub Actions 서비스 캐시 — Python/uv 패키지**
     - `setup-uv`의 `enable-cache: true`가 의존성 다운로드 캐시를 관리합니다.
     - 애플리케이션 입력 파일이나 LLM 결과가 아니며, 외부 파일 재사용 여부에도 영향을 주지 않습니다.

즉, `extra_files` 재사용에 사용되는 것은 **GitHub Actions의 cache 서비스가 아니라 Git 저장소에 커밋된 파일과 SHA-256 상태 기록**입니다. GitHub Actions cache 서비스는 LLM 결과와 Python 패키지 다운로드에만 사용됩니다.

환경 변수 `SCHEDULE_EXTRA_FILES`로 JSON 배열을 지정하면 config.json 값을 대체합니다.

## 상태 파일(`docs/.schedule_state.json`)의 의미

성공적으로 HTML이 생성되면 다음 정보가 저장됩니다.

- `files`: 각 소스 폴더에서 실제로 선택된 파일명과 업로드 시각
- `meeting_id`: 이번 빌드의 기준 미팅 ID
- `timezone`: 해당 미팅에서 감지한 IANA 타임존
- `timezone_status`: `resolved`, `pending_timezone_ref`, `detection_failed` 중 하나
- `timezone_ref`: 실제 시간대 판정에 사용한 Agenda/Chair notes 파일의 식별자. 아직 참조 문서가 없으면 `null`

이 상태는 다음 용도로 사용됩니다.

- `check_update.py`가 FTP 변경 여부를 안정적으로 비교
- 같은 미팅에서는 타임존 재탐지를 생략하여 LLM 호출 절감
- FTP에 오래된 draft/오표기 파일이 뒤늦게 올라와도 기존 미팅 상태를 쉽게 되돌리지 않음

시간대 참조는 사용 가능한 직접 Agenda DOCX를 우선하고, 없으면 현재 미팅의 Chair notes DOCX/DOCM을 사용합니다. 새 미팅의 스케줄이 시간대 참조보다 먼저 올라오면 우선 `UTC`, `timezone_status: "pending_timezone_ref"`, `timezone_ref: null`로 저장합니다. 이후 Agenda DOCX 또는 Chair notes가 나타나면 `check_update.py`가 `timezone_ref`의 변화를 별도로 감지해 빌드를 다시 실행하고, 성공한 빌드가 실제 IANA timezone과 참조 식별자를 저장합니다. 같은 파일명이 갱신된 경우에도 업로드 시각 변화로 재다운로드합니다. 로컬 참조는 파일 내용 SHA-256으로 추적합니다. Agenda ZIP/CSV는 agenda description 입력으로만 사용하며 timezone 참조로 승격하지 않습니다.

## GitHub Actions 자동 배포

두 개의 워크플로우가 설정되어 있습니다:

### `deploy.yml` — 스케줄 빌드 및 배포

- **자동 실행**: 평일 5분마다 FTP 변경 감지 → 변경 시 재빌드 및 배포
- **수동 실행**: GitHub Actions 탭에서 `workflow_dispatch`로 트리거 가능
  - `check-and-deploy`: 변경 감지 후 변경 시에만 빌드/배포 (기본값)
     - `force-deploy`: 변경 여부 무시, LLM 및 외부 파일 캐시를 초기화한 뒤 강제 빌드/배포
  - `deploy-only`: 빌드 없이 현재 `docs/` 그대로 배포
- **변경 감지**: `check_update.py`가 모든 스케줄 폴더의 파일 메타데이터를 비교하며, 정규 미팅은 meeting rank를 우선하고 비정규 미팅은 업로드 시각을 사용
- **배포 방식**: `docs/index.html` 생성 → 상태 저장 → 자동 커밋 & 푸시 → GitHub Pages 배포

### `pages.yml` — GitHub Pages 배포

- `docs/` 경로 변경 시 또는 수동 트리거로 GitHub Pages 배포

### GitHub Secrets 설정

Repository Settings → Secrets and variables → Actions에서 다음 시크릿을 추가하세요:

- `GEMINI_API_KEY`: Gemini API 키

### GitHub Pages 설정

Repository Settings → Pages에서:
- **Source**: GitHub Actions

## 라이선스

MIT

## Bug report or Feature request

Please send email to duckhyun.bae@lge.com or use issue in repo. 
