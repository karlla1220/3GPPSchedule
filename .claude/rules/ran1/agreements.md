---
paths:
  - "working_groups/ran1/agreements.py"
  - "shared/docx_html.py"
  - "shared/docx_math.py"
  - "shared/agreement_assets.py"
  - "templates/agreements-panel.js"
  - "templates/schedule.css"
  - "scripts/preview_agreements.py"
---

# Agreement 패널 (RAN1)

셀을 누르면 그리드 아래 패널에 그 셀 AI의 chair notes 본문이 나온다. HTML은 `shared/docx_html.py`,
패널은 `templates/agreements-panel.js`, 스타일은 `templates/schedule.css`의 agreement 구획이다.
검증 방법과 지원 범위는 `AGREEMENTS_VERIFICATION.md`에 있다.

- **문서 형식이나 출력이 바뀌면 `working_groups/ran1/agreements.py`의 `PARSER_VERSION`을 올려 캐시를 무효화한다.**

## 표면과 서체

- **페이지와 같은 문법을 쓴다.** 패널은 그리드와 같은 표면(흰 바탕, 헤어라인 그림자, `--radius-lg`)이고,
  AI 탭은 요일 탭의 세그먼트 컨트롤을 선택자만 더해 함께 쓴다. 따로 버튼 스타일을 만들지 않는다.
- **본문 서체는 페이지를 따른다.** Word의 본문 글꼴은 버리고 기호·수식·고정폭 글꼴만 남긴다(`_KEPT_FONTS`).
  글자 크기는 Word 본문 11pt 기준 `em`이라 패널의 14px가 전체 배율을 정한다.
- **Word 형광펜은 같은 색조로 옅게 바꾼다.** 원색 녹색·노랑은 페이지에서 가장 튀는 요소가 된다.
- **선택된 셀은 자기 `--accent`로 테두리를 한 줄 더 두른다.** 그림자는 더하지 않는다(hover 전용). 파란 외곽선은 `:focus-visible`에만 쓴다.
- 패널은 하위 섹션까지 푼 `data-agreement-ai`를 쓰고, AI가 없는 셀은 추정한 항목(`inferred_agenda_items`)으로 연다.
  필터의 `data-ai`와 합치지 않는다(`.claude/rules/ui/filter.md`).

## 바뀐 부분 표시

- **초록 한 색이다(`--added`).** 추가된 문단·표는 왼쪽 여백의 3px 막대, 최신 note에서 바뀐 AI 탭은 6px 점,
  제목 아래 변경 줄(`Updated 6 Oct, 14:30 CEST · v10 · 1 added part marked`)도 같은 초록 막대다. Word 형광펜은 바탕만 있어서 막대로 구별된다.
- 초록 바탕은 탭을 여는 순간에만 잠깐 보이고 사라진다(`added-flash`, 2초). 남는 것은 막대뿐이다.
- **막대는 문단의 들여쓰기와 상관없이 문서 왼쪽 여백(본문 왼쪽 선에서 10px 밖)에 선다.** 문단 안쪽에 그리면 내어쓰기된 문단에서 글자와 형광펜 뒤에 가려진다.
  문단의 `margin-left`는 Word가 준 인라인 값이라 CSS에서 알 수 없다. 그래서 문단 왼쪽으로 길게 뻗은 띠(`::before`)를 그리고,
  `.docx-document::after`의 흰 덮개가 왼쪽 3px만 남기고 가린다. 여백 밖은 `clip-path`가 자른다.
  쌓는 순서는 띠(-3) → 덮개(-2) → 번쩍이는 바탕(`::after`, -1) → 글자다.
- **이어서 추가된 블록은 막대 하나로 보인다.** 끊기면 점선처럼 보인다. `joinAdded`가 앞의 추가 블록과 사이에 글·그림이 없는 블록에
  그 틈(`--added-reach`)을 재어 넣는다. 틈은 글꼴과 폭에 따라 바뀌므로 문서 크기가 바뀔 때마다 다시 잰다.
  사이에 바뀌지 않은 글이나 접힌 TDoc 목록이 있으면 따로 선다.
- 시각은 회의 시간대(`schedule.timezone`)로 보인다. 첫 note는 기준선이라 `As of …`로 날짜만 보이고 강조는 없다.
- **패널이 반영한 note의 버전은 머리줄 오른쪽 끝에 늘 보인다(`.agreement-source`, `Chair notes v00 · 10 Oct, 15:55 GMT+9`).**
  AI의 변경 줄만으로는 그 뒤에 나온 note가 반영됐는지 알 수 없다.
  - 셀을 고르기 전에도 보인다. 정적 마크업의 `.agreement-header`에 패널 스크립트가 붙인다.
  - 변경 줄과 같은 톤(12px, `--ink-3`)과 버전 표기다. 시각은 `document_changed_at`, 파일 이름은 `title`(hover)이다.
  - 600px 이하에서는 머리줄의 마지막 줄로 내려와 왼쪽에 붙는다.

## 표

**표는 Word의 열 너비 그대로 두고, 좁으면 통째로 줄인다(`docx_html.py`의 `table_grid`, 패널의 `fitTables`).**
글자가 작은 것은 사용자가 확대하면 되지만, 다시 줄바꿈된 표는 되돌릴 방법이 없다.

