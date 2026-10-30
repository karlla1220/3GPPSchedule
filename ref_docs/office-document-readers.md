# Office 문서 읽기 도구 평가 (추후 고려, 2026-10-30)

새 WG(SA1·SA2·RAN3 DOCX, RAN4 PPTX, RAN3·RAN4 XLSX)를 붙이면서 외부 CLI로 Office 문서를 읽을지 시험했다. **지금은 쓰지 않는다.** DOCX는 python-docx, PPTX는 python-pptx/lxml, XLSX는 openpyxl로 읽는다. 아래는 다시 볼 때의 출발점이다.

## 시험한 것

- [OfficeCLI](https://github.com/iOfficeAI/OfficeCLI) 1.0.159 (C#, Apache 2.0, 단일 바이너리 34MB)
- [docx-cli](https://github.com/kklimuk/docx-cli) 0.28.0 (TypeScript/Bun, MIT, 단일 바이너리 73MB, DOCX 전용)

실제 문서로 돌렸다: RAN1 일정·Chair notes(변경 추적), RAN2 일정(변경 추적), SA1 Chair's Notes, SA2 Session Plan 두 판, RAN3 agenda 두 판, RAN4 #120 PPTX, RAN3 offline·RAN4 토픽 XLSX, 합성 XLSX. 문서 XML에서 직접 센 run 서식과 run 하나하나를 비교했다.

## 결과

| | OfficeCLI (`get … --depth 30 --json`) | docx-cli (`read --ast`) |
|---|---|---|
| DOCX 취소선 · 하이라이트 · 글자색 · run 바탕 | 모두 일치 | 모두 일치 |
| DOCX 변경 추적(넣은 글 · 지운 글) | 일치. 지운 글도 본문에 있고 작성자 · 시각이 붙는다 | 일치. `trackedChange {kind, author, date}` |
| DOCX 표 칸 바탕, 병합 | 있음 | 있음(`shading`, `gridSpan`, `vMerge`) |
| DOCX 글상자(RAN1 방 이름) | 본문 트리에 없음. `query textbox`로 따로 | 본문 순서 안의 `textBox` |
| DOCX 기호 문자(`w:sym`) | ``(글꼴의 사설 코드) | `↔`(풀어 줌) |
| PPTX 표 칸 안의 run별 취소선 · 색 | 없음. 칸 단위 요약과 `txBodyRaw`(원문 XML)뿐 | 해당 없음 |
| XLSX 칸 취소선 · 글자색 · 바탕 | 있음. 테마색은 `accent1+tint80` 꼴 | 해당 없음 |
| XLSX 칸 안 부분 서식 | 공유 문자열은 `run[N]`으로 있음, 인라인 문자열은 없음 | 해당 없음 |
| `.docm` | 읽음 | 읽음 |
| 속도 / 출력(DOCX) | 0.2–2.5초 / 2–19MB | 0.15–0.4초 / 0.1–1.5MB |

RAN4 #120 표 칸 231개 중 3개는 한 칸 안에 취소선 run과 아닌 run이 섞이고, 12개는 글자색이 섞인다. 칸 단위 요약으로는 이것을 알 수 없다.

## 다시 볼 때

- **DOCX를 공용 블록 입력으로 만들 때 docx-cli `read --ast`가 가장 유력하다.** 글상자 순서, 변경 추적, 병합, 기호 문자를 한 JSON 트리로 준다. 대가는 CI에서 73MB 바이너리를 버전 · SHA-256 고정으로 받아 캐시하는 것이다.
- **PPTX 표는 어느 도구로도 칸 XML(`a:txBody`)을 직접 읽어야 한다.** OfficeCLI가 더해 주는 것이 없다.
- **OfficeCLI는 기본이 자동 업데이트(`~/.officecli/config.json`의 `autoUpdate: true`)이고 실행 때 업데이트를 확인한다.** CI에서 쓰려면 끈다. `install`은 PATH와 여러 에이전트에 스킬을 설치하므로 바이너리만 받아 쓴다.
