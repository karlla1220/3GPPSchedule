# 3GPP Schedule Viewer

WG별 독립 파이프라인의 결과를 공통 간트차트로 제공하는 GitHub Pages 사이트입니다. RAN1과 RAN Plenary는 각각 독립된 실제 문서 파서를 사용합니다.

RAN1은 3GPP FTP 서버에서 최신 회의 스케줄 DOCX 파일을 다운로드하고, Gemini API로 비정형 테이블 텍스트를 파싱하여 **CSS Grid 기반 간트차트 스타일의 정적 HTML 페이지**를 생성합니다.

## 주요 기능

- 3GPP FTP에서 최신 스케줄 DOCX 자동 다운로드 (ZIP 내 문서 자동 추출 지원)
- **다중 소스 스케줄 통합**: Chair_notes 외 부의장(Hiroki, Sorour 등) 폴더의 스케줄도 자동 탐색·다운로드
- **미팅 우선순위 인식**: 정규 미팅은 `RAN1#124 < RAN1#124bis < RAN1#125` 순으로 비교하고, 비정규 미팅(AH/e/기타)은 업로드 시각 기준으로 판단
- `python-docx`로 테이블 구조 추출 및 병합 셀 처리 (TextBox 색상 기반 방 매칭)
- Gemini API를 사용한 비정형 텍스트 → 구조화 세션 데이터 변환 (결과 캐싱)
- **다중 소스 크로스레퍼런스**: 같은 시간대의 여러 스케줄 테이블을 하나의 LLM 호출로 통합하여 가장 상세한 세션 정보(AI 번호 등) 도출
- **회의 시간대 자동 감지**: Agenda DOCX 또는 Chair notes DOCX/DOCM의 OOXML에서 개최지 정보를 추출하여 IANA 타임존 자동 설정
- 요일별 탭 전환, 오늘 날짜 자동 선택되는 단일 HTML 간트차트 생성 (그룹별 색상, 자동 새로고침)
- GitHub Actions를 통한 활성 WG별 주기적 변경 감지·빌드 및 GitHub Pages 배포

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
```

- `GEMINI_API_KEY`: RAN1·RAN Plenary 파싱용 Gemini API 키 ([Google AI Studio](https://aistudio.google.com/apikey)에서 발급)
제작자와 연락처는 환경변수 대신 `site.json`의 `presentation`에서 설정합니다.

## 사용법

### 전체 파이프라인 (다운로드 → 파싱 → HTML 생성)

```bash
uv run python main.py
```

RAN1 문서와 RAN Plenary timeplan을 다운로드·파싱합니다. WG별 페이지와 기본 WG로 이동하는 `docs/index.html`을 함께 출력합니다.

### 로컬 DOCX 파일 사용

```bash
uv run python main.py --wg ran1 --local "Chair_notes/RAN1#124 online and offline schedules - v00.docx"
```

이미 다운로드된 DOCX 파일을 직접 지정하여 HTML을 생성합니다.

### 다운로드 건너뛰기

```bash
uv run python main.py --no-download
```

선택 WG의 다운로드 캐시를 사용합니다. RAN P는 `downloads/ran-plenary/inputs/`의 timeplan·agenda와 동일 회의의 메타데이터 또는 명시 설정이 필요합니다. LLM 캐시가 없으면 Gemini 호출은 수행합니다.

### 출력 경로 지정

```bash
uv run python main.py --output-dir output
```

사이트 루트는 `docs/index.html`이며, WG별 페이지는 `docs/ran1/index.html`, `docs/ran-plenary/index.html`입니다.

## CLI 옵션 요약

| 옵션 | 설명 |
|---|---|
| (없음) | FTP 다운로드 → 파싱 → HTML 생성 전체 파이프라인 |
| `--local <path>` | 선택한 한 WG의 로컬 DOCX 또는 RAN P ZIP으로 HTML 생성 |
| `--no-download` | 다운로드 없이 최신 로컬 파일 사용 |
| `--output-dir <path>` | 사이트 출력 폴더 (기본: `docs/`) |
| `--wg <id>` | 갱신할 WG: `ran1`, `ran-plenary`, `all` (기본) |
| `--render-only` | 저장된 `schedule.json`으로 HTML만 재생성; 파싱·네트워크 호출 없음 |
| `--rebuild-slots` | RAN1 슬롯 상태 또는 RAN P timeplan 해석 캐시를 초기화하고 재해석 |

## 프로젝트 구조

```text
main.py / build.py             # WG 실행, 결과 저장, 공통 사이트 조립
ci.py / check_update.py        # 활성 WG 변경 감지, 계획 전달, 빌드, 배포 판단
site.json                      # 기본 WG와 표시 순서
working_groups/
  registry.py                  # WG별 lifecycle의 명시적 등록 (지연 import)
  ran1/
    lifecycle.py               # check / prepare / reset / build 공통 인터페이스
    pipeline.py                # build_schedule(options) → Schedule
    parser.py / session_parser.py / merger.py
    downloader.py / check_update.py / slot_state.py
    agenda_descriptions.py / models.py / config.py
    config.json / prompts/     # RAN1 전용 설정과 LLM 프롬프트
  ran_plenary/
    lifecycle.py / sources.py  # 최신 timeplan·agenda 선택, 재검증, check→build 전달
    document.py                # DOCX 문단·run·글씨 색·취소선·원문 위치 추출
    interpreter.py             # LLM 해석, 근거·방 사용 시간·세션 범위 검증
    pipeline.py                # Portal 메타데이터와 공통 Schedule 조립
    config.json / prompts/     # RAN P 전용 설정과 프롬프트