- 열 너비는 `w:tblGrid`의 비율(`<colgroup>`, `table-layout: fixed`)이고, 표의 최소 폭은 Word의 표 폭이다
  (twip ÷ 220 = 본문 em, `TABLE_FONT_EM`로 나눈다). 그래서 줄바꿈이 Word와 같은 자리에서 일어난다.
- 패널이 그 폭보다 좁으면 줄바꿈을 지키는 가장 큰 공통 배율을 찾아 표에 CSS `zoom`을 건다. `transform`과 달리 높이도 줄어든다.
  넓으면 100%로 늘어나고 비율은 같다.
  - 표 전체를 그대로 줄이면 Word 열의 빈 자리와 셀 여백까지 줄어 글자가 필요 이상으로 작아진다.
    그래서 Word 폭에서 셀마다 가장 긴 줄의 끝을 재고(왼쪽 정렬로 잰다), 열을 그 폭으로 줄인 뒤 배율을 정한다.
  - 셀 여백은 줌 뒤 화면에서 4px · 2px로 고정한다(`FIT_PAD`, 줌 전에 배율로 나눠 넣는다).
  - Word 폭에서 이미 넘치는 셀은 그 폭을 넘겨 재지 않는다. 넓히면 줄이 다른 자리에서 끊긴다.
  - 여러 열을 합친 셀은 모자라는 만큼만, Word 폭까지 남는 열에 나눠 준다.
  - 적용한 뒤 셀마다 줄 수를 다시 세고, 늘어난 셀이 있으면 배율을 8%씩 올린다(Chrome은 6px보다 작은 글자를 키워서 줄이 바뀐다).
    배율 1에서도 다 들어가지 않는 표는 줄바꿈을 지킨 채 그 표만 가로로 스크롤된다(`.table-pan`).
    줄 수는 세로로 겹치는 글자 상자를 한 줄로 센다. 수식(MathML)과 그림은 상자 하나로 본다. 줄이 합쳐져 줄어드는 것은 괜찮다.
  - 줄 폭을 재므로 웹폰트가 다 불린 뒤(`document.fonts.ready`), 문서 폭이 바뀔 때마다 Word 폭으로 되돌려 다시 잰다.
- 줌은 표에 걸고 감싸는 `.table-scroll`에는 걸지 않는다. 바뀐 부분 막대가 `.table-scroll`에 그려지기 때문이다.
  같은 이유로 가로 스크롤은 그 안쪽 `.table-pan`에 둔다(`overflow`가 막대를 자른다).
- 셀을 한 줄씩 쌓는 모바일 배치는 버렸다. 4열 13행 표가 2000px 넘게 늘어나고 머리행과 값이 떨어진다.
- 한 열짜리 표(IPR 안내 상자 등)는 글 상자라서 주변 문단처럼 폭에 맞춰 줄바꿈한다.

## 빈 항목과 TDoc 목록

- **빈 항목은 정상이다.** 하위 번호만 있는 제목이나 아직 합의가 없는 항목은 회색 안내 문구로 보인다. 패널은 본문이 있는 첫 탭을 먼저 연다.
- **TDoc 행은 5행 이상 이어진 목록일 때만 접는다(`agreements.py`의 `_listed_tdocs`, `TDOC_LIST_ROWS`).**
  본문이 가리키는 TDoc까지 빼면 무엇에 대한 글인지 알 수 없다. 4행까지(수신 LS, FL summary, 승인된 CR 번호 등)는 원문 그대로 둔다.
  - 접은 자리에는 회색 한 줄 `▸ 48 TDocs`를 두고, 누르면 그 자리에서 펼쳐진다(`<details class="tdoc-list">`, 닫힌 채로 시작).
    줄은 `--ink-3`, 펼친 행은 0.93em `--ink-2`로 들여 쓴다. 상자나 테두리는 두지 않는다. 펼친 상태는 기억하지 않는다.
  - **패널의 DOMPurify 허용 태그에 `details`·`summary`가 있어야 한다.** 빠지면 목록이 전부 펼쳐진 채 보인다.
  - 접힌 목록은 변경 비교에 넣지 않는다. 넣으면 late contribution 하나에 그 AI가 Updated로 표시된다.
  - 목록만 있는 AI는 접힌 줄도 넣지 않고 비워 둔다(빈 항목 안내가 같은 말을 한다).
  - 목록 끝에 붙은 summary 행은 뒤에 내용이 이어질 때 남긴다(의장은 기고 목록 끝에 `FL summary #1`을 붙이고 그 아래에 합의를 적는다).
    목록 중간의 summary 행은 함께 접는다. 제목 낱말로 행의 종류를 가리는 규칙은 이것 하나뿐이다.
  - 행 아래의 괄호 주석(`(Late contribution)`, `(Revision of R1-…)`)은 그 행을 따라 접힌다.
  - 표는 TDoc 목록 표만 통째로 뺀다. `Tdoc number | Issue`처럼 TDoc으로 항목을 가리키는 표는 행을 빼지 않는다.
