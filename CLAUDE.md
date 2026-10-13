# CLAUDE.md

프로젝트 구조, 실행 방법, WG별 원본 선택과 캐시는 `README.md`, RAN1 파싱 설계는 `PARSING_ARCHITECTURE.md`를 본다.

## 규칙 파일 구성

이 파일에는 모든 작업에 적용되는 공통 규칙만 둔다. 화면 요소와 WG별 규칙은 `.claude/rules/`에 나눠 두었고,
각 파일의 `paths`에 맞는 파일을 열 때 함께 읽힌다.

| 파일 | 다루는 것 |
|---|---|
| `.claude/rules/ui/session-block.md` | 간트 차트 세션 셀: 서체, 색, 정보 위계, 높이 예산 |
| `.claude/rules/ui/grid-zoom.md` | 그리드 핀치 줌 |
| `.claude/rules/ui/popup.md` | 셀 클릭 팝업: 순서, AI 트리, 화면 안 배치 |
| `.claude/rules/ui/filter.md` | 필터: AI 계층 매칭, URL 해시·저장 |
| `.claude/rules/ui/header.md` | 헤더: 후원 안내, 원본 파일 줄, 제작자 줄, 회의 종료 인사, 탭 아이콘 |
| `.claude/rules/ran1/agenda-labels.md` | RAN1: AI 번호 없는 셀의 AI 추정(LLM) |
| `.claude/rules/ran1/agreements.md` | RAN1: Agreement 패널(chair notes 본문 변환과 표시) |

규칙을 고치거나 더할 때도 같은 자리에 둔다. 새 요소나 WG는 파일을 새로 만들고 `paths`와 위 표를 채운다.

## 공통 원칙

- **화면은 한눈에 읽을 것이 적어야 한다.** 정보를 더할 때는 셀에서 빼서 팝업이나 패널로 보낼 수 있는지 먼저 본다.
  모든 규칙 파일의 결정은 이 원칙에서 나왔다. 문서에 이유가 적힌 결정은 이유가 사라지지 않았다면 되돌리지 않는다.
- **WG는 서로의 내부 모듈을 import하지 않는다.** 공통 코드는 `shared/`에 두고, 각 WG는 `build_schedule(options)`로
  `shared.schedule.Schedule`만 넘긴다. 3GPP 파일 접근은 `shared/remote_files.py`를 쓰고 WG에서 따로 재시도하지 않는다.
- **원문에 없는 것을 지어내지 않는다.** 파서가 확신하지 못하는 내용은 버리거나 추측으로 채우지 않고 원문 그대로 남긴다
  (RAN2 하단 정보, RAN P 근거 검증, 셀 원문 Note). LLM 답은 원문으로 확인될 때만 쓴다.
- **없는 폴더·파일은 정상이다.** 3GPP는 없는 경로에 403을 준다. 403·404·FTP 550은 "없음", 5xx·연결 실패만 "실패"다(`remote_files.is_absent`).
- 출력 형식이나 해석 규칙이 바뀌면 해당 캐시 버전(`PARSER_VERSION` 등)을 올린다.
- `docs/`는 CI가 만드는 결과물이다. 손으로 고치지 않는다(예외: `docs/ran1/agenda_label_map/`의 판정 덧붙이기).

## 확인 방법

- 회귀 테스트는 머지 전에 직접 돌린다: `uv run pytest -q`. 20분마다 도는 배포 workflow는 테스트를 돌리지 않는다.
- 화면 변경은 `docs/`를 건드리지 않도록 임시 폴더로 렌더링해서 본다. 데스크톱 폭과 모바일 폭(약 420px)을 모두 확인한다.

```bash
cp -R docs /tmp/out && uv run python main.py --render-only --output-dir /tmp/out
python3 -m http.server 8765 -d /tmp/out   # http://localhost:8765/ran1/
```
