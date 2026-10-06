# 스케줄 파싱 작동 원리

이 문서는 DOCX 원본이 `docs/ran1/index.html`의 일정으로 변환될 때 **결정론적 코드가 처리하는 부분**과 **LLM이 해석하는 부분**을 구분한다. 장애 분석이나 특정 일정의 출처를 추적할 때는 이 경계를 기준으로 확인한다.

이 문서는 RAN1 파이프라인을 설명합니다. 아래의 파서·모델·세션 모듈 이름은
`working_groups/ran1/` 기준이며 공통 HTML 생성기는 `shared/renderer.py`,
화면 템플릿은 `templates/`에 있습니다. 시간대는 Portal API를 우선 사용하며
실제 시작·종료 시각에 따른 NOW 제어는 공통 화면에서 처리합니다.

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
  ├─ [LLM 보조] vice-chair 문서의 모호한 Room A/B 이름 해석
  ├─ [결정론적] source hash 및 이전 slot state 비교
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

결과는 `models.py:CellData`이며 주요 필드는 다음과 같다.

```text
text                 DOCX 셀에서 추출한 원문
day                  요일
room_indices         셀이 차지하는 방 열
time_block_index     표의 시간 행이 속한 표준 시간 블록
time_block_start/end 표준 시간 블록 경계
fallback_start_time  표 구조로 안전하게 유추된 예외적 시작 시각
```

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
- Main과 완전히 같은 vice-chair 셀 텍스트는 중복 제거한다.
- 물리 방 이름은 LLM 입력에서 안정적인 `RAN1_main`, `RAN1_brk1`, `RAN1_off1` 등의 alias로 바뀐다.

vice-chair 문서가 방 이름 대신 `Room A`처럼 모호한 이름만 제공하면 `merger.py:_resolve_vc_room_names()`가 문서의 표 앞 문맥을 LLM에 보내 Main schedule의 방과 연결한다. 즉, 이 부분은 구조 파싱 중 예외적으로 LLM의 도움을 받는다. 결과는 `.cache/`에 저장될 수 있다.

각 source는 방 label, 셀 원문, prompt version을 포함해 hash된다. 이 hash와 `docs/ran1/slot_state/{Day}_{TB}.json`의 이전 hash를 비교하여 다음 상태를 정한다.

- `STALE`: 입력이 같음
- `FRESH`: 기존 source의 내용이 바뀜
- `NEW`: 새 source가 생김
- `REMOVED`: 이전 source가 사라짐

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

### Cold, incremental, short-circuit

슬롯별 처리 경로는 세 가지다.

| 경로 | LLM에 전달되는 데이터 | 사용 조건 |
|---|---|---|
| Cold | 현재 모든 source의 원문 | 이전 slot state가 없거나 `--rebuild-slots` 실행 |
| Incremental | 이전 merge baseline + 변경된 source 원문 | 이전 state가 있고 일부 source가 변경됨 |
| Short-circuit | 전달 없음 | 모든 source가 `STALE`; 이전 merge 결과 재사용 |

`force-deploy`는 `.cache/`와 상태 파일을 지우고 `main.py --rebuild-slots`로 실행하므로 모든 시간 슬롯이 이전 baseline 없이 cold 경로로 재생성된다.

## 5. LLM 응답 이후의 결정론적 처리

LLM 응답을 그대로 HTML에 쓰지는 않는다. 코드가 다음 후처리를 수행한다.

1. Main room의 chair는 Main schedule에 명시적 근거가 있을 때만 유지한다.
2. `agenda_item` 또는 세션 이름을 `docs/agenda_item_description.json`과 연결해 설명 계층을 추가한다.
3. `specified_start_time`이 있으면 `time_to_minutes()`로 분 단위로 바꾼다.
4. 값이 없으면 방별 cursor를 이용해 앞 세션 종료 직후에 순차 배치한다.
5. parser-derived `fallback_start_time`이 적용되는 예외라면 해당 시작 시각을 사용한다.
6. duration을 더해 최종 `start_time`과 `end_time`을 `HH:MM`으로 생성한다.
7. LLM으로 `group_header` 표기를 정리한 뒤, 남은 빈 group은 이름 일치 규칙으로 보완한다.

`specified_start_time`을 분으로 바꿀 수 없는 경우 현재 구현은 오류를 중단시키지 않고 그 방의 순차 cursor로 fallback한다. 따라서 잘못된 문자열이 들어와도 빌드는 성공할 수 있지만 배치 시각이 원문과 달라질 가능성이 있다.

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

현재 slot state는 source별 hash와 최종 merge 결과를 저장하지만, **각 세션이 어느 source의 어느 셀에서 유래했는지에 대한 per-session provenance는 저장하지 않는다.** 따라서 다음 순서로 역추적한다.

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
| HTML 생성 | 결정론적 코드 |


## 9. RAN1 chairman agreement 경로

`pipeline.build_schedule`은 timezone 결정과 독립적으로 `agreements.build_agreements`를 호출한다.
동일 미팅의 note만 선택하고 정확한 번호가 식별되는 agenda heading 사이의 원문 전체를 추출한다.
Agreement/Proposal 등의 표식은 경계로 사용하지 않는다. TDoc ID 단독 행, 탭으로 구분된 ID/제목/제출자 행,
표의 단순 TDoc 메타데이터 행만 제거하며 이후 내용은 유지한다. 모호한 공백 구분 문장과 본문 인용은 보존한다.
섹션은 `html`, `excluded_tdoc_rows`, 블록 키 목록 `blocks`를 저장한다(parser version 17).
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
TDoc 행(비교 전에 이미 빠짐), parser 출력 변경은 변경으로 세지 않고, 취소선은 센다.

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