shared/
  lifecycle.py                 # CheckResult, BuildOptions, WorkingGroup 규약
  site_config.py               # 활성 WG와 기본 WG 검증
  schedule.py                  # 공통 결과 모델과 JSON 저장/읽기
  renderer.py                  # 기존 간트차트 HTML·CSS·JS
  navigation.py                # WG 링크와 개최 상태
# WG별 결과/상태는 아래 폴더에 격리
docs/ran1/                     # index.html, schedule.json, slot_state/, 상태 JSON
docs/ran-plenary/              # index.html, schedule.json
downloads/ran1/                # 기존 다운로드와 extra_files
.cache/ran1/                   # 기존 LLM 캐시
ref_in_manual/ran1/            # RAN1 수동 참조 문서
```

WG의 내부 단계는 자유입니다. 공통 실행기는 각 `build_schedule(options)`에서
`shared.schedule.Schedule`을 받으며, WG끼리는 서로의 내부 모듈을 import하지 않습니다.
새 WG는 독립 폴더와 pipeline을 만든 뒤 `registry.py`와 `site.json`에 등록합니다.
공통 모델의 `DaySchedule.timeline`으로 날짜마다 시작/종료, 슬롯 간격, 눈금 간격,
휴식과 시간 블록을 전달합니다. 새 WG는 방 ID(`RoomInfo.id`, `Session.room_ids`)를
사용하며, RAN1의 기존 열 번호도 호환됩니다. 한 세션이 여러 방에 걸치면 방은 인접해야 합니다.

`Schedule.starts_on`/`ends_on`은 ISO 날짜, `timezone`은 개최지의 IANA 시간대입니다.
`starts_at`/`ends_at`에는 실제 시작·종료 시각을 UTC 오프셋 포함 ISO 형식으로 저장합니다
(예: `2026-08-24T09:00:00+02:00`). 기존 스냅샷에 이 필드가 없으면 `null`로 읽습니다.
날짜가 있는 미팅은 개최 중인 항목과 현재 선택 항목을 상단에 표시합니다.
RAN1도 Portal 메타데이터로 개최 날짜와 시간을 확인하며,
RAN Plenary는 Portal에서 동일 회의번호의 개최 날짜·장소를 조회해 실제 개최 상태를 표시합니다.

## 다중 WG 빌드와 로컬 확인

```bash
# RAN1·RAN Plenary 실제 파이프라인 + 사이트 조립
uv run python main.py

# RAN Plenary만 갱신; RAN1은 저장된 결과를 유지
uv run python main.py --wg ran-plenary

# 저장된 두 WG 결과를 사용하여 화면만 재생성 (API 키 불필요)
uv run python main.py --render-only

# 특정 RAN1 문서로 검증; 로컬 보조 문서를 자동 수집하지 않음
uv run python main.py --wg ran1 --local "path/to/RAN1 schedule.docx" --no-download

# 정적 사이트 미리보기
python3 -m http.server 8000 --directory docs

