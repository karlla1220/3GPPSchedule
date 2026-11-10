# 스케줄 파싱 작동 원리

이 문서는 DOCX 원본이 `docs/ran1/index.html`의 일정으로 변환될 때 **결정론적 코드가 처리하는 부분**과 **LLM이 해석하는 부분**을 구분한다. 장애 분석이나 특정 일정의 출처를 추적할 때는 이 경계를 기준으로 확인한다.

이 문서는 RAN1 파이프라인을 설명합니다. 아래의 파서·모델·세션 모듈 이름은
`working_groups/ran1/` 기준이며 공통 HTML 생성기는 `shared/renderer.py`,
화면 템플릿은 `templates/`에 있습니다. 시간대는 Portal API를 우선 사용하고
모든 WG가 공통 `shared/timezones.py`로 정하며, 실제 시작·종료 시각에 따른 NOW 제어는 공통 화면에서 처리합니다.

## 전체 흐름

```text
3GPP 파일 목록 및 로컬 참조
  │
  ├─ [결정론적] 대상 미팅·원본 파일 선택 및 다운로드
  ▼
DOCX
  │
  ├─ [결정론적] 표, 요일, 시간 블록, 방, 병합 셀, 셀 원문 추출
  ▼
CellData + 방 목록
  │
  ├─ [결정론적] 같은 (요일, 시간 블록)의 Main/vice-chair 셀 수집
  ├─ [LLM 보조] vice-chair 문서의 기본 방 이름(Main, Brk1 …) 해석
  ├─ [결정론적] source hash 및 이전 slot state 비교
  ├─ [결정론적] 칸 이력과 지금 칸을 비교해 칸마다 변경 표시
  ▼
TimeSlotData
  │
  ├─ [LLM] 비정형 셀 원문을 세션 JSON으로 해석·통합
  ▼
세션 JSON
  │
  ├─ [결정론적] chair 근거 검사, agenda 설명 결합
  ├─ [결정론적] 시작·종료 시각 및 물리 방 좌표 계산
  ├─ [LLM] group_header 이름 정규화
  ├─ [결정론적] 비어 있는 group_header 보완
  ▼
Session 모델 → docs/ran1/schedule.json → docs/ran1/index.html
```

## 1. 원본 파일 선택과 다운로드

`working_groups/ran1/downloader.py`와 `working_groups/ran1/pipeline.py`가 다음 정보를 이용해 입력을 선택한다.

- 미팅 ID 우선순위와 업로드 시각
- `ref_in_manual/ran1/`의 로컬 참조
- 이전 실행의 `docs/ran1/.schedule_state.json`
- Chair schedule, vice-chair schedule, Agenda 파일의 종류

이 단계의 선택 규칙과 파일 비교는 결정론적이다. 같은 파일 목록, 설정, 상태를 입력하면 같은 파일이 선택된다. 네트워크 목록이 일부만 반환되거나 다운로드가 실패하는 경우에는 이전 상태를 보존하는 방어 로직이 적용될 수 있다.

성공한 실행은 선택한 파일명, `meeting_id`, timezone 관련 정보를 `docs/ran1/.schedule_state.json`에 기록한다.

## 2. DOCX 구조 파싱: 결정론적 영역

`parser.py:parse_docx()`는 `python-docx`와 OOXML을 이용해 다음 항목을 추출한다.

- schedule table 식별
- 요일별 열 범위
- 온라인·오프라인 방 이름과 병합 셀 범위
- 표의 행 시간으로부터 시간 블록 인덱스
- 각 일정 셀의 **원문 텍스트 그대로**
- 일부 특수 행의 parser-derived `fallback_start_time`
- 셀이 시간 블록 안 몇째 줄에 있는지(아래 `block_row`, `row_span`, `row_starts`)

결과는 `models.py:CellData`이며 주요 필드는 다음과 같다.

```text
text                 DOCX 셀에서 추출한 원문
day                  요일
room_indices         셀이 차지하는 방 열
time_block_index     표의 시간 행이 속한 시간 블록(0~3: 오전, 오전 휴식 뒤, 점심 뒤, 오후 휴식 뒤)
time_block_start/end 그 블록의 경계(Main 문서의 시간 칸에 적힌 값)
fallback_start_time  표 구조로 안전하게 유추된 예외적 시작 시각
block_row            블록 안에서 셀이 시작하는 줄(0부터)
row_span             셀이 세로 병합으로 걸친 줄 수
row_starts           블록의 줄마다 시간 칸에 적힌 시작 시각(병합으로 이어진 줄은 None)
```

