# RAN1

RAN1에만 해당하는 규칙이다. 모든 WG에 공통인 규칙은 루트 `CLAUDE.md`에 있다.

## 팝업 AI 트리의 drafts 폴더 링크

- **트리의 항목마다(상위 항목 포함) 그 항목의 drafts 폴더로 가는 `↗`를 단다(`drafts_folders.py`가 `drafts_url`을 붙이고, 렌더러의 `_drafts_link`가 그린다).** Agreement 패널에는 달지 않는다.
- 폴더 이름은 FL이 손으로 줄여 쓴 것이라(`10.5.2.2 (DL transmission scheme for shared)`) agenda에서 만들 수 없다. FTP 목록에서 앞 번호로 맞춘다. 폴더는 번호대로 겹쳐 있다(`10.5.2 (…)/10.5.2.2 (…)/`).
- sync 폴더(`Meetings_3GPP_SYNC/RAN1/Inbox/drafts/`, `config.json`의 `meeting_sync`)를 쓴다. 회의 폴더(`…/TSGR1_127/Inbox/drafts/`)는 sync보다 늦게 따라온다(2026-11-20 결정).
  - sync에는 다음 회의 전까지 지난 회의 폴더가 남는다. 폴더 이름에 회의가 없으므로 회의 기간(첫날~마지막 날, 회의 시간대)에만 목록을 읽는다. 이미 찾은 링크는 회의가 끝나도 그대로 둔다.
  - sync가 다음 회의로 다시 만들어지면 끝난 회의 페이지의 링크는 깨진다.
- 찾은 폴더는 `docs/ran1/drafts_folders.json` 한 파일에 빌드하는 회의의 것만 두고(`meeting_id`가 다르면 새로 시작한다, 이력은 커밋), 목록에 없는 항목이 나올 때만 FTP를 읽는다. 폴더가 없는 항목(`10.5`)은 다음 빌드에서 다시 찾는다. 회의 중에 폴더가 생긴다. 목록 실패는 빌드를 실패시키지 않는다.
- `10.6.x` 같은 x 항목은 `10.6` 폴더를 가리킨다. 그 폴더가 없으면 그 줄에는 링크가 없고, 하위 항목 줄은 각자 링크가 있다.

## 빌드 알림

- **RAN1 세션 시각이 문서의 줄과 맞지 않아도 알리고 빌드가 실패한다.** 시각은 LLM 답의 순서가 아니라 세션이 가리킨 출처 셀(`source_cells`)이 표의 어느 줄에 있는지로 정한다. 세션이 셀의 끝을 넘거나 출처 셀을 끝내 답하지 못하면 이슈 알림을 남기고, 그 슬롯을 다음 빌드에서 처음부터 다시 묻는다(`PARSING_ARCHITECTURE.md`의 "시각은 셀의 줄에서").

## 메일로 온 일정

공용 구조는 루트 `CLAUDE.md`의 "메일로 온 입력 파일"에 있다.