# 회귀 테스트
uv run pytest -q
```

루트는 활성 WG 중 실제 저장된 스케줄의 미팅 번호와 Portal `ONGOING` 항목이
일치하는 WG로 이동합니다. 동시에 개최 중이면 `site.json`의 `default_wg`를 우선하고,
그 WG가 개최 중이 아니면 `working_groups` 순서를 따릅니다. 일치하는 개최 중 미팅이
없으면 `default_wg`(기본 `ran1`)로 이동합니다. 데모와 다른 미팅 번호의 오래된
스케줄은 자동 선택 후보가 아닙니다. 실제 RAN Plenary 스케줄은 동일 미팅이
개최 중이면 자동 선택 후보에 포함됩니다.

API 장애 또는 `--render-only`/`--no-download`에서는 저장된 개최지 날짜로 판정합니다.
CI는 미팅 상태에 따른 루트 이동 변경도 감지하여 문서 변경 없이 사이트를 다시 렌더링합니다.
WG 링크는
`../ran1/`, `../ran-plenary/`인 일반 링크이며 클릭 시 URL과 전체 페이지가 바뀝니다.
GitHub Pages 프로젝트 하위 경로에서도 직접 접속·새로고침·뒤로가기가 동작합니다.
선택된 미팅은 중앙에서 강조하고 다른 미팅은 같은 줄의 회색 링크로 표시합니다.
날짜 탭과 NOW 표시 설정은 WG/미팅별로 분리해 저장합니다.
NOW는 브라우저의 현재 시각이 `starts_at ≤ 현재 시각 ≤ ends_at`인 동안 기본 ON이며,
해당 미팅에서 수동으로 OFF를 저장했다면 그 설정을 유지합니다. 기간 밖에서는 저장된
ON 설정도 적용하지 않고 버튼과 NOW 선을 OFF로 둡니다. 시각 정보가 없거나 유효하지
않은 스케줄 및 데모도 OFF입니다. 페이지를 열어 둔 상태에서는 시작·종료 경계와
분 단위 갱신, 페이지 복귀 시 상태를 다시 확인합니다. 시간 비교는 절대 시각으로,
NOW 선의 위치는 개최지 시간대로 계산합니다.

한 WG만 빌드해도 사용 가능한 모든 WG의 내비게이션은 다시 생성합니다.
실패한 WG는 마지막 정상 `schedule.json`을 유지하고 다른 WG 빌드는 계속하지만,
로컬 `main.py` 명령은 실패 코드로 종료합니다. 운영 CI는 아래의 `ci.py` 절차를 사용해
실패한 WG의 저장 상태까지 복원하고, 성공한 다른 WG의 갱신은 계속합니다.
`--wg ran-plenary`는 RAN1 파서를 import하지 않지만 새로운 문서의 해석에는 Gemini API 키가 필요합니다.
`--render-only`는 두 WG 모두 API 키·네트워크 없이 동작합니다.
`--render-only`는 기존 `schedule.json`이 필요하며 설정된 기본 WG 결과가 없으면 오류로 종료합니다.

기존 루트의 RAN1 Python 파일·설정·프롬프트는 `working_groups/ran1/`로 이동했고,
기존 `docs/`의 상태와 다운로드/캐시도 WG 하위 폴더로 옮겼습니다.
외부 코드의 import 경로와 수동 참조 파일 위치를 새 경로로 변경하세요.
RAN1만 단독 HTML로 출력하던 동작은
`uv run python -m working_groups.ran1.pipeline --output output/schedule.html`로 사용할 수 있습니다.

## RAN Plenary timeplan

RAN P는 Chair 폴더의 `RAN#<번호> time plan v<버전>.zip`/`.docx`를
**회의번호 → 숫자 버전 → 목록 메타데이터** 순으로 선택합니다. 오래된 회의·버전으로
자동 후퇴하지 않으며 ZIP에는 같은 회의·버전의 DOCX가 하나 있어야 합니다.
기본 주소는 `working_groups/ran_plenary/config.json`에 있습니다.

파서는 셀을 평문으로 뭉개지 않고 방 안내와 문단별 run의 글씨 색, 테마/스타일 상속,
줄바꿈, 취소선, 원문 ID를 보존합니다. Gemini가 문서 전체를 읽어 물리적 장소,
요일별 사용 가능 시간, 세션을 해석하고 코드는 원문 누락·시간 추정·방 중복·사용 기간을
검증합니다. 검증 실패 시 오류를 포함해 한 번 수정 요청하고, 계속 실패하면 기존 결과를 유지합니다.
시간이 없는 둘째 표는 순서를 유지한 별도 토픽·문서·AI 참조 목록입니다.
표 바로 앞의 문단들(이전 표 이후)을 원문 순서와 문구 그대로 표시하며, 문단이 없으면
안내문을 만들지 않습니다. 이 문구는 `Schedule.topic_preamble`에 저장됩니다.
이 목록은 원본 하이라이트를 연한 배경색으로 표현하고 원본 하이퍼링크·강조·취소선을
보존합니다. Agenda 설명은 상위 항목을 합친 들여쓰기 트리로 표시하므로 세부 항목의
문맥을 확인할 수 있습니다. 이 서식은 스냅샷에 저장되며 서식만 변경되면 LLM 재호출 없이 반영됩니다.

RAN#113 v04의 주요 표현:

- 검은 일반 토픽은 `room_scope="unassigned"`로 하루 전체 방 열에 병합합니다.
  공통 블록은 `shared`이며 둘 다 실제 모든 방을 예약한다는 의미는 아닙니다.
- 명시적인 `Main:`/`in RAN main room`은 검은 글씨라도 Main으로 배치합니다.
  개회·종료의 빨간 강조는 방 구분으로 사용하지 않습니다.
- 화요일은 세 장소 열을 표시하고 12:30부터 Breakout을 Madrid 1–2에서
  Neptuno + PTA Alcala로 옮깁니다. TBD도 해당 방 셀에 남습니다.
- 휴식 중 회의도 보존하고 휴식 이름은 시간축에 표시합니다.
  점심 세션은 12:30–13:30, 화요일 저녁은 19:00까지, 목요일은 17:00까지입니다.
- AI는 timeplan의 명시 번호 또는 구체적으로 연결된 토픽 표에서만 얻습니다.
  agenda.csv는 번호별 설명을 제공합니다. 근거가 없는 AI는 비워 둡니다.

`Schedule.topic_references`, `Session.room_scope`, `Session.notes`는 선택 필드로
기존 스냅샷도 읽을 수 있습니다. `--render-only`는 저장된 설명과 주석까지 재현합니다.

### 날짜·시간대 및 오프라인 입력

