# RAN4 일정 페이지 스터디 — 2026-10-15

RAN4 페이지를 만들기 전에 원본 자료가 어디에, 어떤 형식으로 올라오는지, 기존 공통 모델(`shared/schedule.py`)과 렌더러로 무엇을 그대로 쓸 수 있는지 조사한 기록이다.

## 확인 수준

- 진행 중인 **RAN4#120bis(제주, 10월 12~16일)** 의 SYNC 서버와 아카이브를 직접 조회하고 파일을 내려받아 열어 봤다.
  지난 회의(#117, #119, #120)의 일정 파일도 하나씩 열어 양식이 같은지 비교했다.
- 일정 셀 해석은 버려도 되는 시험 파서로 측정했다(아래 "규칙으로 읽은 결과"). 저장소에는 넣지 않았다. `python-pptx`가 아직 의존성에 없어서 `uv run --with python-pptx`로 실행했다.
- 3GPP FTP는 Cloudflare 뒤에 있어서 기본 `curl` User-Agent에는 403을 준다. 저장소의 `shared/ftp_transport.USER_AGENT`(`3GPPSchedule/1.0 …`)로는 200이므로 `shared/remote_files.client()`를 그대로 쓰면 된다.
- Chair notes(세션 리포트)는 구조만 봤다. 내용을 파싱해 보지는 않았다.

## 결론

1. **RAN4 일정은 PPTX 하나다.** `Inbox/Meeting_Arrangements/RAN4_<회의>_meeting_schedule_<꼬리표>.pptx`이고, 요일마다 슬라이드 한 장, 슬라이드마다 표 하나(행 = 시간대, 열 = 방)다.
   #117(2025-11)부터 #120bis까지 다섯 회의의 틀이 같다. 표 구조는 RAN1 DOCX보다 단순하고, 셀 글은 RAN2보다 자유롭다.
2. **셀의 주 정보는 AI가 아니라 토픽 번호다(`[101-B] System Parameter`).** 토픽 번호는 회의마다 새로 매긴다(6G testability: #120에서 `[213]`, #120bis에서 `[217]`).
   토픽 → AI, WI, FL/moderator 매핑은 같은 폴더의 **토픽 목록 엑셀**(`List_Topics_moderators-RAN4_<회의>_*.xlsx`)에 있다. 이 표가 있으면 셀마다 AI 줄과 필터를 LLM 없이 만들 수 있다.
3. **규칙만으로 거의 다 읽힌다.** #120bis 최신본(THU)에서 셀 구간 126개 중 122개(97%)의 시각이 규칙으로 정해졌다.
   틀린 곳은 정해진 몇 가지 유형이다(아래). RAN2처럼 **규칙 + 불확실한 셀만 Gemini 폴백**이 맞다. RAN P처럼 문서 전체를 LLM에 맡길 이유는 없다.
4. **회의 메타데이터는 Portal에서 그대로 온다.** TB ID 382는 `shared/portal_meetings.py`에 이미 있다. #120bis는 `Id 60732`, `(GMT+09:00) Seoul`, `MtgDocURL …/TSGR4_120bis/Docs/`로 나온다.
5. **목표는 RAN4#121(캘거리, 11월 16~20일)** 이다. Portal에 등록돼 있고(`MtgDocURL`은 아직 없음), 아카이브에는 `TSGR4_121/Invitation`만 있다.
   #120bis에서는 토픽 목록이 회의 7일 전(10/05), 일정 v04가 4일 전(10/08) 아카이브에 올라왔다.

## 1. 원본 자료 위치

### 폴더

| 시기 | 위치 | 비고 |
|---|---|---|
| 회의 전 | `tsg_ran/WG4_Radio/TSGR4_<회의>/Inbox/Meeting_Arrangements/` | 일정 v0x, 토픽 목록, 회의 안내 PPTX |
| 회의 중 | `Meetings_3GPP_SYNC/RAN4/Inbox/Meeting_Arrangements/` | 최신본 하나만 폴더에 있고 이전 판은 `History/`로 옮겨진다 |
| 회의 후 | 아카이브의 같은 폴더 | 회의 중 판이 모두 한 폴더에 쌓인다 |

- 회의 안내(`R4-2620002 … meeting arrangements and guidelines.pptx`, Readme)가 "최신 일정은 `Inbox/Meeting_Arrangements`의 `RAN4_120bis_meeting_schedule_xxx.pptx`"라고 명시한다.
- **회의 중 아카이브는 늦다.** 10/15 01:00 UTC 기준 아카이브의 최신본은 `v06_morning_Monday`, SYNC는 `THU`였다. RAN2처럼 회의 기간에는 SYNC를 본다.
- SYNC 동기화는 로컬 서버(10.10.10.10)에서 약 10분 간격이다(안내 PPTX).
- SYNC 목록의 시각은 UTC다(조회 시각 01:04 UTC에 `Inbox 2026/10/15 1:02`).
- SYNC의 `Agenda/`, `Inbox/`의 많은 파일이 `2026/10/11 7:00`으로 같다. 서버를 세울 때 한꺼번에 복사한 시각이라 업로드 시각이 아니다.

### 최신본 고르기

파일 이름의 꼬리표가 회의마다, 판마다 다르다.

```
#117    v06, v6_post_Mon, v7_post_Tue, v8_post_wed, v8_noon_thur
#119    v06 … v11
#120    after Monday, after Tuesday, after Thursday
#120bis v02, v03, v04, v06_morning_Monday, after_Tuesday, THU
```

- 숫자 버전만으로는 고를 수 없다(`after_Tuesday`, `THU`에는 번호가 없다).
- 아카이브의 시각도 믿을 수 없다. #119에서 `v08`이 `2026/05/25`, `v11`이 `2026/05/21`로 나온다(회의 뒤에 다시 복사됨).
- **규칙 제안:** 회의 중에는 SYNC `Meeting_Arrangements/` 바로 아래(History 제외)의 `*meeting_schedule*.pptx`가 최신본이다. 둘 이상이면 목록 시각이 늦은 것.
  회의 전에는 아카이브 폴더의 같은 패턴 중 목록 시각이 가장 늦은 것. 변경 감지는 RAN2처럼 선택한 파일의 URL + SHA-256으로 한다.
- `~$RAN4_118bis_meeting_schedule_v11.pptx`(0.1 KB, Office 잠금 파일)가 올라온 적이 있다. `~$`로 시작하는 이름은 버린다.

### 함께 쓸 파일

| 파일 | 위치 | 쓰임 |
|---|---|---|
| 토픽 목록 `List_Topics_moderators-RAN4_120bis_final.xlsx` | `Meeting_Arrangements/` | 토픽 → 제목, WI, 덮는 AI, FL/moderator (2절) |
| Agenda `R4-2621608.zip` → `…_RAN4#120bis_agenda.docx` | `Agenda/` | AI 번호 → 제목 |
| 회의 안내 `R4-2620002_…guidelines.pptx` | `Meeting_Arrangements/` | 의장·부의장 이름(열 머리의 `Yang/Shan/Gene Session`) |
| 세션 리포트 | `Inbox/Chair_Notes/{Main,RRM,BDaT}_Session_Notes/` | 나중 단계(Agreement 패널) 후보 |
| 토픽 스레드 폴더 `[120bis][101-A] 6G system parameter (part I)` | `Inbox/drafts/` | 토픽 제목의 다른 출처, moderator summary |
| TDoc 목록 `TDoc_List_Meeting_RAN4#120-bis_Oct14.zip` | `Inbox/Tdoclist/` | 이번 단계에서는 쓰지 않음 |

방의 실제 이름(제주 회의장 이름)은 회의 안내 PPTX 4~5쪽의 **그림**에만 있다. 글로 된 출처가 없으므로 열 머리를 방 이름으로 쓴다.

## 2. 일정 PPTX 구조

### 슬라이드

- 1쪽은 표지(`RAN4#120bis Meeting Schedule`, `3GPP TSG RAN WG4 Meeting #120bis / Jeju, South Korea, Oct. 12-16, 2026`).
- 2~6쪽이 월~금이다. 요일은 제목 placeholder의 글(`Monday` …)로 읽는다. 목요일 슬라이드는 제목 도형이 표 **뒤에** 있으므로 도형 순서에 기대지 않는다.
- 금요일 슬라이드에는 표 아래 안내 글상자가 있다: `Note: … additional morning AH sessions, starting no earlier than 8 am.`
- 표가 슬라이드보다 길 때가 있다(목요일 표 높이 7.69M EMU > 슬라이드 6.86M). 행 높이가 0인 행도 있다. 둘 다 내용과 무관하므로 크기는 보지 않는다.
- 숨김 슬라이드는 이번에 본 8개 파일에 없었다.

### 열 = 방

```
Venue / Time | RAN4 Main (Yang Session) | RAN4 RRM_Demod_OTA (Shan Session) | RAN4 RF,NTN,A-IOT (Gene Session) | Ad hoc room | Offline room
```

- 의장 Yang Tang, 부의장 Shan Yang·Gene Fong이다. 세 세션 = Main / BR1(RRM·Demod·OTA) / BR2(RF·NTN·A-IoT, BDaT)이다.
- 같은 열의 머리 글이 판마다, 요일마다 다르다: `RAN4  Breakout 1(BR1) (Shan Session)`과 `RAN4 RRM_Demod_OTA (Shan Session)`. 방 식별은 괄호 안의 `<이름> Session` 또는 `Ad hoc`/`Offline` 낱말로 한다.
- **방 수가 요일마다, 판마다 다르다.** 금요일은 4열(Offline room 없음). `Offline room`은 #120bis 회의 중에 생겼다(v04까지 5열, `v06_morning_Monday`부터 6열).
- **머리가 빈 열이 있다.** #119·#120 화·수요일의 폭 0.16M EMU짜리 6번째 열은 모든 칸이 비어 있다(버린다).
  그런데 #120bis `v06`의 수요일 6번째 열은 머리가 비었는데 칸에 offline 세션이 있다. 머리가 아니라 **칸이 모두 비었는지**로 버릴지 정한다. 머리가 빈 열의 이름은 같은 파일의 다른 요일에서 같은 위치 머리를 빌린다.
- `DaySchedule.rooms`가 요일마다 따로라서 방 수가 바뀌는 것은 지금 모델 그대로 된다.

### 행 = 시간대

- 첫 칸이 시간대다: `9:00-09:30`, `8:30-10:30`, `14:30-16:30 \n`(뒤에 빈 줄), `14:00-EOM`.
- 월요일은 9:00 개회, 화~목은 8:30, 금요일은 8:00에 시작한다. 셀 안 `Start at 8:00`처럼 행보다 일찍 시작하는 세션도 있다(수요일 BR2). 요일의 timeline 시작은 행과 셀 안 시각 중 가장 이른 것이다.
- `EOM`(금요일 마지막 행)은 끝 시각이 없다. Portal `EndDate`(`17:30`)를 쓰는 안을 제안한다(열린 질문).
- **쉬는 시간 행:** 글이 `커피 타임`, `점심시간`(개최국 언어)이고 칸이 `FFC000` 주황이다. 다른 회의에서는 영어일 수 있으므로 글과 색 둘 다 본다.
  병합 폭이 일정하지 않다(`[M1x4]`, `[M1x2]` + 따로 칠한 칸). 행의 비어 있지 않은 칸이 모두 쉬는 시간 글이거나 주황이면 쉬는 시간 행이다.
  금요일은 점심이 12:45~13:45이고 다음 행이 14:00이라 15분이 빈다.
- 셀 바탕색(`F0F3F8`, `D1DAE9`, 테마색)은 행 단위 줄무늬다. 카테고리를 뜻하지 않으므로 색 배정에 쓰지 않는다.
- 월요일 9:00~9:30 개회 셀은 세 세션 열에 병합돼 있다(`[M1x4]`, 옆 칸은 offline room). `room_scope: shared`에 해당한다.

## 3. 셀 문법

한 셀은 한 방의 2시간 블록이고, 그 안에 세션이 하나 이상 있다. 줄 단위로 읽힌다.

| 형태 | 예 | 뜻 |
|---|---|---|
| 토픽 줄 | `[201] NR_RRM_Ph6`, `[346] NR_IoT_NTN_Ph2 (40)` | 토픽 번호 + 이름. 괄호 숫자는 TDoc 수(BR2만 적는다) |
| 분류 머리 | `Rel-20 RRM`, `6G SI: BSRF`, `5G UE RF` | 다음 토픽 줄들의 묶음 이름 |
| 시각 구분선 | `----9.30-------`, `---16:00---`, `—12:00—` | 위아래 세션의 경계 시각 |
| 괄호 시각 | `(14:30 – 15:30)`, `(8.30 – 9.30)`, `(18.00-19.00)`, `(until 11:00)`, `(14:30 to TBD)` | 그 세션의 시작~끝 |
| 단독 시각 | `Start at 8:00`, `8:30 – 10:30` | 다음 줄부터의 시작(~끝) |
| ad hoc 머리 | `Main Ad-hoc:`, `BR1 Ad-hoc:`, `BR2 Ad-hoc: Ambient IoT D2T2`, `RRM Ad-hoc:`, `BDaT Ad-hoc: TBD` | 새 세션의 시작. 앞말은 **어느 세션의 ad hoc인지**이고 방이 아니다 |
| 좌장 | `Chaired by Toni (Qualcomm)`, `chaired by Jinyup`, `by Dorin` | ad hoc·offline 좌장. 세 세션 열은 열의 의장 |
| 쉬는 시간 ad hoc | `Coffee break AH: [105-A] 6G RRM, chaired by Jerry` | 블록이 아니라 **뒤따르는 쉬는 시간**(16:30~17:00 등)에 열린다 |
| 진행 항목 | `EARLY RETURN TO (in the following order) / 6G system parameter / …`, `Return to (final round)`, `Reserved`, `TBD` | 토픽 번호 없이 주제 이름만 |
| 개회·폐회 | `1. Opening of the meeting / 2. Approval of the agenda / …`, `13 Any other business / 14 Close of the meeting` | Agenda AI 번호 |
| 문서 | `R4-2621419 (Reply to RAN1 LS on scheduling restriction …)` | TDoc 하나를 다루는 세션 |
| 메모 | `(Continued Weds morning)`, `(cont)`, `NOTE: To avoid the conflict with Main session, and 17:00-18:00 can also be sued for AI mobility` | 덧붙인 말 |

### 토픽 번호

- 세 자리 번호의 첫 자리가 세션이다: `1xx` Main(Yang), `2xx` RRM·Demod·OTA(Shan), `3xx` RF·NTN·A-IoT·BDaT(Gene). 토픽 목록 엑셀의 시트 이름(`Main session`, `Shan session`, `Gene session`)과 맞는다.
  `x00`(`[100] Main Session` 등)은 의장 공지 스레드다.
- 큰 토픽은 `-A`/`-B`로 나뉜다(`101-A` waveform 등, `101-B` modulation 등). 셀에는 `[105]`(둘 다), `[102-A/B]`, `[214] [215] 6G Demod_Part1&Part2`, `[216] … & [217] …`처럼 묶어 적기도 한다.
- 메일 제목·drafts 폴더·moderator summary가 모두 `[120bis][101-A]`를 쓴다. RAN4 참석자가 세션을 부르는 이름이 이 번호다.

### 실제로 본 오타와 예외

규칙 파서가 견뎌야 하는 것들이다.

- 괄호가 빠진 번호: `Coffee break AH: 105-B 6G RRM`
- 여는 괄호가 엉뚱한 곳: `[NTN (cont) / [351] …`
- 머리 오타: `BBR2 Adhoc:`, `BR2 Ad-Hoc:`, `Main Ad hoc`, `COEFEE BREAK AH`(#119)
- **번호와 이름이 어긋남:** 목요일 `[102-A/B] System Parameter, Chaired by Leo (Huawei)/Jinqiang (OPPO)`.
  `102`는 General RF and UE RF이고 System Parameter는 `101`이며, Leo·Jinqiang은 `101-A`·`101-B`의 FL이다. 토픽 목록과 맞춰 보면 어긋남을 찾을 수 있지만 어느 쪽이 맞는지는 규칙으로 정할 수 없다(LLM 폴백 또는 경고).
- 이름 표기 흔들림: `Jingqiang`/`Jinqiang`, `Jin-Yup`/`Jinyup`, `jingjing`, `Toni Laehteensuo`(엑셀)와 `Toni`(일정)
- 같은 셀 안에서 시각이 없는 연속 토픽: `Rel-20 RRM / [207] NR_Mob_Ph5_RRM / [209] Ambient_IoT_Ph2_RRM`(2시간). 나눌 시각이 없다.
- 메모 속 시각: 위 `NOTE:`의 `17:00-18:00`은 세션 시각이 아니다.

## 4. 토픽 목록 엑셀

시트 세 개(`Main session`, `Shan session`, `Gene session`), 한 행이 토픽 하나다. 분류 행(`6G SI`, `Rel-20 RRM` 등)은 병합된 한 칸이다.

| 열 | 예 (`101-B`) |
|---|---|
| 번호 | `101-B` |
| Title | `6G system parameter (part II)` |
| Topic title | `=CONCATENATE("[",A1,"]","[",A5,"] ",B5)` → `[120bis][101-B] 6G system parameter (part II)` (수식이라 값이 캐시되지 않았을 수 있다. 번호와 Title로 다시 만든다) |
| WI | `FS_6G_Radio` |
| Topic areas | `6G system parameter: modulation, channel arrangement, min max CBW for device type` |
| AI covered | `8.2.3, 8.2.4, 8.2.5,8.2.6` |
| Summary agenda | `8.1` |
| FL / Moderator | `Jinqiang Xing` |

- 시트마다 열 순서와 이름이 다르다(Gene 시트는 `Company`, `Moderator Summary` 열이 더 있다). 머리 행 이름으로 열을 찾는다.
- `AI covered` 표기가 지저분하다: `8.2.1, 8.2.2, ,8.2.7`, 줄바꿈 구분, `4.2	Rel-19 spectrum related WI maintenance	[WI code]`처럼 제목이 붙은 줄, `11.2, 11.3, 11.4 (BDaT related)`. 번호 패턴만 뽑고 이 회의 Agenda에 있는 번호만 남긴다.
- 파일 이름도 회의마다 다르다: `List_Topics_moderators-RAN4_120bis_final.xlsx`, `RAN4_120 list of topics 6G FL and 5G moderators v1.xlsx`. 패턴은 `*topic*.xlsx`(대소문자 무시)가 안전하다.
- 회의 중에 고쳐질 수 있으므로 일정과 같이 변경 감지 대상에 넣는다.

## 5. Agenda

- `Agenda/R4-2621608.zip` 안의 DOCX 하나(`…_revR4-2620000_RAN4#120bis_agenda.docx`). 표지 뒤가 AI 목록이다.
- **AI 번호가 글이 아니라 Word 자동 번호다.** 329개 문단이 모두 같은 `numId`이고 `ilvl`만 다르다. 수준별 카운터로 번호를 세면 토픽 목록과 맞는다:
  `7.16 Artificial Intelligence (AI)/Machine Learning (ML) for mobility [NR_AIML_Mob]`, `8.12 RAN4 operation efficiency [FS_6G_Radio]`, `12.1 Migration of A-MPR to JSON format`.
  확인은 몇 항목만 했다. 구현 때 `numFmt`, `start`, 수준별 재시작(`lvlRestart`)을 `shared/docx_html.py`의 번호 계산과 맞춰 본다.
- 제목 끝의 탭 + `[WI code]`는 떼어 낸다. `-------- Other Rel-20 spectrum WIs --------` 같은 구분 문단과 `Note:` 문단에는 번호가 없다.

## 6. 공통 모델로 옮기기

| `Session` 필드 | RAN4에서 |
|---|---|
| `day`, `start_time`, `end_time` | 슬라이드 제목, 행 시간대와 셀 안 시각 |
| `room_col_start/end`, `room_ids` | 열 위치. 병합 칸은 폭만큼, 개회 셀은 `room_scope: shared` |
| `name` | 원문 토픽 줄 그대로(`101-B System Parameter`). 토픽이 둘 이상이면 아래 열린 질문 |
| `agenda_item` | 토픽 목록의 `AI covered`(셀에 적힌 토픽들의 합) |
| `chair` | 셀에 적힌 좌장. 없으면 열의 세션 의장(Yang/Shan/Gene). Ad hoc·Offline 열에서 좌장이 없으면 비운다 |
| `group_header` | 토픽 번호의 세션(`Main`/`RRM·Demod·OTA`/`RF·NTN·A-IoT`). Ad hoc 방에서도 자기 세션 색이 된다 |
| `notes` | 토픽 줄 목록(`[340] NR_UE_RF_Ph5 (19)`), 메모(`(Continued Weds morning)`). 셀의 Note 자리와 팝업에 그대로 쓰인다 |
| `description`, `agenda_descriptions` | 토픽 목록의 Title·Topic areas, Agenda의 AI 제목 |
| `source_labels` | `Schedule THU`, `Topics`, `Agenda` 같은 짧은 이름(CLAUDE.md "원본 파일은 짧은 이름과 버전으로") |

- **필터:** 지금 필터는 AI 번호 계층으로 맞춘다(`_ai_filter_scope`). RAN4 참석자는 토픽 번호로 찾을 가능성이 크다. 토픽 번호를 필터 항목으로 따로 둘지는 열린 질문이다.
  AI만 쓰면 토픽 목록에서 푼 AI가 `data-ai-scope`에 들어가므로 지금 코드로 동작한다.
- **쉬는 시간 ad hoc:** 렌더러는 쉬는 시간 막대(`.break-bar`)를 그리고 세션 블록을 따로 놓는다. 쉬는 시간 안의 세션 블록이 막대 위에 제대로 보이는지는 확인하지 않았다.
- **Agreement 패널:** 세션 리포트가 세션별(Main/RRM/BDaT) DOCX이고 날마다 판이 바뀐다. 제목 스타일(Heading)이 없고 토픽 스레드 이름(`[120bis][216] NR_UE_OTA_Enh`)으로 나뉘며 `Agreement` 줄이 73개 있었다(RRM v03).
  Main은 요일 폴더(`MON`, `TUE`, `WED`)에 ad hoc 의사록·WF 초안이 따로 쌓인다. RAN1 패널과 구조가 달라서 다음 단계로 미룬다.

## 7. 규칙으로 읽은 결과 (시험 파서)

줄 단위로 위 3절의 패턴만 적용한 시험 파서를 #120bis `THU`판에 돌린 결과다.

| 항목 | 수 |
|---|---:|
| 쉬는 시간 행 | 14 |
| 세션 구간 | 126 |
| 시작·끝이 정해진 구간 | 122 |
| 토픽 번호가 있는 구간 | 104 |
| 토픽이 둘 이상인 구간 | 21 |
| 쉬는 시간 ad hoc | 4 |

맞게 읽은 예:

```
Mon 11:00-12:00 Ad hoc room  [105-A] Jerry   | Main Ad-hoc / [105-A] 6G RRM Chaired by Jerry (Apple) / —12:00— /
Mon 12:00-13:00 Ad hoc room  [105-B] Jin-Yup | [105-B] 6G RRM Chaired by Jin-Yup (LGE)
Tue 08:30-09:30 Yang Session [107-A]         | [107-A] 6G AI / ----9.30------- /
Tue 09:30-10:30 Yang Session [107-B]         | [107-B] 6G AI
Wed 18:00-18:45 Ad hoc room  [105-A] Jerry   | … ---18:45--- …
```

틀린 예(규칙을 더하거나 LLM 폴백으로 넘길 것):

- 목요일 BR1 14:30~16:30: `NOTE: … 17:00-18:00 can also be sued …`의 시각을 세션 시각으로 읽었다. `NOTE:` 뒤는 메모로 다룬다.
- 목요일 BR2 11:00~13:00: 시각 없이 이어지는 `BR2 Ad-hoc: 6G Sensing feasibility`가 `13:00-13:00`이 됐다. 시각 없는 순서는 나눌 수 없으므로 블록 전체를 함께 쓰거나 불확실로 표시한다.
- 수요일 BR2 08:30~10:30: `Start at 8:00 / [356] … / 8:30 – 10:30 / [371] …`에서 단독 시각 줄을 구간 경계로 쓰지 않아 한 블록이 됐다(8:00~8:30, 8:30~10:30 두 개가 맞다).
- 쉬는 시간 ad hoc 4개는 끝 시각이 없었다. 다음 행의 시작(쉬는 시간 끝)을 쓰면 된다.
- 토픽 없는 22구간 중 `EARLY RETURN TO`, `Return to`, `Reserved`, `TBD`, `6G SI BSRF (cont)`는 정상이다. `-TN and NTN coexistence chaired by Shubham`, `~7GHz TN co-ex topic.`은 번호 없는 토픽이다(토픽 목록의 `364 NTN_coexistence`와 맞출 수 있다).

## 8. 구현 계획 제안

기존 WG와 같은 모양으로 `working_groups/ran4/`를 만든다.

1. **소스(`sources.py`, `lifecycle.py`):** Portal(TB 382)로 현재·다음 회의를 고르고, 회의 기간에는 SYNC, 그 밖에는 아카이브 `TSGR4_<회의>/Inbox/Meeting_Arrangements/`에서 일정 PPTX·토픽 엑셀을, `Agenda/`에서 agenda를 받는다.
   `shared/meeting_folders.py`의 `WGS`에 `'ran4': ('…/tsg_ran/WG4_Radio/', 'TSGR4_', ('bis', 'b'))`를 더한다(아카이브는 `TSGR4_118bis`, `TSGR4_120bis`).
   check는 세 파일의 URL + SHA-256으로 변경을 판단한다.
2. **문서(`document.py`):** `python-pptx`로 슬라이드 → 표 → 칸(병합, 칠, 줄)을 읽는다. 의존성에 `python-pptx`(MIT)를 더한다. `openpyxl`은 이미 있다.
3. **셀 해석(`sessions.py`):** 3절 규칙으로 구간을 나누고, 시각을 정하지 못하거나 토픽 번호와 토픽 목록 제목이 어긋나는 셀만 불확실로 표시한다.
4. **폴백(`llm.py`):** 불확실한 셀만 Gemini에 묻고, 답의 시각이 셀 글에 있는지 코드가 확인한다(RAN1 이메일 토론의 `times_in`과 같은 원칙).
5. **토픽·AI(`topics.py`):** 엑셀 → 토픽 표, agenda → AI 제목. 셀의 토픽 → AI, 설명, FL.
6. **파이프라인·등록:** `registry.py`, `site.json`(처음에는 `enabled: false`로 두고 확인 뒤 켬), `README.md`.
7. **테스트:** #120bis의 일정 판 6개, 토픽 엑셀, agenda를 `tests/fixtures/ran4/`에 두고, 판마다 세션 목록 스냅숏을 비교한다. #117·#119·#120 최종본은 양식 회귀용으로 쓴다.

#121 일정 v0x는 회의 1주 전쯤 아카이브에 올라올 것으로 보인다. 그 전에 1~5를 #120bis 자료로 끝내 두면 된다.

## 9. 열린 질문

구현 전에 정할 것들이다.

1. **셀 제목:** 원문 토픽 줄(`101-B System Parameter`)인가, 토픽 목록의 Title(`6G system parameter (part II)`)인가.
   CLAUDE.md의 "제목 하나가 주 정보" 원칙에서 짧은 쪽은 원문이다. 토픽 번호를 제목 맨 앞에 두면 RAN4 참석자가 부르는 이름과 같다.
2. **토픽이 여럿인 블록:** BR2는 2시간에 토픽 2~9개를 시각 없이 나열한다. 블록 하나에 토픽 목록(Note 자리)으로 둘지, 블록을 균등하게 나눌지.
   나누면 원문에 없는 시각을 만든다. 하나로 두는 안을 권한다.
3. **필터 항목:** AI 번호만인가, 토픽 번호도인가.
4. **좌장:** Ad hoc·Offline 칸에 좌장이 적혀 있지 않으면 토픽 목록의 FL을 보일지, 비울지. 적힌 것만 쓰는 쪽이 저장소의 다른 규칙(근거 없는 추정 안 함)과 맞다.
5. **금요일 `EOM`:** Portal `EndDate`의 시각(17:30)으로 끝낼지.
6. **방 이름:** 열 머리를 줄여 `Main (Yang)`, `BR1 (Shan)`, `BR2 (Gene)`, `Ad hoc`, `Offline`으로 보일지. 실제 회의실 이름이 필요하면 config에서 회의별로 손으로 넣는다.
