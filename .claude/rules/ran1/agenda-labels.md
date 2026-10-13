---
paths:
  - "working_groups/ran1/agenda_labels.py"
  - "working_groups/ran1/pipeline.py"
  - "docs/ran1/agenda_label_map/**"
---

# AI 없는 셀의 AI 추정 (RAN1)

`MIMO`, `AI/ML`처럼 일정에 AI 번호 없이 주제만 적힌 셀이 있다. 이 셀도 필터에 걸리고 Agreement 패널이 열리도록,
셀 이름이 가리키는 agenda 항목을 `Session.inferred_agenda_items`에 둔다. 코드는 `working_groups/ran1/agenda_labels.py`다.

- **용도는 필터와 Agreement 패널뿐이다.** `data-ai-scope`와 `data-agreement-ai`에만 들어가고, 셀의 AI 줄·팝업·`data-ai`·필터 항목 목록에는
  나오지 않는다. 일정에 적힌 AI가 있으면 그것만 쓰고 추정은 보지 않는다.
- **근거는 셀 이름과 이 회의의 agenda뿐이다.** 그룹 헤더, 다른 셀, 시간·방은 쓰지 않는다(그룹은 LLM이 실행마다 다르게 정리할 수 있다).
  agenda(`agenda_item_description.json`)가 없거나 다른 회의 것이면 아무것도 추정하지 않는다.
- **판정은 LLM이 한다(Gemini, thinking `low`).** 질문은 "참석자가 이 이름을 일정표에서 보면 어느 항목의 세션으로 읽는가"다.
  낱말이 겹치는지가 아니라 RAN1에서 그 이름이 쓰이는 방식으로 판단한다.
  - 주제가 여러 릴리즈·스터디에 걸치면 모두 넣는다: `NTN` → GNSS resilient NR-NTN, NR NTN DL compensation, 6GR NTN.
  - 다른 주제의 한 측면에 낱말이 나올 뿐인 항목은 넣지 않는다: `MIMO` → NR MIMO Phase N만. 6GR의 `MIMO operation`은 아니다.
  - 유지보수 항목은 이름에 `maint`, 이전 릴리즈 번호(`R19`), 또는 그 항목 자신의 주제(`UE features`)가 있을 때만 넣는다.
    기능 이름이 적힌 유지보수 항목이 없으면 그 릴리즈의 `others` 항목으로 간다.
  - 자리표시(`TBD`, `To be assigned by …`), 진행(`Sweep`, `Session reports`), 식사는 대상 없음이다.
  - 프롬프트의 예시는 번호가 아니라 항목 제목으로 적는다. 번호는 회의마다 바뀐다.
  - thinking `high`는 230초쯤 걸려 504가 났고 결과는 `low`와 같았다.
- **답은 코드가 정리한다.** agenda에 없는 번호는 버리고, 상위 항목이 있으면 하위 항목은 뺀다(`9.1`이 있으면 `9.1.2`는 뺀다).
- **회의별 목록에 남긴다(`docs/ran1/agenda_label_map/<meeting>.jsonl`).** 한 줄이 이름 하나이고 대상 없음(`[]`)도 기록한다.
  LLM의 원래 답(`raw`, 항목별 이유)과 정리한 값(`agenda_items`)을 함께 둔다.
  - 목록에 있는 이름은 다시 묻지 않는다. LLM 판단이 흔들려도 화면은 실행마다 같다.
    **판정을 고치려면 같은 이름으로 줄을 덧붙인다(뒤의 줄이 이긴다).**
  - 회의가 바뀌면 새 파일이다. 목록에 없는 이름만 묻고 덧붙인다. 이름은 공백만 정리한 원문 그대로 비교한다.
  - 호출이 실패하거나 답에서 빠진 이름은 적지 않는다(다음 실행에서 다시 묻는다). 빈 목록은 "대상 없음으로 판정함"이라 실패와 섞으면 안 된다.
  - 회의 중 agenda 개정은 없다고 본다. agenda가 바뀌어도 목록은 다시 만들지 않는다.