Portal 조회는 진행 중 여부가 아니라 선택한 RAN 회의번호로 일치시킵니다.
조회 범위는 현재 연도 전후 1년이며 과거 회의는 아래 설정으로 보완할 수 있습니다.
알려진 Portal 시간대는 공통 매핑으로 IANA 시간대로 변환하고, 미등록 표기는
장소·국가를 LLM에 전달해 해석한 뒤 `ZoneInfo`로 검증합니다. Portal 시작·종료 시각은
UTC 오프셋을 포함한 `starts_at`/`ends_at`으로 저장해 NOW 기간 제어에 사용합니다. 명시 설정이 우선이고, 조회 장애 시 같은 회의의 성공 메타데이터만 재사용합니다.
새 회의의 날짜·시간대를 확보하지 못하면 UTC로 추측하지 않고 빌드에 실패합니다.

아래 항목을 `working_groups/ran_plenary/config.json`에 설정하면 지정 v04를
오프라인 입력으로 검증할 수 있습니다. `timezone` 대신 `location`/`country`만 주면
시간대 해석에는 Gemini를 사용합니다. 전체 설정 예시는 `config.example.json`에 있습니다.

```json
{
  "local_agenda": "tests/fixtures/ran_plenary/agenda.csv",
  "meetings": {
    "113": {
      "starts_on": "2026-09-14",
      "ends_on": "2026-09-17",
      "location": "Madrid",
      "country": "ES",
      "timezone": "Europe/Madrid"
    }
  }
}
```

```bash
uv run python main.py --wg ran-plenary --local "tests/fixtures/ran_plenary/RAN#113 time plan v04.zip"
uv run python main.py --wg ran-plenary --no-download
uv run python main.py --render-only
```

### RAN P의 상태와 캐시

- `docs/ran-plenary/.schedule_state.json`: 성공한 timeplan·agenda의 URL, 회의번호,
  버전, SHA-256, HTTP validator 및 날짜·시간대 메타데이터. Git으로 추적합니다.
- `downloads/ran-plenary/inputs/`: 검증 가능한 원문 캐시. Git에는 추가하지 않습니다.
  check runner의 입력은 `.ci/transfers/ran-plenary/`로 build runner에 전달합니다.
- `.cache/ran-plenary/`: 원문 구조·색·방 안내·프롬프트·모델을 포함한 해시별
  LLM 결과와 원문 근거. 기존 CI의 `.cache/` 캐시 대상에 포함됩니다.

색만 변경되어도 재해석합니다. agenda 설명만 변경되면 LLM 시간표 결과를 재사용하고
설명을 다시 붙입니다. `force-deploy`는 입력·LLM 캐시를 비우되 마지막 성공 출력은
새 빌드가 성공하기 전까지 유지합니다. 다운로드·파싱·검증·렌더링 실패를 성공 상태로
기록하지 않으며 다음 check에서 다시 시도합니다.

실제 v04 ZIP·agenda와 검수한 Gemini 결과는 `tests/fixtures/ran_plenary/`에 있으며
자동 테스트는 실제 네트워크나 API 키를 사용하지 않습니다.

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
    lookup_timezone_reference()   # Portal 미팅 번호/TB 매칭 → IANA 시간대 + 개최 날짜
                                  # 확인된 표시 문자열은 코드 매핑, LLM 호출 없음
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

## 점진적 머지(Incremental Merge)와 `docs/ran1/slot_state/`

각 `(요일, 시간 블록)` 슬롯의 머지 결과는 `docs/ran1/slot_state/{Day}_{TB:02d}.json` 으로 저장됩니다. 다음 실행에서 각 소스의 해시를 직전 스냅샷과 비교해 다음 중 하나로 분류합니다.

- **STALE** — 이전과 동일한 내용. 모든 소스가 STALE이면 LLM 호출 없이 직전 머지 결과를 그대로 재사용합니다.
- **FRESH / NEW / REMOVED** — 변경이 감지됨. LLM에는 *변경된 소스의 원문* 과 *직전 머지 결과(baseline)* 만 전달되며, 변경되지 않은 STALE 소스의 원문은 프롬프트에서 제외됩니다. 이렇게 하면 한 소스가 항목을 합쳐버린 변경(consolidation)을 다른 소스의 오래된 상세 정보가 되살리는 회귀를 막을 수 있습니다.

이 파일들은 깃으로 추적되므로 GitHub 웹 UI에서 한 파일만 지우면 해당 슬롯만 다음 실행에서 cold 경로로 재빌드되고, 나머지 슬롯은 그대로 점진적/숏-서킷 경로를 탑니다. 누적된 carry-forward 오류 등으로 전체를 다시 빌드해야 한다면 `python main.py --rebuild-slots` 로 디렉터리 전체를 비울 수 있습니다.

## 미팅 선택 규칙

스케줄 파일 선택은 단순 업로드 시각 기준이 아니라, **미팅 ID 우선순위 + 상태 캐시**를 함께 사용합니다.

- 정규 plenary 미팅은 이름 순서를 해석합니다.
     - 예: `RAN1#124 < RAN1#124bis < RAN1#125`
     - 따라서 `RAN1#124` 파일이 더 늦게 올라와도, `RAN1#124bis`가 이미 존재하면 `124bis`가 우선입니다.