한 블록을 여러 줄로 나누는 표가 있다. 시간 칸을 세로로 병합하고(RAN1#126bis 화요일 17:00~19:30: 첫 줄에 방마다 칸, 둘째 줄에 `Early dinner (60)`),
또는 늦게 시작하는 줄에 자기 시간 칸(`09:00 ~ 10:30`)을 둔다. 이어진 물리 행이 같은 블록이면 한 블록의 줄들이다. 줄 높이는 읽지 않는다(표시용이다).

**시간 블록과 휴식은 Main 문서에서 읽는다(`parser.py`의 `read_time_blocks`, `document_time_blocks`).** 의장은 회의마다 휴식을 옮긴다.
RAN1#119~#126bis는 08:30–10:30, 11:00–13:00, 점심 13:00–14:30, 14:30–16:30, 17:00–19:30(#119는 19:00)이었고,
RAN1#127 v00은 08:00–10:30, 11:00–12:30, 점심 12:30–14:00, 14:00–16:30, 17:00–19:00이다. 블록을 고정해 두었을 때 #127의 08:00·14:00 행이 통째로 빠졌다.

- Main의 첫 일정 표(online 표)에서, 휴식 행 사이에 이어진 시간 칸들이 한 블록이다(가장 이른 시작 ~ 가장 늦은 끝). 같은 시간 칸이 두 번 적힌 줄(#127 수요일 `Early dinner`)과 늦게 시작하는 줄도 그 블록이다.
  휴식은 휴식 행에 적힌 이름과 시각이다(`Lunch break: 12:30 ~ 14:00`). 시각이 없으면 블록 사이의 틈이다. 그리드의 시간 축(`ran1_timeline`)은 첫 블록 시작부터 마지막 블록 끝 + 15분이다.
- 블록 번호는 예전 표준 블록(`models.py`의 `TIME_BLOCKS`, #126bis) 가운데 가장 많이 겹치는 것의 번호다. 08:00–10:30도 0번이다. 번호가 slot state 파일(`Monday_00`)과 Main 이력의 키라서 블록이 옮겨도 이어진다. 일대일로 맞지 않으면 순서대로 센다.
- 부의장 문서는 Main의 블록에 넣는다. 시간 칸의 범위가 가장 많이 겹치는 블록이다. 부의장 표가 예전 시각(`08:30 ~ 10:30`)을 적어도 Main의 08:00 블록에 든다.
- 블록 경계가 바뀌면 그 슬롯의 이전 병합은 기준이 되지 않는다(cold). slot state가 병합한 블록의 경계(`block_start`, `block_end`)를 적고, 그 값이 없는 예전 state는 #126bis 블록으로 본다.
- Main의 시간 칸을 읽지 못하면(겹치는 블록, 시간 칸 없음) 예전 표준 블록을 쓴다.
- #119~#127 문서 34개(Main, 부의장)를 예전 코드와 비교했다. #127 말고는 셀이 바뀌지 않는다(#119는 마지막 블록 끝만 문서대로 19:00이 된다). 아래 비정규 셀은 따로 있다. 테스트는 `tests/test_time_blocks.py`다.

**휴식을 넘어 병합된 셀은 비정규 셀 하나다(`CellData.cell_end`).** 의장은 금요일 마지막 칸을 19:00까지 세로로 병합하고, 오후 커피 휴식 행은 월~목 열만 덮는다(#119~#127 모든 회의, Main의 `Any other open issues`와 offline의 `To be assigned by …`).
- 셀은 시작한 블록의 슬롯에만 둔다. `cell_end`는 병합이 닿는 마지막 블록의 끝이다(#127 금 14:00 블록, `cell_end` 19:00). 블록마다 사본을 두지 않는다. 원본은 세션 하나이고, 사본을 두면 금요일에 없는 휴식이 세션을 둘로 가른다.
- 슬롯 병합 지시문은 그 셀 머리에 `(cell merged down to 19:00, past this time block)`를 붙인다. 세션은 셀 끝까지 블록 끝을 넘을 수 있고, 셀 끝을 넘으면 배치 오류다(`session_parser._limits`).
- 병합이 시간 칸 없는 행에서 시작하면(#119 금요일만의 점심 행 아래, 부의장 문서의 `expected to close at` 행) 처음 닿는 블록에 둔다. 예전에는 시작 행을 읽지 않아 셀이 통째로 빠졌다.
- `cell_end`는 셀 대장과 소스 hash에 들어간다(`fallback_start_time`과 같다). 정규 셀은 값이 없어서 예전 hash가 그대로다.

**개회·마감 문장은 LLM이 뜻으로 읽는다(`prompts/multi_source_cold.md`의 "When the day opens and closes").** `RAN1#127 commences at 09:00 on Monday`, `RAN1#127expected to close at 16:00`처럼 날의 시작과 끝을 말하는 문장은 낱말이 달라도(`closing by 4pm`, `end around 15:30`) 같다.
- 그 슬롯의 모든 방에 적용한다. 마감 문장이 없는 칸(offline `To be assigned by Sorour`, 19:00까지 병합)도 마감에서 끝난다. Main 칸은 지우지 않고 줄인다.
- 파서는 셀 글에서 시각을 읽지 않는다. 병합 모양(구조)만 `cell_end`로 넘기고, 19:00까지의 병합이 표의 모양일 뿐인지는 글을 읽는 쪽이 정한다.
- 규칙을 넣으면서 `MERGE_PROMPT_VERSION`을 7로 올렸다.

여기서 `parser.py`의 `HH:MM` 정규식은 **표 왼쪽의 시간 행을 어느 시간 블록에 넣을지** 판단하는 데 사용된다. 일정 셀 내부의 `9 :50-10 :30` 같은 문자열을 세션으로 분해하기 위한 정규식은 아니다.

따라서 이 단계는 다음 텍스트를 정규화하지 않고 보존할 수 있다.

```text
Xiaodong
6GR
.10.4.2 (8 :30-9 :00)
..10.5.3.4 (9 :50-10 :30)
```

## 3. 시간 슬롯별 다중 소스 수집

`merger.py:collect_time_slot_data()`가 모든 `CellData`를 `(day, time_block_index)`별로 묶는다.

- Chair schedule은 `Main Schedule`로 등록한다.
- Hiroki/Sorour 등의 문서는 `<person>'s schedule`로 등록한다.
- vice-chair 셀 가운데 같은 요일·시간 블록의 Main 글과 같은 셀(공백 차이 무시)은 뺀다. 지금 Main뿐 아니라 이 회의에서 본 모든 Main 버전(Draft 포함)의 글과 비교한다(`main_history.py`).
  부의장 문서에는 의장 표를 붙여 둔 사본이 있고, 사본은 의장이 나중에 고친 내용을 따라가지 않는다. 옛 Main 글이 그대로인 셀은 부의장이 고친 것이 아니라 사본이므로 병합에 넣지 않는다.
  - RAN1#126bis: Hiroki v08의 사본 표(표 2·3)는 모두 빠지고(금요일 08:30 Tamna BC Hall·Yeongju A Hall의 Main v02 글 포함), 자기 표(표 0·1)는 모두 남는다.
    Sorour v01의 사본 표에서는 Sorour가 고친 5칸(`AI 10.3.1` 표기, `9.4`·`9.5` NTN)만 남는다. 지난번에 Sorour가 고친 것으로 본 10.5.4 세부 시간(50/25/40/35)은 Main Draft의 글 그대로라서 빠진다. 10.5.4는 Hiroki 세션이고 Hiroki 자기 표의 세부가 따로 있다.
  - Main 글은 실행마다 `docs/ran1/main_history/<회의>.json`에 블록별로 쌓는다. 같은 글은 한 번만 두어서 회의 하나가 약 8KB다(#126bis Main 10개 버전).
    slot state와 따로 두므로 `force-deploy`가 지우지 않는다. 결과의 근거가 원본 문서의 기록이라서 cold도 같은 입력을 본다.
  - 실행이 보지 못한 Main 버전은 기록에 없다. 그 버전의 사본은 남을 뿐이고(예전과 같다), 부의장 글을 잘못 빼지는 않는다.
    FTP의 회의 보관 폴더에는 #126bis Main이 Draft v00~v03과 v00만 남아서, 지난 버전을 나중에 FTP에서 다시 채울 수는 없다. 그래서 실행마다 쌓는다.
- 물리 방 이름은 LLM 입력에서 안정적인 `RAN1_main`, `RAN1_brk1`, `RAN1_off1` 등의 alias로 바뀐다.
- 셀마다 id(`cell c1a2b3`)를 붙인다(`_assign_cell_ids`). source, 방, 글로 만들어서 셀이 그대로이면 실행이 바뀌어도 같은 id다. 증분 병합의 baseline과 재사용한 slot state가 그 id로 셀을 가리킨다.

문서가 방 이름을 적지 않으면(방 이름 글상자가 비어 있으면) RAN1의 방 역할 이름을 쓴다: online 표 `Main`, `Brk1`, `Brk2`, offline 표 `Offline1`, `Offline2`(`parser.py`의 `_default_room_names`). RAN1#127 v00이 그렇다. 표 아래에 `RAN1 Meeting Rooms (Main, Brk#1, Brk#2)`, `RAN1 Offline Meeting Room (Offline#1, Offline#2)`만 있고 글상자가 비어 있다. 표 메타데이터의 `default_room_names`가 이것을 밝힌다.

vice-chair 문서의 방 이름이 이 기본값이면(`default_room_names`) `merger.py:_resolve_vc_room_names()`가 문서의 표 앞 문맥을 LLM에 보내 Main schedule의 방과 연결한다. 즉, 이 부분은 구조 파싱 중 예외적으로 LLM의 도움을 받는다. 결과는 `.cache/`에 저장될 수 있다.

각 source는 방 label, 셀 원문, prompt version을 포함해 hash된다. 이 hash와 `docs/ran1/slot_state/{Day}_{TB}.json`의 이전 hash를 비교하여 다음 상태를 정한다.

- `STALE`: 입력이 같음
- `FRESH`: 기존 source의 내용이 바뀜
- `NEW`: 새 source가 생김
- `REMOVED`: 이전 source가 사라짐

이 상태는 슬롯을 건너뛸지 정하는 데만 쓴다. 칸 하나만 바뀌어도 source 전체가 `FRESH`가 되기 때문이다.
무엇이 바뀌었는지는 칸 단위로 본다. slot state에는 직전 병합에 쓴 칸들(칸 이력, `source_entries`)이 함께 저장되고,
`merger.py:diff_source_entries()`가 지금 칸을 그것과 맞춰 칸마다 `UNCHANGED`·`CHANGED`·`NEW`를 붙이고 사라진 칸을 `REMOVED`로 모은다.
비교는 파싱 결과끼리만 한다. LLM 결과는 실행마다 달라질 수 있어 기준이 되지 못한다. 용어는 `GLOSSARY.md`에 있다.

- 칸은 방 label과 원문(공백 무시)으로 맞춘다. 같은 방 label에 다른 원문이 있었으면 `CHANGED`이고 이전 원문을 함께 둔다.
  병합 칸이 나뉘거나 합쳐지면 방 label이 바뀌므로 `NEW`와 `REMOVED`가 된다. 방 배치를 따로 해석하지 않아서 방이 몇 개이든 같다.
- 칸 이력은 칸마다 id(`cell_id`)도 둔다. 바뀌거나 사라진 칸의 옛 id를 baseline 세션의 `source_cells`에서 찾으면 그 칸이 먹여 살리던 방을 코드가 안다(`stale_cell_ids`).
- 칸 이력은 그 병합의 지시문 버전(`merge_prompt_version`)과 함께 저장한다. 버전이 다르면 칸 이력을 쓰지 않는다(cold).
- 칸이 바뀐 시각(`since`)은 두지 않는다. 증분이 다시 만드는 영역은 cold와 같은 규칙이라 시각으로 정할 것이 없고, 이력을 지우면 사라지는 값에 결과가 기대면 안 된다(2026-11-03 결정).
- RAN1#126bis 금요일 08:30 두 업데이트가 테스트다(`tests/test_cell_changes.py`). Main v04 → v05는 203 칸만, Hiroki v07 → v08은 Hiroki의 Yeongju B Hall 칸만 바뀐 것으로 나와야 한다.
  예전 증분 병합은 source 단위로 판단해서 Main v05가 Yeongju B Hall의 Hiroki 세부를 지웠고, Hiroki v08 안의 옛 Main 사본이 Tamna BC Hall·Yeongju A Hall을 Main v02로 되돌렸다.

## 4. 비정형 셀 해석: LLM 영역

`session_parser.py:parse_time_slots()`는 시간 슬롯마다 Gemini를 호출한다. LLM은 다음 의미 해석을 담당한다.

- header와 실제 leaf session 구분
- `(N)`을 duration으로 해석
- 점(`.`)으로 시작하는 하위 AI 구조 해석
- Chair와 `group_header` 연결
- Main schedule과 vice-chair 상세 내용 통합
- 세션을 실제 target room에 배치
- 셀 내부의 explicit time range 해석
- `name`, `duration_minutes`, `specified_start_time`, `chair`, `agenda_item` 생성

응답은 JSON schema로 필드와 자료형을 제한하지만, `specified_start_time`은 현재 단순 문자열이다. schema 자체가 `HH:MM` 정규식까지 검증하지는 않는다. 대신 prompt가 explicit time range를 만나면 시작 시각을 `HH:MM`으로 반환하도록 지시한다.

예를 들어 다음 원문은:

```text
..10.5.3.4 (9 :50-10 :30)
```

LLM에 의해 다음처럼 해석된다.

```json
{
  "name": "10.5.3.4",
  "duration_minutes": 40,
  "specified_start_time": "09:50",
  "chair": "Xiaodong",
  "group_header": "6GR"
}
```

즉, 공백 또는 NBSP가 섞인 inline time range를 `09:50`으로 정규화한 주체는 결정론적 DOCX parser가 아니라 LLM이다.

### 미정 칸: 원문을 잃지 않는다

칸이 그 자리에 무엇을 할지 아직 정하지 않았다고 적을 수 있다(`To be assigned by Sorour`, `TBD`, `TBC`, `Reserved`). 이런 칸을 **미정 칸**이라 부른다. 규칙은 원문을 가장 적게 잃는 쪽이다(`prompts/multi_source_cold.md`의 "Undecided cells").

- 같은 방·시각을 정해 둔 다른 칸이 있으면(내용이 맞는 부의장 표) 그 내용을 쓴다. 미정 칸도 출처(`source_cells`)에 넣는다.
- 없으면 미정 칸을 적힌 그대로 둔다. 칸 글 전체가 이름이고(줄은 ` / `로 잇는다), 칸이 걸친 시간 전체이며, AI 번호와 그룹은 비운다. 주제, 시간 나눔, 방 나눔을 지어내지 않는다.
- 칸이 정할 사람을 적었으면(`by Sorour`) 그 사람이 의장이다. 여럿이면 ` / `로 잇는다(`Sorour / Hiroki`).
- 여러 방에 걸친 칸은 그 방들에 걸친 하나로 둔다.
- RAN1#126bis 금요일 14:30 201+203의 `To be assigned by Sorour / To be assigned by Hiroki`는 두 방에 걸친 14:30–16:30 하나이고 의장은 `Sorour / Hiroki`다.
  예전에는 LLM이 이 칸을 버리거나(2026-10 cold 실험), 두 방에 Sorour 1시간 · Hiroki 1시간으로 나눠 지어냈다(2026-11-03 게시본).

### Cold, incremental, short-circuit

슬롯별 처리 경로는 세 가지다.

| 경로 | LLM에 전달되는 데이터 | 사용 조건 |
|---|---|---|
| Cold | 현재 모든 source의 모든 칸 | 이전 slot state가 없음, 칸 이력이 없음, 칸 이력의 지시문 버전이 다름(`MERGE_PROMPT_VERSION`을 올림) |
| Incremental | 이전 merge baseline + 모든 source의 모든 칸(칸마다 id와 변경 표시) + 사라진 칸 + 다시 만들 방 목록 | 같은 버전의 칸 이력이 있고 바뀐 칸이 있음 |
| Short-circuit | 전달 없음 | 모든 source가 `STALE`, 또는 칸 이력과 칸이 모두 같음(같은 방 label끼리 순서만 바뀜); 이전 merge 결과 재사용 |

증분 병합은 `UNCHANGED`가 아닌 칸이 걸친 영역(방)만 다시 만들고, 다른 영역은 baseline을 그대로 옮긴다(`prompts/multi_source_incremental_prefix.md`).
바뀐 칸의 옛 글이 걸쳐 있던 방도 다시 만든다. 부의장 칸은 내용으로 방이 정해지므로, `Room A` 칸이 9.3.x(Yeongju B Hall)에서 10.2(203)로 바뀌면 203만이 아니라 Yeongju B Hall도 다시 만들어야 옛 9.3.x가 남지 않는다.
코드가 baseline 세션의 `source_cells`로 그 방을 찾아 프롬프트의 `Rooms whose baseline cells changed`에 적는다.
다시 만드는 영역은 그 영역의 모든 칸으로 cold와 같은 규칙에 따라 만든다. Main 칸만 바뀌어도 바뀌지 않은 부의장 칸의 세부가 그 방에 다시 들어간다.
바뀌지 않은 칸까지 모두 보내는 것은 병합 칸이 여러 방에 걸치고 부의장 칸이 다른 방의 세부를 알려 주기 때문이다. 바뀌지 않은 칸은 그것만으로 baseline을 바꾸지 않는다.

baseline에 들어간 오류는 그 영역의 칸이 다시 바뀌면 증분이 그 영역을 다시 만들면서 없어진다. 칸이 바뀌지 않는 영역의 오류는 cold로만 없어진다(`--rebuild-slots`, 슬롯 파일 하나 지우기).

**칸 이력은 호출을 줄이고 결과를 흔들지 않으려는 것일 뿐, 맞는 결과의 근거가 아니다.** 기준은 지금 칸으로 한 cold다.
- 증분을 여러 번 이어 온 결과는 지금 칸으로 한 cold 한 번과 같아야 한다. 다시 만드는 영역은 cold 규칙이고, 다시 만들지 않는 영역은 칸이 그대로다.
- 그래서 이력을 모두 지워도(`force-deploy`) 잃는 것은 호출 비용뿐이다. 모든 슬롯이 cold로 다시 만들어지고, 그 cold가 새 칸 이력을 저장해 다음 실행부터 증분이 된다.
- 이력에만 있고 원본 문서에는 없는 정보로 결과를 정하지 않는다. cold가 알아야 하는 것(예: 부의장 문서의 옛 Main 사본 가려내기)은 원본 문서에서 다시 만들 수 있어야 한다.

`force-deploy`는 `.cache/`와 상태 파일을 지우고 `main.py --rebuild-slots`로 실행하므로 모든 시간 슬롯이 이전 baseline 없이 cold 경로로 재생성된다.
칸 이력이 생기기 전에 저장된 slot state는 그대로 읽힌다. 바뀌지 않은 슬롯은 건너뛰면서 칸 이력을 저장하고, 바뀐 슬롯은 한 번 cold로 만든다.

## 5. LLM 응답 이후의 결정론적 처리

LLM 응답을 그대로 HTML에 쓰지는 않는다. 코드가 다음 후처리를 수행한다.

1. Main room의 chair는 Main schedule에 명시적 근거가 있을 때만 유지한다.
2. `agenda_item`을 세션 이름과 맞춘다(`_normalize_agenda_fields`). AI 번호와 이름 앞 번호의 끝 점을 떼고(`10.8.1.` → `10.8.1`), 하나뿐인 AI가 이름 번호의 상위(`10.5.4`, `10.5.4.x`)이면 이름 번호(`10.5.4.4`)로 바꾼다. 원문의 `10.8.1.(40)` 같은 점이 남으면 필터에 `10.8.1`과 다른 항목으로 보이고, Main의 `.10.5.4 (120)`을 vice-chair의 하위 항목으로 나눌 때 LLM이 부모 번호를 하위 세션에 그대로 붙이는 경우가 있어서다. 이 단계는 설명 계층을 붙이고 `slot_state`에 저장하기 전에 돈다. 그래서 팝업의 AI 이름도 보정된 번호를 따르고, 다음 증분 실행의 baseline도 보정된 값이다. 캐시된 슬롯도 매 실행 이 단계를 거쳐 다시 저장된다. AI가 비어 있으면 Session으로 바꿀 때 이름 앞의 번호를 쓴다(이 값은 저장하지 않는다).
3. `agenda_item` 또는 세션 이름을 `docs/agenda_item_description.json`과 연결해 설명 계층을 추가한다.
4. 시각은 각 세션의 셀이 표의 어느 줄에 있는지로 정한다(아래 "시각은 셀의 줄에서"). `specified_start_time`이 있으면 그 시각이 이긴다.
5. duration을 더해 최종 `start_time`과 `end_time`을 `HH:MM`으로 생성한다.
6. LLM으로 `group_header` 표기를 정리한 뒤, 남은 빈 group은 이름 일치 규칙으로 보완한다.
7. AI 번호가 없는 셀은 이름을 회의별 매핑 목록(`docs/ran1/agenda_label_map/<meeting>.jsonl`)에서 찾아 `inferred_agenda_items`를 채운다. 목록에 없는 이름만 LLM에 묻고 답을 목록에 덧붙인다(`agenda_labels.py`, CLAUDE.md의 "AI 없는 셀의 AI 추정").

### 같은 시각, 두 방의 같은 AI: 빌드 실패

LLM 해석 직후(위 6·7단계 전) `consistency.py`가 같은 날 서로 다른 방에서 시간이 겹치는 세션이 같은 AI 번호를 가졌는지 본다. RAN1은 한 AI를 두 방에서 동시에 다루지 않으므로, 이런 세션은 어느 source가 엉뚱한 방에 놓였다는 뜻이다. RAN1#126bis 목요일에는 Hiroki의 10.5.4.x가 Tamna BC Hall과 Yeongju B Hall 두 곳에 들어가면서 Tamna의 10.5.3.x가 사라졌다.

- 고치지 않는다. 빌드를 실패시켜 관리자가 알게 한다. 화면에는 직전에 배포된 일정이 그대로 남는다.
- 해당 슬롯의 `slot_state`는 지운다(`RejectedState`). 다른 실패처럼 상태를 되돌리면 같은 결과가 다음 실행에도 재사용되기 때문이다. 지운 파일은 실패한 빌드에서도 커밋된다.
- CI는 이 WG의 성공 기록(`docs/.build_state.json`)도 지운다. 소스가 그대로여도 다음 check가 빌드를 다시 하고(`no successful build recorded`), 지운 슬롯은 cold로 해석된다.
- cold 해석에서도 같은 중복이 나오면 다시 실패하고 슬롯을 다시 지운다. 사람이 원인을 고칠 때까지 실행마다 반복된다.
- AI 번호는 일정에 적힌 `agenda_item`만 본다. 추정한 `inferred_agenda_items`와 번호 없는 세션(식사, 휴식, 자리표시)은 보지 않는다.
- 세부 AI만 비교한다. 제목 수준의 번호(`10.5.2.x`, 또는 `10.5.2.1`이 있는 `10.5.2`)는 두 방에 동시에 있어도 된다. 같은 제목 아래에서 방마다 다른 세부 AI(`10.5.2.1`, `10.5.2.2`)를 논의할 수 있기 때문이다. 하위 항목이 있는지는 이 회의의 agenda와 일정에 나온 번호로 판단한다.

`specified_start_time`을 분으로 바꿀 수 없는 경우 현재 구현은 오류를 중단시키지 않고 그 셀의 줄에서 순서대로 놓는다. 따라서 잘못된 문자열이 들어와도 빌드는 성공할 수 있지만 배치 시각이 원문과 달라질 가능성이 있다.

### 시각은 셀의 줄에서

LLM 답의 순서로 시각을 정하지 않는다. 프롬프트가 정하는 순서는 방 그룹 안에서뿐이고, 여러 방 그룹(`ALL_ONLINE`, `ALL_ROOMS`)이 방 그룹들 사이 어디에 오는지는 LLM 마음이다.
RAN1#126bis 화요일 17:00~19:30은 원본도 답 내용도 같은데 재병합 때 `Early dinner`(`ALL_ROOMS`)가 온라인 방과 오프라인 방 사이로 왔다. 예전 방별 cursor는 이것을 순서대로 믿어서, 오프라인 방의 17:00 세션을 19:30~21:00으로 밀었다(그리드 밖으로 나갔다).

- **LLM은 세션마다 출처 셀의 id를 답한다(`source_cells`).** 시각을 판단하는 일이 아니라 찾아 적는 일이다. 프롬프트의 셀 머리에 id가 있다(`[cell c1a2b3 — RAN1_main]`). 증분 병합도 모든 칸을 글과 id로 보낸다.
  - 코드가 검사한다(`source_cell_problems`): 비어 있지 않고, 이 슬롯에 있는 id이고, Main Schedule 셀에서 온 세션은 그 셀의 방 안에 있다.
    글이 있는 Main Schedule 칸은 모두 어느 세션의 출처여야 한다. 빠진 칸은 페이지에서 사라지기 때문이다. 미정 칸도 적힌 그대로 남긴다(위 "미정 칸"). 모두 취소선인 칸만 예외다.
    Main Schedule에 그 방의 칸이 있으면 부의장 셀만 가리켜서는 안 된다(부의장 표는 내용으로 맞추고, 방 이름과 줄 구조를 믿을 수 없다). 틀리면 문제를 붙여 다시 묻고(3번), 끝내 틀리면 알림을 남기고 빌드가 실패한다.
  - 줄의 시각은 프롬프트에 넣지 않는다. 표 구조에서 나온 시각은 코드가 적용한다.
- **세션은 셀의 줄 시작부터 놓인다.** Main Schedule 셀을 가리키면 그 셀, 아니면 부의장 셀의 줄이다. 같은 방에서 앞선 세션 뒤에 이어 놓는다.
  줄은 문서 순서대로, 줄 안에서는 답의 순서대로 놓는다. 그래서 저녁이 답의 어디에 있든 오프라인 세션은 17:00에 시작한다.
- **줄의 시작은 문서가 말하는 만큼 정한다(`_row_bounds`).** 순서대로:
  1. 첫 줄은 블록 시작, 마지막 줄의 끝은 블록 끝, 시간 칸이 있는 줄은 그 시각
  2. parser-derived `fallback_start_time`(마지막 줄에 길이가 하나만 적힌 칸, `Early dinner (60)` → 18:30)
  3. 위 줄에서 끝나는 셀 중 가장 긴 것의 끝(`(60)`이 없어도 첫 줄의 90분 칸이 18:30을 말한다)
  4. 그 줄에서 시작하는 셀이 제때 끝나려면 시작해야 하는 시각
- **빈칸은 빈 시간이다.** 첫 줄에 칸이 없는 방은 자기 칸이 있는 줄까지 비어 있다. LLM에게 빈 세션을 만들게 하지 않는다. 길이를 지어내야 하기 때문이다.
- **셀에 들어가지 않으면 빌드가 실패한다.** 순서대로 놓은 세션이 셀의 마지막 줄 끝을 넘거나, 세션이 이 슬롯의 셀을 가리키지 않거나, 줄의 시작을 알 수 없으면 `SlotPlacementError`다.
  `parse_time_slots`가 모든 슬롯을 본 뒤 GitHub 이슈 알림(`RAN1: session times do not fit the schedule document`)을 남기고 `RejectedState`로 실패한다.
  해당 슬롯의 `slot_state`를 지우므로 다음 빌드가 그 슬롯을 cold로 다시 묻는다. 위 "같은 시각, 두 방의 같은 AI"와 같은 처리다. 같은 문제는 이슈에 한 번만 댓글로 남는다.
- **이력.** 처음(2026-02)에는 셀마다 따로 LLM에 물어 셀이 곧 위치였다. 부의장 문서를 함께 보려고 시간 블록마다 한 번에 묻게 바꾸면서 세션과 셀의 연결이 끊겼고, 방 그룹마다 블록 시작부터 쌓게 되었다.
  2026-08 세로 병합된 줄(저녁)을 다룰 때 파서가 셀 시작을 셌고, 처음에는 그 시각을 프롬프트에 `Explicit cell start time`으로 넣었다가, 따로 `fallback_start_time` 칸으로 답하게 했다가, 결국 프롬프트에서 빼고 코드가 이름과 열로 맞췄다(LLM이 추정 시각을 옮기거나 baseline에 남기지 않게).
  같은 때 여러 방 세션이 블록 시작에 놓이던 것을 방별 cursor로 고쳤는데, 이것이 답의 순서를 믿는 원인이 되었다. 지금 방식도 줄의 시각은 LLM에 주지 않는다. LLM은 매번 검사하는 셀 id만 답하고 시각은 코드가 빌드마다 다시 센다.
  블록을 넘지 않는 어긋남도 셀의 끝으로 잡는다. 예전처럼 블록 끝만 보면 저녁이 답의 맨 앞에 올 때(17:00 저녁, 나머지 18:00~19:30) 아무것도 걸리지 않는다.

## 6. Agenda 설명의 출처

화면에 표시되는 다음 계층은 schedule DOCX의 시간표 셀과 출처가 다를 수 있다.

```text
10 - Rel-20 Study of 6GR
10.5 - Multi-antenna system
10.5.3 - CSI acquisition and report
10.5.3.4 - Beam management for downlink and uplink
```

이 설명은 Agenda CSV/DOCX를 파싱해 만든 `docs/agenda_item_description.json`에서 결정론적으로 조회·결합된다. 반면 시간, 방, Chair, AI 번호와 duration은 schedule source의 셀 원문을 LLM이 구조화한 결과다.

## 7. 특정 일정의 출처 추적 방법

현재 slot state는 source별 hash, 최종 merge 결과, 그 merge에 쓴 칸(칸 이력)을 저장한다. 세션마다 출처 칸의 id(`source_cells`)가 있고, 칸 이력에서 그 id의 source·방·원문을 찾을 수 있다. 원본 파일까지 따라가려면 다음 순서로 본다.

1. `docs/ran1/index.html` 또는 `docs/ran1/slot_state/{Day}_{TB}.json`에서 최종 일정 확인
2. slot state의 `source_hashes`에서 참여한 source 확인
3. `docs/ran1/.schedule_state.json`에서 해당 실행이 선택한 실제 파일명 확인
4. Main 및 vice-chair DOCX의 같은 요일·시간 블록 원문 비교
5. `git log -S`로 일정이 처음 추가되거나 변경된 빌드 커밋 확인
6. 설명 계층은 `docs/agenda_item_description.json`의 source metadata와 항목 확인

이 절차로 원본을 확인할 수는 있지만, source 간 내용이 겹칠 때 자동으로 한 파일만 지목할 수는 없다. 정확한 자동 provenance가 필요하다면 향후 LLM schema에 source evidence를 추가하고 slot state에 저장해야 한다.

## 8. 경계 요약

| 항목 | 처리 주체 |
|---|---|
| 원본 미팅/파일 선택 | 결정론적 코드 |
| DOCX 표·요일·방·병합 셀 추출 | 결정론적 코드 |
| 표의 시간 행 → 표준 시간 블록 | 결정론적 코드 및 `HH:MM` 정규식 |
| 셀 원문 보존 | 결정론적 코드 |
| 모호한 vice-chair 방 이름 연결 | LLM 보조 |
| header/세션/AI/Chair/duration 의미 해석 | LLM |
| 셀 내부 explicit time range 해석 | LLM |
| source hash 및 freshness 판정 | 결정론적 코드 |
| cold/incremental/skip 선택 | 결정론적 코드 |
| agenda 설명 계층 결합 | 결정론적 코드 |
| `specified_start_time` 적용 및 최종 시각 계산 | 결정론적 코드 |
| group 이름 정규화 | LLM |
| AI 없는 셀 이름 → agenda 항목 | LLM (회의별 목록에 저장, 한 이름은 한 번만 묻는다) |
| HTML 생성 | 결정론적 코드 |


## 9. RAN1 chairman agreement 경로

`pipeline.build_schedule`은 timezone 결정과 독립적으로 `agreements.build_agreements`를 호출한다.
동일 미팅의 note만 선택하고 정확한 번호가 식별되는 agenda heading 사이의 원문 전체를 추출한다.
Agreement/Proposal 등의 표식은 경계로 사용하지 않는다. 모호한 공백 구분 문장과 본문 인용은 보존한다.

TDoc 행(ID 단독, 또는 탭으로 구분된 ID/제목/제출자)은 5행 이상 이어진 기고 목록일 때만 접는다(`_listed_tdocs`, `TDOC_LIST_ROWS`).
섹션 하나를 모아 놓고 연속된 행을 한 묶음으로 판단한다.

| 묶음 | 처리 | 예 |
|---|---|---|
| 4행 이하 | 원문 그대로 | AI 5의 수신 LS, `FL summary #2`, 승인된 CR 번호, mirror CR 3행 |
| 5행 이상 | `<details class="tdoc-list">`로 접는다. 보이는 줄은 `N TDocs` | AI마다 맨 위의 기고 목록, `Relevant Tdocs:` 아래 목록 |
| 5행 이상 묶음 끝의 summary 행(제목 `summary` 또는 제출자 `Moderator`), 뒤에 목록이 아닌 내용이 있을 때 | 접지 않는다 | 기고 40행 + `FL summary #1`, 빈 줄, `Agreement:` |

행 바로 아래의 괄호 주석(`(Late contribution)`, `(Revision of R1-…)`, `(Withdrawn)`)은 그 행과 함께 접히거나 남는다.
접힌 목록은 블록 키가 없고(변경 비교에서 빠진다), 목록뿐인 섹션은 접힌 줄도 없이 비워 둔다. `excluded_tdoc_rows`는 접힌 행 수다.
행을 HTML에 남기므로 #126bis 사전 note(Draft v01) 기준 `agreements/` 조각의 합계가 약 100KB에서 380KB로 는다(AI별로 따로 받는다).
표는 TDoc 목록 표(모든 행이 ID 행이거나 TDoc/Title/Source 머리행 아래 ID 행뿐)만 통째로 빼고,
TDoc 번호로 항목을 가리키는 표(`Tdoc number | Issue`)는 그대로 둔다.
기준 5행의 근거는 RAN1#124~#126bis note 6개의 조사다: 연속 행 묶음은 1행(맥락)과 10행 이상(기고 목록)으로 갈리고,
2~4행은 CR 묶음과 짧은 목록이 섞여 있으며, 5~9행은 모두 기고 목록이었다.

섹션은 `html`, `excluded_tdoc_rows`, 블록 키 목록 `blocks`를 저장한다(parser version 18).
LLM 요약을 사용하지 않는다. 별도 `agreements_ref`로 원본 파일/URL/SHA를 비교하므로
Portal timezone을 사용하거나 note의 이름이 그대로여도 내용 변경을 감지한다.

로컬 note(`ref_in_manual/ran1`, chair_notes extra_files)는 check와 build가 같은 함수
(`local_note_path`)로 고르므로 두 쪽의 `agreements_ref`가 어긋나지 않는다. check의 미팅도
다른 비교와 같은 `selected_meeting_id`다. zip에서 푼 문서는 안쪽 파일명 대신 원격 이름으로 미팅을 확인한다.

agreement는 선택적인 입력이라 실패해도 일정 빌드를 멈추지 않는다. 파싱에 실패하면 같은 미팅의
마지막 게시분(`docs/ran1/schedule.json`)을 유지하고 그 문서의 식별자는 저장해서, 같은 깨진 문서로
매시간 재빌드하지 않는다. 목록 조회나 다운로드 실패면 이전 식별자를 유지해 다음 check가 다시 시도한다.

### 변경 추적과 저장

note가 바뀌면 섹션마다 직전 게시분과 블록 키를 비교한다(`track_changes`). 블록은 Word 문단 하나 또는
표 하나이고, 키는 HTML이 아니라 Word XML의 텍스트에서 만든다(`_unit_key`). 그래서 공백, run 분할,
빠진 TDoc 목록(비교 전에 이미 빠짐), parser 출력 변경은 변경으로 세지 않고, 취소선은 센다.

parser version이 바뀌면 같은 note도 블록이 달라진다(남기는 TDoc 행이 늘어난 v18이 그렇다). 그 빌드에서는 차이를
note의 변경으로 읽지 않고, 직전의 변경 기록을 그대로 옮기며 강조 위치만 같은 키끼리 다시 맞춘다.
강조하던 블록이 모두 사라진 섹션은 기준선(`initial`)으로 돌린다. 같은 빌드에서 note도 바뀌었다면 그 변경은 다음 note부터 비교된다.

- 키가 같은 섹션은 직전의 변경 시각과 강조를 그대로 둔다. 바뀐 섹션은 `changed_at`·`changed_in`을 새
  note로 갱신하고, `difflib`의 insert/replace 블록을 추가분(`added`)으로 표시한다. 지워지기만 한 경우는
  날짜만 바뀐다. 미팅의 첫 note는 기준선(`initial`)이라 강조가 없고, 기준선 뒤에 생긴 섹션은 전부 추가분이다.
- 변경 시각은 원격 note의 `Last-Modified`(FTP MDTM)이고, 로컬 note는 처음 본 빌드 시각이다.
- 파싱할 때 블록의 첫 태그에 `data-unit`을 달고, 비교 뒤 `finish_units`가 추가분만 `agreement-added`
  클래스로 바꾼다. 섹션 HTML은 XML로 파싱되지 않으므로(닫지 않은 `<br>`, `<img>`) 다시 직렬화하지 않는다.

`schedule.json`에는 섹션의 메타데이터(제목, 키, 변경 정보, 파일 이름)만 두고, HTML은 옆의
`agreements/<내용 해시>.html`에 둔다(`package_agreements`, `save_schedule`). 바뀌지 않은 섹션은 같은
파일이라, 새 note는 바뀐 섹션의 파일만 커밋한다. 렌더링(`write_agreement_assets`)은 현재와 직전 빌드가
참조하지 않는 파일을 지운다. 직전 빌드의 파일(`retained_files`)은 열려 있는 페이지를 위해 한 번 더 남긴다.

원격 note는 식별자를 만들 때 받은 바이트를 그대로 저장해 파싱한다(두 번 받지 않는다). timezone 단계가 같은
미팅의 chair note를 이미 조회했으면 그 결과를 넘겨 받아 폴더를 다시 나열하지 않는다(`listed`).

공통 Schedule의 `chairman_agreements`는 선택적인 추가 필드다.
HTML 렌더링 시 `agreement_assets`가 manifest를 만들고 페이지에는 소형 manifest만 넣는다. RAN1 SolidJS island가 셀의 AI 탭 중 활성 AI만 lazy fetch한다.
셀의 `data-ai`는 필터용으로 일정 표기 그대로이고, 패널이 여는 섹션 목록(`10.6.x` → `10.6.1|10.6.2`)은
`data-agreement-ai`에 따로 둔다.
DOMPurify로 HTML을 정제하고 Shadow DOM에 직접 삽입하여 문서 CSS를 격리한다.
iframe이나 내부 스크롤, 선택에 따른 자동 스크롤은 없다. 기존 세션 상세 팝업은 유지한다.
수식은 OMML→MathML, WMF→SVG의 서로 다른 경로다.

캐시는 `.cache/ran1/agreements` 안에 parser version/이미지 backend/원본 SHA 단위로 저장한다.
출력은 `docs/ran1`에 포함되어 기존 WG별 rollback을 따른다.
지원 범위, 실제 문서 통계, 검증 명령은 [AGREEMENTS_VERIFICATION.md](AGREEMENTS_VERIFICATION.md)에 기록한다.

GFM 목록 깊이는 문단에 직접 지정한 위치 → 선택된 numbering level의 위치 → 상속 스타일 순으로 판정한다.
공통 List Paragraph 스타일의 들여쓰기가 모든 목록 단계를 평탄화하지 않도록 일반 문단 서식 cascade와 구분한다.

### 실제 일정 AI 표기와 heading 번호 깊이

RAN1 agreement 탭의 AI 값은 `agenda_section_ids`가 정규화한다. 원본 agenda_item/팝업 문자열은 보존한다.
번호는 자기 자신과 존재하는 하위 번호(`9.1` → `9.1.1`, `9.1.2`)로 확장하고, `.x`는 하위 번호만으로
확장한다. 하위 판정은 계층 단위(`10.4.` 접두)이므로 `10.4`가 `10.40.1`을 끌어오지 않는다. 끝 마침표와
번호 뒤 제목 접미어를 제거하고, 슬래시 표기는 완전한 번호 또는 동일 부모의 형제 번호로 해석한다.
본문 없는 항목(하위 번호만 있는 heading, 아직 합의가 없는 항목)은 정상 상태다. 패널은 본문이 있는 첫
탭을 먼저 열고, 빈 항목은 오류가 아닌 안내 문구로 표시한다.
Word heading 카운터는 선택한 ilvl과 숫자 템플릿의 깊이를 사용하며, outline 스타일이 템플릿보다
깊으면 제한한다. `%1.1` 같은 고정 접미어와 기존 #124 번호 복원도 회귀 검사한다.
