# SA1

SA1에만 해당하는 규칙이다. 모든 WG에 공통인 규칙은 루트 `CLAUDE.md`에 있다.

## TDoc 결론 (Agreement 패널)

SA1은 같은 패널에 의장 파일 끝의 TDoc 표를 보인다(`working_groups/sa1/conclusions.py`). 패널 UI는 루트 `CLAUDE.md`의 "Agreement 패널 (공용)" 그대로이고 따로 UI를 만들지 않는다.

- **글은 데이터의 `text`가 바꾼다.** 머리줄(`TDoc conclusions for`), 빈 항목 안내, 불러오기 문구다. 없으면 RAN1 문구다(`agreements-panel.js`의 `TEXT`). Help 투어는 패널 클래스(`tdoc-conclusions`)로 단계를 가른다.
- **AI 하나가 표 하나다.** 제목 행(agenda와 같은 번호 세기, `document.headings`) 사이의 TDoc 행을 Word 열 너비 그대로 한 표로 만들고(`DocxHTML.table`, `fitTables`), 첫 행에 범례 표의 열 이름을 둔다.
- **결론 색은 원문 칸 바탕을 옅게 옮긴다.** Word 형광펜과 같은 값(초록 `#c6f0bd`, 청록 `#bdeef3`, 노랑 `#fff0a0`)이고, 다른 진한 색은 흰색과 섞는다. 이미 옅은 색(`CCFFCC`)은 그대로다.
- **TDoc 번호는 3GPP 아카이브 링크다.** 의장 파일은 의장 PC의 경로(`file:///C:\TSGS1_115_Prague\Docs\S1-….zip`)로 걸어 두었으므로 같은 파일의 회의 폴더 주소로 바꾼다.
- **바뀐 행은 초록 막대다.** 이전 빌드와 행(글과 바탕)을 비교해 새로 생기거나 바뀐 행의 첫 칸 왼쪽에 3px 막대를 둔다. 표 칸 밖으로는 그릴 수 없어서 문단 막대와 달리 칸 안쪽 테두리다(`td.agreement-added`).
- 이름으로 찾은 필터 전용 AI(`inferred_agenda_items`)로도 패널이 열린다(RAN1 추정과 같다).