- 이전 실행의 `meeting_id`는 `docs/ran1/.schedule_state.json`에 저장되며, 다음 실행에서 **현재 미팅 힌트**로 사용됩니다.
- 다만 이 힌트는 고정값이 아닙니다.
     - 캐시에 `ran1#124bis`가 있어도 FTP에서 `ran1#125`가 나타나면, 새 실행은 `125`로 자동 전환하고 state도 갱신합니다.
- `AH`, `adhoc`, `e` 등 **비정규 미팅**은 이름만으로 전체 순서를 확정할 수 없으므로 업로드 시각을 기준으로 선택합니다.
- Vice-chair/Hiroki/Sorour 같은 보조 소스도 가능한 한 현재 메인 미팅에 맞춰 정렬되며, 같은 폴더 안의 오래된 미팅 파일이 늦게 업로드되어도 현재 미팅을 덮어쓰지 못합니다.

## 외부 파일 참조 (`extra_files`)

`working_groups/ran1/config.json`의 `extra_files`에는 폴더가 아닌 **개별 원격 파일 URL**을 나열할 수 있습니다.

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

빌드 시 `curl -OJL`과 등가 동작(redirect follow + Content-Disposition 기반 파일명)으로 `downloads/ran1/extra_files/`에 저장하고, 해당 파일도 Git에 커밋합니다. 파일명은 `Content-Disposition` → URL 경로 마지막 세그먼트 → `name` 필드 → 생성 순서로 결정됩니다. `docs/ran1/.extra_files_state.json`에는 URL별 다운로드 파일명과 SHA-256을 기록합니다.

기록된 URL의 파일명이 존재하고 로컬 파일의 SHA-256이 기록값과 같으면 check와 build 모두 네트워크 다운로드를 생략합니다. 상태가 없거나 파일이 삭제·변조된 경우에만 원격 파일을 다시 받아 캐시를 복구합니다.

CI 변경 감지는 각 URL에 대응하는 커밋된 파일의 **콘텐츠 sha256**을 `docs/ran1/.extra_files_state.json`과 비교해 동작합니다(`check_update.py`). 헤더(ETag/Last-Modified) 비교는 ETSI가 해당 헤더를 제공하지 않아 사용하지 않으며, `ref_in_manual`과 동일한 콘텐츠 해시 방식을 따릅니다.

CI에서 외부 파일 요청은 다음과 같이 동작합니다.

- `check` job은 먼저 커밋된 `downloads/ran1/extra_files/`의 파일을 기록된 SHA-256과 비교합니다. 일치하면 원격 URL을 요청하지 않습니다.
- URL이 새로 추가되었거나 캐시 파일이 없거나 해시가 다르면 `check` job이 원격 파일을 다운로드해 변경 여부를 확인하고 build를 트리거합니다.
- 변경이 감지되면 `check` job과 별도의 새 runner에서 `build` job이 실행됩니다. check에서 새로 받은 파일은 workflow artifact로 build job에 전달되므로 build에서 다시 다운로드하지 않으며, 성공한 build는 `docs/`와 `downloads/ran1/extra_files/`를 함께 커밋합니다.
- 이후 workflow의 check/build는 커밋된 파일과 해시가 일치하는 동안 외부 파일을 다시 다운로드하지 않습니다.
- 동일 URL의 원격 본문이 URL 변경 없이 바뀌는 경우에는 로컬 캐시만으로 알 수 없습니다. ETSI `wa.exe` URL은 메시지마다 새로 생성되므로 새 URL이 이 변경을 감지하는 기준입니다. 원격 재확인이 필요하면 해당 캐시 파일 또는 상태 항목을 삭제하고 build를 실행합니다.
- build 실패 시 새 상태와 캐시 파일은 커밋되지 않아 다음 실행에서 이전 상태를 기준으로 다시 확인합니다.

### 캐시의 종류와 저장 위치

이 프로젝트에서 `cache`라는 표현은 서로 다른 세 가지를 가리킬 수 있습니다.

1. **GitHub Actions 서비스 캐시 — LLM 결과**
     - [deploy.yml](.github/workflows/deploy.yml)의 `actions/cache@v4`가 `.cache/` 전체(WG별 하위 디렉터리)를 GitHub Actions 캐시 서비스에 저장합니다.
     - `session_parser.py`의 Gemini/LLM 결과 재사용을 위한 캐시이며, `extra_files` 원문 파일과는 관계가 없습니다.
     - `check` job에서는 사용하지 않고 `build-and-deploy` job에서만 복원합니다.
     - 캐시 키는 실행별 `wg-cache-v1-${{ github.run_id }}`이고, `wg-cache-v1-` prefix로 이전 실행의 최근 캐시를 복원합니다. 새 실행이 끝나면 post-job 단계에서 새 키로 저장됩니다.
     - `force-deploy`는 각 활성 WG의 `reset_cache()`를 호출합니다. RAN1은 LLM 캐시, 슬롯 상태, 외부 파일 캐시를 비워 다시 파싱합니다.

