# 스케줄 파싱 작동 원리

이 문서는 DOCX 원본이 `docs/index.html`의 일정으로 변환될 때 **결정론적 코드가 처리하는 부분**과 **LLM이 해석하는 부분**을 구분한다. 장애 분석이나 특정 일정의 출처를 추적할 때는 이 경계를 기준으로 확인한다.

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
Session 모델 → docs/index.html
```

## 1. 원본 파일 선택과 다운로드

`downloader.py`와 `main.py`가 다음 정보를 이용해 입력을 선택한다.

- 미팅 ID 우선순위와 업로드 시각
- `ref_in_manual/`의 로컬 참조
- 이전 실행의 `docs/.schedule_state.json`
- Chair schedule, vice-chair schedule, Agenda 파일의 종류

이 단계의 선택 규칙과 파일 비교는 결정론적이다. 같은 파일 목록, 설정, 상태를 입력하면 같은 파일이 선택된다. 네트워크 목록이 일부만 반환되거나 다운로드가 실패하는 경우에는 이전 상태를 보존하는 방어 로직이 적용될 수 있다.

성공한 실행은 선택한 파일명, `meeting_id`, timezone 관련 정보를 `docs/.schedule_state.json`에 기록한다.

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

각 source는 방 label, 셀 원문, prompt version을 포함해 hash된다. 이 hash와 `docs/slot_state/{Day}_{TB}.json`의 이전 hash를 비교하여 다음 상태를 정한다.

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

1. `docs/index.html` 또는 `docs/slot_state/{Day}_{TB}.json`에서 최종 일정 확인
2. slot state의 `source_hashes`에서 참여한 source 확인
3. `docs/.schedule_state.json`에서 해당 실행이 선택한 실제 파일명 확인
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