- **RAN1(`downloader.py`의 `mailed_schedule_files`, `_with_mail`, `_mailed_documents`):** 메일 첨부는 FTP에서 찾은 문서(Main 또는 부의장 문서)의 새 판이거나, FTP에 아직 없는 새 일정이다.
  - 같은 문서인지는 판 번호·`Draft`·Final/EOM 표시를 뺀 파일 이름으로 가린다(`schedule_stem`). 이름만으로는 누구 문서인지 알 수 없다(`RAN1#126b Sorour sessions … schedules - v00`에는 `schedule for`가 없다). FTP는 폴더로 가른다.
  - FTP에 아직 없는 문서도 메일로 오면 쓴다(`_mailed_documents`). 다음 회의의 첫 일정(RAN1#127 `Draft … schedules - v00`은 2026-11-08 메일로 먼저 왔고 FTP `Chair_notes`는 비어 있었다)과 회의 중 부의장이 메일로 보낸 새 일정이다.
    - 누구 문서인지는 지난 회의의 같은 문서(회의 번호까지 뺀 이름, `_document_name`)의 폴더로 정한다. 없으면 이름의 사람(`schedule for Hiroki`), 그것도 없으면 Main이다. 지난 회의에 있던 문서가 먼저 자리를 잡는다.
    - FTP가 이미 채운 자리(그 회의의 Main, 그 부의장)는 차지하지 않는다. 메일 Main이 더 새 회의면 그 회의로 넘어간다(지난 회의가 끝나지 않았으면 `held_meeting` 잠금 때문에 넘어가지 않는다).
  - 후보는 빌드한 회의와 다음 회의(`read_meetings`, `meeting_turn.to_read`)의 첨부뿐이다.
  - 빌드한 메일본은 상태 파일(`.schedule_state.json`의 `files`)에 메일 주소까지 적고, 다음 실행의 후보로 늘 다시 넣는다. 첨부는 아카이브에서 사라지지 않으므로 캐시를 잃어도 내려가지 않는다. check도 같은 후보로 비교한다.
  - 소스 이름(`Main Schedule`)과 폴더는 출처와 상관없이 그대로다. 이름이 바뀌면 모든 슬롯이 `NEW`/`REMOVED`로 증분 재병합된다.
  - 메일본 Main은 `main_history`에 더하지 않는다(`record(..., store=False)`). 영구 기록이라 잘못된 메일본이 부의장 칸을 사본으로 지울 수 있다.
  - 로컬 참조(`ref_in_manual/`, `extra_files`)는 메일로 바뀌지 않는다.

## Agreement 패널: 체어맨 노트

패널 UI는 루트 `CLAUDE.md`의 "Agreement 패널 (공용)"에 있다. 여기는 RAN1이 패널에 넣는 체어맨 노트를 읽는 규칙이다(`agreements.py`).

- **TDoc 행은 5행 이상 이어진 목록일 때만 접는다(`agreements.py`의 `_listed_tdocs`, `TDOC_LIST_ROWS`).** 본문이 가리키는 TDoc까지 빼면 무엇에 대한 글인지 알 수 없다. AI 5에서 LS 행이 빠지면 설명만 남는다.
  - 4행까지는 원문 그대로 둔다. 수신 LS, FL summary, 승인된 CR 번호, 릴리스별 mirror CR이 여기에 든다.
    실제 note에서 이어진 행은 1행(맥락)과 10행 이상(기고 목록)으로 갈리고, 5~9행은 모두 기고 목록이었다.
  - 접은 자리에는 회색 한 줄 `▸ 48 TDocs`를 두고, 누르면 그 자리에서 목록이 펼쳐진다(`<details class="tdoc-list">`, 닫힌 채로 시작).
    원문을 버리지 않고, `Relevant Tdocs:` 같은 머리말이 가리킬 것을 잃지 않는다.
    - 줄은 원문에 없는 글이라 `--ink-3`로 구별한다. 펼친 행은 본문보다 한 단계 약하게(0.93em, `--ink-2`) 들여 쓴다. 상자나 테두리는 두지 않는다.
    - 펼친 상태는 기억하지 않는다. 탭을 바꾸면 다시 접힌다.
    - 패널의 DOMPurify 허용 태그에 `details`·`summary`가 있어야 한다. 빠지면 태그만 지워져 목록이 전부 펼쳐진 채 보인다.
    - 접힌 목록은 변경 비교에 넣지 않는다. 넣으면 late contribution 하나에 그 AI가 Updated로 표시된다.
  - 목록만 있는 AI는 접힌 줄도 넣지 않고 비워 둔다. 패널의 빈 항목 안내가 같은 말을 하고, 본문이 있는 첫 탭을 먼저 여는 동작이 그대로다.
  - 목록 끝에 붙은 summary 행은 뒤에 내용이 이어질 때 남긴다. 의장은 기고 목록 끝에 `FL summary #1`을 붙이고 그 아래에 합의를 적는다.
    목록 중간의 summary 행은 번호만 잡아 둔 것이라 함께 접는다. 제목 낱말로 행의 종류를 가리는 규칙은 이것 하나뿐이다.
  - 행 아래의 괄호 주석(`(Late contribution)`, `(Revision of R1-…)`)은 그 행을 따라 접힌다. 주석만 밖에 남지 않게 한다.
  - 표는 TDoc 목록 표만 통째로 뺀다. `Tdoc number | Issue`처럼 TDoc으로 항목을 가리키는 표는 행을 빼지 않는다. 일부 행만 빠진 표는 원문과 다른 말을 한다.
- **회의가 끝나면 체어맨 노트는 Final·EOM 파일로 이어진다(`downloader.py`의 `_release_key`, `_closing_stage`).** 마지막 `vNN` 뒤에 오고 버전 번호 없이 자기 번호를 센다.
  - 순서는 정식본 > draft, 그다음 `vNN` < Final < EOM, 그다음 각자의 번호다. EOM끼리는 `eom`(= `eom0`) < `eom1` < `eom2` …이고 Final도 같다(`final` < `final1`). 업로드 시각은 같은 키일 때만 가른다.
  - Final은 EOM 전에 쓰던 표기다(#104bis-e, #105-e: `v012` 다음 `final`). 지금은 쓰지 않지만 다시 쓸 수 있어서 받는다. 둘이 함께 나오면 EOM이 나중이다.
  - 표기는 FTP(`TSGR1_111` ~ `TSGR1_126`의 `Inbox/Chair_notes`)에서 본 그대로 받는다: `_eom`, ` eom0`, `_eom1`, ` - eom3`, ` - eom_01`(#124). 번호는 건너뛰기도 하고(#118: `eom2` → `eom4`) `eom1`부터 시작하기도 한다(#116bis).
  - 회의 중에 앞 버전이 지워져 `v00`과 EOM만 남기도 한다(#126: `v00.docm`, `eom`, `eom1`). 번호 없는 `eom`을 버전 없음(-1)으로 읽으면 `v00`이 이긴다.
  - 선택, 로컬 캐시, check 잡의 downgrade 판정이 모두 이 키를 쓴다. 세션 노트도 같은 키다.
- **체어맨 노트는 이름의 `Chair notes`·`Chairman notes`·`Chair's notes`로 찾는다(`_is_chair_notes_name`).** `Chair's Notes`는 #115까지 쓰던 표기이고, Final을 쓰던 회의도 이 표기였다. 낱말 사이는 공백이나 `_`다.
  - 회의 번호의 `#` 자리에 `_`를 쓴 이름(`RAN1_111 v03`)도 같은 회의다(`_MEETING_ID_PATTERN`). #110bis와 #111은 회의 도중 `RAN1#111`에서 `RAN1_111`로 바뀌어서, 읽지 못하면 `#111` 묶음의 `v02`가 마지막 버전이 된다.
- 문서 형식이나 출력이 바뀌면 `working_groups/ran1/agreements.py`의 `PARSER_VERSION`을 올려 캐시를 무효화한다.

## Agreement 패널: 세션 노트

부의장(Hiroki, Sorour) 세션의 합의는 회의 중에는 부의장의 세션 노트에만 있다. 의장은 언제인지 정해지지 않은 때(EOM 등)에 그것을 체어맨 노트로 옮긴다.
그래서 패널은 AI마다 **최신 체어맨 노트와 최신 세션 노트를 합친 상태**를 보이고, 변경도 합친 상태를 이전 빌드의 합친 상태와 비교한다. 코드는 `agreements.py`다.

- **찾기:** 각 Inbox의 `*_notes` 폴더(`_extract_person_name`)에서 `Session Notes of AI …` 파일을 찾고, TDoc 번호마다 최신 버전 하나를 쓴다(정식본 > draft, 그다음 버전).
  파일 이름에 회의가 없어서 본문 머리줄(`3GPP TSG RAN WG1 #126bis`)로 회의를 확인하고, 다른 회의 것이면 뺀다. AI는 `Agenda Item:` 줄로 정한다.
- **번호:** 세션 노트는 Word 번호를 새로 매겨서 10.5.4.1이 `10.5.0.1`, 9.2가 `8.1`로 읽힌다. 첫 제목을 `Agenda Item:`에 맞추고(`_rebase`),
  체어맨 노트에 제목이 같은 상위·하위 항목이 있으면 그 번호에 맞춘다(10.2.1 노트가 10.2 제목으로 시작한다). 하위 섹션은 상대 위치를 따른다.
- **합치기(`_merge_parts`):** 체어맨 노트 순서를 기준으로 블록 키를 정렬해 맞춘다. 같은 블록은 한 번, 한쪽에만 있는 블록은 그 자리에 둔다.
  - 같은 자리의 90% 넘게 같은 문장은 한 문장을 고친 것으로 보고, **나중에 올라온 문서**의 문장 하나만 둔다.
  - 접힌 TDoc 목록은 하나로 보고 행이 많은 쪽을 둔다. 빈 줄만 있는 추가분은 넣지 않는다.
  - #126 EOM과 최종 세션 노트를 이렇게 합치면 AI마다 0~1블록이 겹쳐 남는다(의장이 한 줄을 두 문단으로 나눈 경우 등). 더 느슨하게 맞추면 세션에만 있는 합의를 지울 수 있어서 그대로 둔다.
- **비교:** 합친 블록 키를 이전 빌드와 비교한다. 세션 노트의 블록이 체어맨 노트로 옮겨 와도 변경이 아니다.
  섹션마다 문서별 몫의 digest(`sources`)를 두고, 몫이 바뀐 문서 중 가장 늦은 것을 `changed_in`으로 적는다(`Updated … · Session notes draft v02`).
  - 세션 노트를 처음 읽는 빌드(이전 스냅숏에 `session_notes`가 없음)는 기준선이다. 이미 진행 중인 회의에서 세션 노트 전체가 강조되지 않는다.
    그 뒤에 새로 올라온 세션 노트는 추가로 강조된다.
- **실패:** 세션 노트 하나를 읽지 못하면 그 노트가 들어 있던 AI는 직전에 게시한 섹션을 그대로 둔다. 목록·다운로드 실패는 체어맨 노트와 같이 빌드 전체가 이전 결과를 유지한다.
- **변경 감지:** check 잡의 식별값은 체어맨 노트 식별값에 `session_notes`(이름, URL, 업로드 시각)를 더한 것이다. 세션 노트는 버전마다 이름이 바뀌므로 내려받지 않고 목록만 본다.
  세션 노트가 없으면 예전 식별값과 같다.

## 이메일 토론

FL이 이메일 토론 스레드에 올리는 세션 계획("Offline 11:30 ~ 12:30: Part 1 PUSCH MIMO")을 모아 셀과 팝업, Agreement 패널에 보인다. 수집은 `working_groups/ran1/email_sessions.py`, 테스트셋은 `tests/fixtures/ran1/listserv_126bis/`다.

- **스레드의 AI는 Chair notes가 정한다.** AI 절마다 `[126bis-R20-MIMO] Email discussion on Rel-20 MIMO – Darcy (MTK)` 줄이 있고, 대괄호 태그가 메일 제목의 태그다.
  - `agreements.py`가 체어맨 노트를 읽을 때 이 줄을 `email_threads`(AI, 태그, FL, 회사)로 함께 낸다. 태그는 회의마다 바뀌므로 회의마다 그 회의의 노트에서 다시 모은다.
  - 스냅숏(`schedule.json`의 `chairman_agreements`)에만 두고 페이지 데이터에는 넣지 않는다. 노트를 읽지 못하면 직전 결과가 그대로 남는다.
  - 세션 노트의 같은 줄은 쓰지 않는다(`_rebase`가 옮기지 않는다).
  - `Email discussion`은 손으로 친 글이라 깨져 있다(#126bis v06의 `Ema-il discussion` 두 줄). 글자 사이의 하이픈·공백을 허용하고, 태그 없이 낱말만 있는 줄도 `tag: null`로 남긴다.
- **메일은 제목의 첫 대괄호 태그로 스레드에 붙인다(`thread_of`).** `Re:`, `回复:`, 태그만 남은 제목, 태그 뒤를 고친 제목이 모두 같은 스레드다.
  - 태그는 회의 부분(`126bis`, `126b`)을 떼고 나머지로 비교한다. 나머지가 이번 회의 스레드와 같은데 회의가 지난 번호(`126-`)이면 지난 태그를 그대로 쓴 것으로 보고 붙인다. `Post-126-…`은 지난 회의의 후속 토론이라 붙이지 않는다.
- **메일에 적힌 AI 번호도 반드시 본다(`agenda_of`).** 태그가 없거나 깨진 메일, 여러 AI를 묶은 스레드(9.3 A-IoT, 8.2)는 번호로만 AI를 알 수 있다.
  - 형식: `AI 8.2`, `agenda item 9.4.1`, `(Agenda 9.5.1)`, `[9.3.3]`, 번호만 적은 것(`10.5.2.2`), drafts 폴더 경로의 가장 깊은 번호. 이 회의 Agenda에 있는 번호만 센다.
  - 번호 앞에 다른 것임을 밝히는 낱말이 있으면 세지 않는다(`Proposal 3.1.1-4`, `Section 8.2`, `Clause 8.1`, `v10.2`, `TS 38.213`). 낱말은 낱말 경계로 맞춘다. 한 글자(`r`)를 넣었다가 `for 10.5.1.2`를 놓친 적이 있다.
  - 스레드가 있으면 그 AI이고, 적힌 번호가 그 아래이면 좁힌다. 바깥 번호는 `other`로 따로 둔다(10.6.x 세션을 짜는 메일의 10.6.1.2, 10.6.3). 스레드가 없으면 제목, 없으면 본문의 번호다.
  - 보낸 사람이 Chair notes의 FL인지는 거르는 기준이 아니다. 묶인 스레드의 하위 주제 담당자는 FL 목록에 없다.
- **A(셀 세부 정보)는 online·offline 세션이고, B(Agreement 아래 부차 정보)는 off-offline이다.** online과 offline은 늘 방에서 열리므로 시간표의 세션이다. 방이 없거나 방 밖에서 모이면(`outside Sorour's session room`, `in front of Yeongju A Hall`) off-offline이다.
  - **online은 메인 방 세 개(Main, Brk1, Brk2) 중 하나에서 열린다.** 의장의 일정 문서는 표마다 무엇인지 제목을 단다.
    `RAN1#126b Online Session Schedule` 아래 표가 online 방(#126bis: Tamna BC Hall, Yeongju A Hall, Yeongju B Hall), 다음 쪽의 `RAN1#126b Offline Session Schedule` 아래 표가 offline 방(201, 203)이다. #126 v04도 같다.
    - 파서가 표 바로 위 글(`context_text`)의 마지막 `Online`/`Offline` 낱말을 방의 `RoomInfo.kind`로 남긴다(`rooms.table_kind`). 그 글은 앞 표부터 이어져 방 이름 글상자가 먼저 오므로 마지막 낱말이 그 표의 제목이다.
    - 제목이 둘 다 아닌 표만 있거나 kind를 읽기 전에 저장한 일정은 표 순서로 정한다(첫 표가 online). 회의 중에는 표 순서가 바뀌지 않는다고 본다.
    - 정의는 `working_groups/ran1/rooms.py`의 `online_rooms` 한 곳이고, 부의장 표의 방 이름을 맞추는 `merger.py`도 같은 것을 쓴다.
    - 셀은 모든 방이 online 방이면 online이다(`Cell.online`). 제목도 없고 표도 하나뿐인 날은 알 수 없다(`None`, 거르지 않는다).
  - 방 이름 자체로는 가르지 않는다. 방 이름은 회의마다 다르고, off-offline 안내도 방 이름을 쓴다.
  - 시각으로도 가르지 않는다. 시각 없이 "오늘 오후 offline"이라고만 쓴 메일이 있고, 적힌 시각이 시간표와 다른 메일도 있다.
  - "today", "tomorrow"는 회의 시간대로 바꾼 송신 시각으로 읽는다. 메일 날짜는 보낸 사람의 시간대(`+0800` 등)이고, 자정을 넘겨 쓴 "tomorrow"는 그날이다.
- **시간표에 없는 시각의 online은 그 AI가 쓰는 online 방의 그 시각 셀에 "maybe <AI>"로 붙인다.** FL이 의장·부의장과 정해 두었는데 시간표가 아직 바뀌지 않은 경우다.
  - offline은 붙이지 않고 B로 보낸다. online은 의장이 있어야 열리지만, FL은 off-offline도 offline이라고 부른다. #126bis 9.5: "start the offline at 3:30pm" 다음 날 "off-offline … same place as yesterday".
  - #126bis 10.3.1: "online session this morning from 12:00"인데 12:00에 10.3.1이 없다. 10.3.1 online은 모두 Yeongju A Hall이고, 그 방 12:00 칸이 AI 8.1이다. 8.1 셀에 "maybe 10.3.1"을 두고 10.3.1 필터에도 걸리게 한다.
  - 방은 시간표의 열에서 읽는다. 방 이름은 `day.rooms[room_col_start − 2]`다(1열이 시간 축). 열 번호를 그대로 쓰면 두 칸 밀린다.
- **추출은 스키마로 묶은 LLM이 하고, 판정은 코드가 한다(`email_details.py`, Gemini thinking `low`).**
  - LLM에 주는 것: 제목, 회의 시각으로 바꾼 송신 시각, 스레드의 AI, 그 AI의 남은 시간표 세션(id·요일·시각·방과 그 방이 online/offline 방인지·셀 이름), 같은 스레드에서 앞서 공지된 일정, 최신 답장.
    "FL proposal for online"은 앞선 "online from 12:00"을 알아야 읽힌다. 자정~05:00에 보낸 메일에는 "보낸 사람에게 오늘은 아직 전날일 수 있다"를 한 줄 더한다.
  - LLM이 답하는 것: 항목마다 kind(`session`·`informal`), 세션 id, online/offline, 적힌 요일·시간대(`part_of_day`: morning·afternoon·evening)·시각, 장소, 다룰 내용, 시간 나눔(`split`), 입력 마감(`input_due`), 80자 요약.
    "offline/online on Wednesday"는 그날 그 AI의 세션마다 항목 하나씩이다. 세션에 걸린 입력 마감("1 hr before our offline")은 그 세션의 세부 정보다.
  - 코드가 하는 것: 메일이 한 말에 맞는 세션을 지금 시간표에서 찾는다(`_bind`). 없으면 tentative 자리를 찾고, 그것도 없으면 버린다.
  - **세션은 LLM이 고른 id가 아니라 메일이 한 말로 정한다.** 맞는 세션은 그 AI를 다루고, 메일 뒤에 시작하고, online/offline 방·요일·시간대·시각이 메일과 맞는 셀이다.
    - 고른 id가 아직 맞으면 그 셀이다. 맞지 않으면 맞는 셀이 하나뿐일 때만 그 셀이다. id도 요일도 시각도 없는 항목은 붙이지 않는다.
    - 맞는 셀이 여럿이면, 고른 id가 시간표에서 사라졌거나 다른 AI가 되었을 때만 그 id의 요일·시간대로 가린다. id가 그대로 있는데 메일과 맞지 않으면(offline인데 online 방) 그 id의 시각은 아무것도 말하지 않으므로 붙이지 않는다.
    - 시간대는 시각을 가정하지 않고 그날의 휴식 구간으로 읽는다(`day_parts`). 점심 전이 morning, 점심 뒤가 afternoon, 점심 뒤 마지막 휴식 뒤가 evening이다(#126bis: 13:00 전, 14:30부터, 17:00부터). "afternoon"은 evening 블록도 포함한다. 점심이 없는 날은 시간대로 거르지 않는다.
    - 그래서 시간표가 바뀌면 세부 정보가 따라가고, 의장의 forecast로 미리 쓴 메일("After discussing with Mr Chair, … online tomorrow. Detailed slots to be updated.")은 시간표에 그 세션이 생기는 빌드에서 붙는다. 다시 묻지 않는다.
    - #126bis 10.5.2.2: "Friday morning online"을 물을 때 금 09:30 Yeongju A Hall(`10.5.2.x`, Sorour)이 유일한 후보였다. 의장이 09:30 10.5.2.1, 11:00 10.5.2.2(의장), 08:30 201 10.5.2.2(Sorour)로 바꾼 뒤에도 세부 정보가 10.5.2.1 셀에 남았다. 지금은 online 방의 11:00 하나만 맞는다.
  - **LLM이 준 시각은 메일에 있는지 코드가 확인한다(`times_in`).** 그 메일이나 같은 스레드의 앞 메일에 없는 시각은 지운다. 9.3.3 "For Tuesday Online session …"에 LLM이 17:00을 붙여 다른 셀로 갈 뻔했다.
  - **시각은 메일에 적힌 것만 쓴다.** 길이만 적힌 나눔("10.6.1.2: 40 min")은 길이로, "1 hr before" 마감은 그대로 둔다. 셀 시작에서 시각을 셈하지 않는다. 세션이 제시각에 시작한다는 보장이 없다.
  - TDoc 번호는 LLM에 맡기지 않고 최신 답장에서 패턴으로 뽑는다. 미리보기의 링크 대상(`…<https://…R1-2607194…docx>`)에도 번호가 있어서 링크를 지우기 전에 읽는다.
  - 평가는 `scripts/eval_email_details.py`다. #126bis 테스트셋에서 recall 0.971(34/35), precision 1.0이고, 새로 물은 두 번째 실행도 같았다. 남은 하나는 #39의 비공식 일정 두 개가 하나로 합쳐진 것이다.
- **메일 하나는 한 번만 읽고 한 번만 묻는다(`email_updates.py`).** 메일마다 한 줄씩, LISTSERV 메시지 id로 회의별 목록에 남긴다.
  - 목록은 두 곳이다. `docs/ran1/email_sessions/<meeting>.jsonl`(빌드가 커밋)과 `.cache/ran1/email_sessions/`(CI 캐시, 빌드가 실패해도 저장된다).
    빌드가 실패하면 `docs/`는 되돌아가지만 캐시는 남는다. 다음 빌드는 둘을 합쳐 읽고, 한쪽에만 있는 줄을 다른 쪽에 채운다. 그래서 실패한 빌드에서 쓴 호출도 다시 하지 않는다.
  - 목록에 있는 메일은 본문을 다시 받지도, 다시 묻지도 않는다. 판정을 고치려면 같은 id로 줄을 덧붙인다(뒤의 줄이 이긴다). 지시문이 바뀌어도 기존 답은 다시 묻지 않는다(`prompt_version`만 남긴다).
  - 적지 않는 것은 다음 실행에서 다시 한다: 본문을 못 받은 메일, 실패한 호출, API 키가 없는 실행, 실행당 호출 한도(`MAX_ASKS`)를 넘은 메일.
  - 스레드나 AI 번호가 없는 메일(공지, 분실물, `Post-126-…`)은 적지 않는다. 제목만으로 가리므로 다시 봐도 비용이 없고, Chair notes가 나중에 스레드를 더하면 그때 잡힌다.
  - 묻지 않고 `skipped`로 적는 메일: AI를 정할 수 없는 메일, 최신 답장에 시각·장소를 가리키는 낱말이 하나도 없는 메일(`SCHEDULE_WORDS`). 테스트셋에서 이 낱말 필터는 정답 메일을 하나도 거르지 않고 48통 중 7통을 뺐다.
  - 아무 요청도 보내지 않는 때: 회의 기간 밖(시작 3일 전부터 마지막 날까지만), 더미 미팅, Chair notes가 이메일 토론을 아직 적지 않은 때, 이 회의의 agenda가 없을 때.
  - 기록한 답은 빌드마다 지금 시간표에 다시 맞춘다(`apply_records`, 호출 없음). 시간표가 바뀌면 세부 정보도 따라 옮겨지거나 빠진다.
    기록의 `offered`는 물을 때 보인 셀 id이고, 묶을 때는 LLM이 고른 id가 그 안에 있었는지만 본다. 후보는 빌드마다 지금 시간표에서 다시 만든다.
    같은 스레드의 뒤 메일이 같은 세션의 시각·나눔을 다시 적으면 앞 메일의 것을 바꾼다(#36이 #38을). 시각 없는 덧붙임("FL proposal for online")은 더한다.
- **check 잡은 피드 하나만 본다(`pending`).** 회의 기간에만, 요청 한 번이다. 목록에 없고, 스레드에 붙고, 미리보기에 시각·장소 낱말이 있는 메일이 있을 때만 빌드 사유가 된다. 실패는 check 오류가 아니라 경고다.
- 테스트는 메일 리스트에 요청하지 않는다. `tests/conftest.py`가 회의 기간 판정을 끄고 목록 경로를 임시 폴더로 바꾼다. `tests/test_email_updates.py`는 테스트셋의 피드·본문과 기록해 둔 LLM 답(`llm_answers.json`)으로 실행 두 번을 흉내 낸다.
- **화면(`renderer.py`): 셀에는 AI 줄 끝의 "maybe 10.3.1"과, 자리가 있으면 Note 자리의 한 줄씩, 나머지는 팝업이다.**
  사용자는 변경 안내를 읽지 않는다. 팝업을 열어야만 보이면 이 정보가 있는지 모른다.
  - "maybe"는 AI 줄에 ` · `로 잇고 Chair와 같은 가장 약한 톤(`--chair`, `.session-maybe`)이다. 새 줄을 쓰지 않아 높이 예산이 바뀌지 않는다. AI 줄이 보이는 셀(30분 이상)에서만 보인다.
  - 공지 하나가 셀의 Note 자리 한 줄이다(RAN2 Note와 같은 높이 예산과 톤, `_email_cell_lines`). 셀은 이미 시각과 종류를 보이므로 무엇을 다루는지만 적는다.
    한 줄은 `TDoc 번호 · 나눔 또는 주제`다(`R1-2608681 · Section 2, DL bandwidth settings`). TDoc 번호를 맨 앞에 둔다. 세부를 이해하려면 가장 먼저 열어 볼 문서다.
    그 뒤는 나눔(`10.6.1.2 40 min · 10.6.3 30 min · 10.6.1.1 20 min`), 없으면 메일이 든 주제(`Part 1 (PUSCH MIMO)`, `Section 2.3`, `DMRS sequence`)다. 셋 다 없는 공지는 셀에 넣지 않고 팝업에만 둔다.
    Proposal·Observation 번호는 셀에 쓰지 않는다(`_subject`). 그 문서 안에서 찾는 번호라 한눈에 읽는 정보가 아니다. 팝업에는 남는다.
    번호에 설명이 붙은 주제(`Proposal 4.3.1-1/2: DMRS sequence`)는 설명을 남긴다.
    요약은 셀에 쓰지 않는다. 짧은 셀에서 "Check proposals in"까지만 보여 아무것도 말하지 않는다. 제목에 이미 있는 줄("10.5.2.2 PDSCH"의 `PDSCH`)은 뺀다.
  - 필터는 "maybe" 항목도 그 셀의 AI처럼 맞춘다(`data-ai-scope`에 더한다). `data-ai`와 필터 항목 목록, Agreement 패널은 그대로다.
  - 팝업에는 노트 아래, AI 이름 위에 `From email threads` 구획을 둔다. 공지 하나가 한 덩어리다(`_email_detail_html`).
    **팝업은 셀 줄을 펼친 것이다.** 셀 줄에 있는 것(TDoc 번호, 나눔, 주제)을 모두 담고 더 자세히 적는다. 사용자가 셀과 팝업을 맞춰 읽지 않고 팝업만 보면 되게 한다. 팝업은 스크롤되므로 길어도 된다.
    ```
    maybe 10.3.1 · 요약                     ← 요지(LLM 80자 요약)
    R1-2608628 · Input 1 h before          ← 요지에 없는 TDoc 번호와 입력 마감
    10.6.1.2 40 min · 10.6.3 30 min        ← 나눔(셀과 같은 표기)
    • Proposal 6.1-2: PUCCH format         ← 주제 전부, 한 줄에 하나, Proposal 번호 포함
    — 보낸 사람, Fri 09:10 ↗
    ```
    - `Online`·`Offline` 같은 종류 표시는 두지 않는다. 사용자는 이미 그 세션 셀을 눌렀다. 메일의 시각도 셀의 시각과 같으면 적지 않는다.
    - 요지가 이미 말한 것은 아래에 다시 적지 않는다(요약에 있는 TDoc 번호·마감·주제, 나눔에 있는 주제).
    - TDoc 번호는 하이픈에서 줄바꿈되지 않는다(`.popup-email-tdoc`). 보낸 사람이 가려진 메일(`<>`)은 시각만 적는다.
  - **원문 메일 링크는 보낸 사람 뒤의 `↗` 하나다(`_mail_source`).** 팝업은 `— 보낸 사람, Wed 11:47 ↗`, off-offline 줄은 `— 보낸 사람 ↗`이고 새 탭으로 열린다.
    보낸 사람 이름에 걸지 않는다. 이름이 링크이면 메일 주소(쓰기)처럼 보여 누르기 망설여진다.
    밑줄도 `mail` 같은 낱말도 두지 않는다. 줄마다 낱말과 밑줄이 붙으니 목록보다 링크가 먼저 눈에 들어왔다. `↗`는 바깥 사이트의 새 탭이라는 표시로 충분하고, 무엇이 열리는지는 `title`(hover)에 둔다.
    글자 하나라 누를 자리가 작아서 padding으로 넓히고 같은 값의 음수 margin으로 줄 배치는 그대로 둔다(`.mail-link`). `&nbsp;`로 이름에 붙여 화살표만 다음 줄로 떨어지지 않게 한다.
    주소는 LISTSERV 메시지 id로 만든다(`email_sessions.message_url`, `wa.exe?A2=3GPP_TSG_RAN_WG1;<id>`). 기록에는 id만 있고, `apply_records`가 빌드마다 `url`을 붙인다.
  - **B(off-offline, FL이 기다리는 곳)는 Agreement 패널 아래의 별도 구획 하나에 모두 나열한다(`.email-informal`, `_render_email_informal`).** 시간표에 없는 offline도 B다.
    AI별로 나누지 않는다. 참석자는 그날의 비공식 모임을 한 번에 훑는다. Agreement 패널 안에 두면 셀을 골라야만 보이고, 고른 AI의 것만 보인다.
    한 줄은 `10:45 AI 9.4 요약 (장소) — 보낸 사람`이고 시각 순이다. 요일은 적지 않는다. 위에서 고른 요일 탭의 줄만 보인다. 요약에 이미 있는 장소는 다시 적지 않는다(`_says`, 낱말 단위로 비교).
    패널과 같은 표면이고 글은 더 연하다. 머리줄 `Off-offline and FL whereabouts` 옆에 `From the email threads, not on the schedule`을 적는다. 의장 글도 시간표도 아니라는 것을 밝힌다.
    셀을 고르면 그 셀 AI(상위·하위 포함, `data-ai-scope`)의 줄이 굵어지고 그 셀의 `--accent`를 12% 깐다. 선택된 셀 테두리와 같은 색이라 어느 셀의 것인지 보인다.
    셀의 click 핸들러가 전파를 막으므로 구획 스크립트는 capture 단계에서 듣는다.
    구획 스크립트가 요일 탭의 click을 듣고 그 요일(`data-day`)의 줄만 보인다. RAN1 요일 패널에는 날짜가 없어서 요일로 맞추고, 패널에 날짜(`data-date`)가 있으면 날짜까지 맞춘다. 고른 요일에 줄이 없으면 구획도 숨긴다.
    그날 이미 지난 시각의 줄은 남긴다(오후에도 오전 off-offline이 보인다). 날짜는 빌드가 정하고(`date`: 적힌 요일의 다음 날짜, 없으면 보낸 날), 회의 기간(`starts_at`~`ends_at`) 밖의 날짜는 다른 주의 같은 요일 탭에 붙지 않게 뺀다.