2. **Git 저장소에 커밋되는 캐시 — `extra_files` 원문**
     - `downloads/ran1/extra_files/`의 실제 다운로드 파일과 `docs/ran1/.extra_files_state.json`의 URL·파일명·SHA-256 기록이 여기에 해당합니다.
     - [deploy.yml](.github/workflows/deploy.yml)의 새 runner는 `actions/cache`에서 이 파일을 복원하는 것이 아니라 `actions/checkout`으로 Git 커밋에서 가져옵니다.
     - 캐시 miss가 발생한 현재 workflow 안에서는 check job이 받은 파일과 상태를 `actions/upload-artifact`로 build job에 한 번 전달합니다. 이 artifact는 job 간 전달용이며 장기 보관용 캐시는 아닙니다.
     - Python 코드가 checkout된 파일의 SHA-256을 상태 기록과 비교합니다. 일치하면 네트워크 요청 없이 check/build 모두 파일을 재사용합니다.
     - 이 캐시는 Git commit history에 포함되므로 runner가 바뀌거나 Actions 캐시가 만료되어도 유지됩니다. 대신 DOCX 파일이 Git 저장소 용량을 차지합니다.
     - `force-deploy`는 이 디렉터리와 상태 파일도 삭제한 뒤 build하므로 원격 `extra_files`를 다시 다운로드합니다.

3. **GitHub Actions 서비스 캐시 — Python/uv 패키지**
     - `setup-uv`의 `enable-cache: true`가 의존성 다운로드 캐시를 관리합니다.
     - 애플리케이션 입력 파일이나 LLM 결과가 아니며, 외부 파일 재사용 여부에도 영향을 주지 않습니다.

즉, `extra_files` 재사용에 사용되는 것은 **GitHub Actions의 cache 서비스가 아니라 Git 저장소에 커밋된 파일과 SHA-256 상태 기록**입니다. GitHub Actions cache 서비스는 LLM 결과와 Python 패키지 다운로드에만 사용됩니다.

환경 변수 `SCHEDULE_EXTRA_FILES`로 JSON 배열을 지정하면 working_groups/ran1/config.json 값을 대체합니다.

## 상태 파일(`docs/ran1/.schedule_state.json`)의 의미

성공적으로 HTML이 생성되면 다음 정보가 저장됩니다.

- `files`: 각 소스 폴더에서 실제로 선택된 파일명과 업로드 시각
- `meeting_id`: 이번 빌드의 기준 미팅 ID
- `timezone`: 해당 미팅에서 감지한 IANA 타임존
- `timezone_status`: `resolved`, `pending_timezone_ref`, `detection_failed` 중 하나
- `timezone_ref`: 우선 `type: "portal"`인 API 미팅 ID·시간대·개최 날짜·장소. 문서 대체 경로에서는 Agenda/Chair notes 식별자이며, 미해결 시 `null`

이 상태는 다음 용도로 사용됩니다.

- `check_update.py`가 FTP 변경 여부를 안정적으로 비교
- 같은 미팅에서는 타임존 재탐지를 생략하여 LLM 호출 절감
- FTP에 오래된 draft/오표기 파일이 뒤늦게 올라와도 기존 미팅 상태를 쉽게 되돌리지 않음

시간대는 `GetMeetings`에서 **해당 RAN1 미팅 번호와 TB ID가 모두 일치하는 행**을 우선
사용합니다. `StartTimeZone`/`EndTimeZone`은 IANA 이름이 아닌 표시 문자열이므로,
확인된 문자열과 `Country`를 코드로 매핑합니다. 예를 들어 유럽 표시 문자열과 `NL`은
`Europe/Amsterdam`이 되며 서머타임은 `ZoneInfo`가 처리합니다. GMT 숫자만 보고 고정
오프셋으로 추측하지 않습니다. 이 경로에서는 시간대용 문서 다운로드·장소 추출·LLM
호출이 없고 `schedule.json`에 `starts_on`/`ends_on` 및 시각을 보존한
`starts_at`/`ends_at`도 저장합니다. Agenda는 항목 설명
생성에 계속 사용하며 변경 여부도 별도로 추적합니다.

Portal 목록은 한국 날짜 기준 과거 366일~미래 183일 범위를 복수 WG로 조회하며,
프로세스/날짜별 성공 결과를 공유합니다. 100행이면 다음 페이지를 조회하고 최대
20페이지 또는 반복 페이지는 오류로 처리합니다. 이 범위 밖의 과거 미팅을 모두
찾는 기능은 아닙니다. `MtgDocURL`은 조회 결과에 보존하지만 FTP 소스 설정을 자동으로
바꾸지는 않습니다.

API 장애·미등록 시간대·조회 범위 밖 미팅에서는 같은 미팅의 저장된 Portal 시간대를
우선 재사용합니다. 사용할 Portal 참조가 없으면 기존 문서 대체 경로를 사용합니다.
이 경로는 사용 가능한 직접 Agenda DOCX를 우선하고, 없으면 현재 미팅의 Chair notes
DOCX/DOCM에서 개최지를 추출해 Gemini로 시간대를 판정합니다. 새 미팅의 스케줄이 시간대 참조보다 먼저 올라오면 우선 `UTC`, `timezone_status: "pending_timezone_ref"`, `timezone_ref: null`로 저장합니다. 이후 Agenda DOCX 또는 Chair notes가 나타나면 `check_update.py`가 `timezone_ref`의 변화를 별도로 감지해 빌드를 다시 실행하고, 성공한 빌드가 실제 IANA timezone과 참조 식별자를 저장합니다. 같은 파일명이 갱신된 경우에도 업로드 시각 변화로 재다운로드합니다. 로컬 참조는 파일 내용 SHA-256으로 추적합니다. Agenda ZIP/CSV는 agenda description 입력으로만 사용하며 timezone 참조로 승격하지 않습니다.

## GitHub Actions 자동 배포

운영 진입점은 `ci.py`입니다. 로컬 미리보기용 `main.py`와 동일한 WG 등록부,
활성 WG 설정, HTML 생성기를 사용하며 WG 내부의 파싱 절차는 강제하지 않습니다.

```text
repository_dispatch(cronjob_trigger) / workflow_dispatch
  → ci.py check: 활성 WG별 조회, 변경/실패 사유 기록
  → .ci/plan.json + .ci/transfers/<wg>/ 를 artifact로 전달
  → ci.py build: 변경된 WG만 준비·파싱, 공통 사이트 조립
  → 공개 파일 내용이 바뀐 경우만 Pages 배포
  → 성공한 WG의 결과·상태를 커밋
```

자동 호출 주기는 외부 스케줄러에서 관리합니다. 저장소 workflow 자체에는 cron이 없습니다.
`check`와 `build-and-deploy`는 기존 `github-pages` environment를 사용합니다.
`deploy.yml`과 수동 파일 배포용 `pages.yml`은 동일한 `pages` 동시 실행 잠금을 사용하며,
다음 주기 요청이 실행 중인 파서나 배포를 취소하지 않습니다.

### 활성 WG 설정과 추가 방법

`site.json`의 `working_groups[].enabled`로 참여 여부를 정합니다(생략하면 `true`).
비활성 WG는 import·조회·파싱·캐시 초기화·내비게이션에서 제외합니다.
기존 결과 파일은 보존하며 `default_wg`는 반드시 활성 WG여야 합니다.

WG를 추가할 때는 다음 세 곳만 구성합니다. 동일한 실행 환경과 자격 증명을 사용한다면
workflow YAML에 WG별 job이나 shell 분기를 추가할 필요가 없습니다.

1. 독립 WG 폴더에 `lifecycle.py`와 실제 처리 코드를 작성합니다.
2. `working_groups/registry.py`에 ID와 lifecycle 모듈을 등록합니다.
3. `site.json`에 WG를 추가하고 활성화합니다.

`shared/lifecycle.py`의 공통 인터페이스:

| 항목 | WG의 책임 |
|---|---|
| `check_updates() → CheckResult` | 문서 변경 여부·사유·오류를 반환. 새 파일은 `.ci/transfers/<wg>/`에만 임시 저장 |
| `prepare_build()` | 새 build runner에서 전달된 파일을 재사용하도록 준비 |
| `build_schedule(BuildOptions) → Schedule` | WG 고유 파싱·병합 후 공통 일정 반환 |
| `reset_cache()` | 강제 빌드 시 해당 WG의 캐시·점진 상태 초기화 |
| `input_paths` | 코드·설정·수동 입력 경로. 콘텐츠 해시로 변경 감지 |
| `persistent_paths` | 커밋할 결과·원문·상태 경로. 실패 시 빌드 전 상태로 복원 |
| `cache_paths` | 재생성 가능한 WG 캐시 경로 (`.cache/<wg>/` 사용) |

RAN1은 기존 FTP/문서/시간대/외부 파일 감지를 그대로 구현합니다.
RAN Plenary는 Chair의 timeplan과 agenda.csv를 독립적으로 HTTP 재검증하며, 같은 URL의 본문 변경과 Portal 메타데이터 변경도 감지합니다.
공통 데이터 모델·의존성이 바뀌면 활성 WG를 다시 빌드합니다. 공통 화면 또는 기본 WG만
바뀌면 저장된 일정으로 화면만 다시 생성합니다. 개최 날짜에 따른 내비게이션 변화도 검사합니다.

### 상태와 실패 처리

- `.ci/plan.json`: `build_ids`, WG별 `changed/unchanged/error`, 사유·오류, 입력 해시.
- `.ci/build-report.json`: WG별 `built/skipped/failed`, 조회 결과, `site_changed`, `has_errors`, 커밋 대상 경로.
- `docs/.build_state.json`: 마지막 성공 빌드의 WG 입력 해시와 사이트 입력 해시. 초기 도입 시 활성 WG를 한 번 빌드합니다.
- WG가 실패하면 해당 WG의 선언된 영속 경로를 복원합니다. 중간에 쓴 슬롯·문서 상태도 성공으로 기록하지 않습니다.
- 다른 WG의 성공 결과는 조립·배포할 수 있고, 마지막 단계에서 실패한 WG 때문에 workflow를 실패로 표시합니다.
- 새 WG가 실패해 이전 결과도 없으면 내비게이션에 넣지 않습니다. 기본 WG 결과가 없으면 사이트 조립을 실패 처리합니다.
- 문서 메타데이터만 바뀌고 일정은 같으면 기존 생성 시각을 유지해 불필요한 배포를 피합니다. 상태만 바뀌면 커밋만 합니다.
- Pages 배포가 실패하면 새 상태를 커밋하지 않습니다. 다음 주기에서 같은 입력 변경을 다시 감지합니다.
- RAN1 LLM 재시도가 모두 실패하거나 선택한 소스 다운로드가 실패하면 빌드를 실패 처리합니다. 빈 슬롯이나 누락된 부의장 자료를 성공 상태로 저장하지 않습니다.
- RAN1 `ScheduleSource.origin`은 원격/수동 출처를 보존합니다. 다운로드 후 `local_path`가 생겨도 원격 파일 목록을 다음 조회에 사용할 상태에 남깁니다.
- 수동 문서가 삭제되면 과거 `meeting_source: local` 기록만으로 이전 미팅에 고정하지 않습니다. 로컬 부의장 문서도 선택한 메인 미팅 ID에 맞춰 고릅니다.

### 실행 모드

| workflow 입력 | 동작 |
|---|---|
| `check-build-deploy` | 활성 WG 조회 후 필요한 WG만 빌드; 공개 결과가 달라지면 배포 |
| `build-deploy` | 조회 생략, 활성 WG 전체 빌드; 공개 결과가 달라지면 배포 |
| `force-deploy` | 활성 WG 각각의 캐시를 초기화하고 전체 빌드; 공개 결과가 달라지면 배포 |
| `deploy-only` | 파싱 없이 현재 `docs/`를 명시적으로 배포 |

로컬에서 동일한 운영 절차를 검증할 수 있습니다(저장소 루트에서 실행):

```bash
uv run python ci.py check
uv run python ci.py build
# 조회를 생략하는 수동 모드
uv run python ci.py build --action build-deploy
uv run python ci.py build --action force-deploy
```

`ci.py check/build`는 WG별 실패도 끝까지 수집하고 `has_errors`를 출력합니다.
운영 workflow는 이 값을 검사해 실패 처리합니다. 위 명령 자체는 예측 가능한 WG 실패를
보고서로 반환하므로 직접 실행할 때도 보고서를 확인하세요. 설정·계획 오류는 비정상 종료합니다.
빌드는 계획 생성 이후 코드나 설정이 바뀐 경우 해당 계획을 거부합니다.

### 환경과 Pages 설정

- `github-pages` environment(또는 repository secrets)에 `GEMINI_API_KEY`를 둡니다.
- 공통 제작자·연락처는 `site.json`의 `presentation`에서 읽습니다. WG 파서는 연락처 환경변수를 요구하지 않습니다.
- Repository Settings → Pages → Source는 **GitHub Actions**입니다.
- `pages.yml`은 `main`의 `docs/**` 변경 또는 수동 트리거로 저장된 사이트를 배포하는 별도 경로입니다.

## 라이선스

MIT

## Bug report or Feature request

Please send email to duckhyun.bae@lge.com or use issue in repo. 

## 공통 페이지 정보와 WG별 레이아웃

`site.json`의 `presentation`은 활성화된 모든 WG 페이지에 적용됩니다.

```json
"presentation": {
  "creator": "Duckhyun Bae",
  "contact_name": "Duckhyun Bae",
  "contact_email": "duckhyun.bae@lge.com",
  "notice": "자동 생성된 일정입니다. 오류나 개선 의견을 알려주세요."
}
```

`creator`, `contact_email`을 비우면 해당 줄을 숨깁니다. `notice`를 생략하면 기본 안내문을
사용하고, 빈 문자열이면 숨깁니다. 더미 미팅은 설정과 관계없이 Demo 안내를 표시합니다.
메일 주소는 빌드 전 검증하고, 표시 문자열은 HTML 이스케이프합니다.

`shared/page.py`의 `render_header()`가 WG 선택, 출처·생성 시각, 안내문, 제작자와 연락처를
함께 생성합니다. `shared/renderer.py`는 이 헤더와 일정 본문을 조립합니다. 이후 다른 WG용
HTML 레이아웃을 작성할 때도 `render_header(schedule, presentation=..., schedules=..., groups=...)`를
재사용할 수 있습니다. 헤더는 `meta`, `demo-notice`, 내비게이션 클래스와 현재 시각용
`#tz-now`를 제공하며, 스타일과 시계 갱신은 사용하는 레이아웃이 담당합니다.

WG 파서는 일정 데이터만 반환합니다. 과거 `schedule.json`의 연락처 필드는 읽기 호환을 위해
남겨 두지만 페이지에서는 사용하지 않습니다. 공통 정보 변경은 CI에서 HTML 재생성만 수행하며,
WG 문서 다운로드나 파싱을 다시 실행하지 않습니다. 로컬 확인은 `uv run python build.py --render-only`입니다.


공통 화면의 HTML/CSS/JavaScript 원본은 `templates/`에 있으며 `shared/renderer.py`가
WG별 스케줄과 미팅 메타데이터를 주입합니다. 템플릿 변경도 CI의 렌더링 변경 감지에
포함됩니다. RAN1 파서의 시간 계산과 LLM 역할은 [파싱 작동 원리](PARSING_ARCHITECTURE.md)를 참고하세요.
