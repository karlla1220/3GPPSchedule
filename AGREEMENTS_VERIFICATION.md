# RAN1 chairman agreement 구현·검증

이 워크트리에만 구현했다. 배포, main 병합, 원본 checkout 수정은 수행하지 않았다.

## 실행과 미리보기

```bash
uv sync --locked
# 권장: Java 21 설치 후, 체크섬이 고정된 변환기 준비
uv run python scripts/setup_wmf2svg.py
uv run python scripts/preview_agreements.py
python3 -m http.server 8874 --bind 127.0.0.1 --directory test_runs/agreements
```

- [RAN1#124 agreement 미리보기](http://127.0.0.1:8874/ran1/): 실제 chairman note + 검증용 일정 셀/시간.
- [실제 RAN1#126 일정](http://127.0.0.1:8874/current-ran1/): 해당 chairman note가 없는 상태. #124 문서를 섞지 않는다.
- [RAN Plenary](http://127.0.0.1:8874/ran-plenary/): 기존 팝업 유지. WG 이동 및 뒤로 가기 확인용.
- 미리보기 생성은 LLM/API 키나 FTP 접속이 필요 없다. 선택 후 SolidJS CDN 접속은 필요하다.

실제 빌드는 기존 `uv run python main.py --wg ran1` 경로를 사용한다.
`--local`/`--no-download`에서는 동일 미팅의 로컬 note만 사용한다.
`--render-only`는 schedule.json의 agreement 기록과 옆의 `agreements/*.html`로 다시 렌더링한다.
미리보기는 v09를 기준선으로, AI 10.1에 문단 하나를 더한 v10을 만들어 변경 일시와 추가분 강조를 보인다.

## 실제 문서에서 확인한 내용

입력: `tests/fixtures/ran1/Chair notes RAN1#124 - v09.docx` (사용자 기존 다운로드 캐시에서 복사).
SHA-256: `0089af1622cee5168e8b8c815a9ec8fa9345878ab8ebcb023b7f82cbbcdf4476`.

- 번호가 식별된 agenda 90개 전체를 대상으로 하며, TDoc 제외 후 80개에 본문이 남는다.
- TDoc 메타데이터 1,539행(탭 구분 1,537행, ID 단독 2행)을 제외한다.
- 원문의 `Agreement` 68개와 `Way forward agreement…` 1개는 상태를 변경하지 않고 다른 내용과 함께 보존한다.
- AI 10.1과 10.10은 별도 키다. 마커별 article 대신 AI별 연속된 본문을 출력한다.
- 빈 heading 문단을 번호 계산에서 제외하여 AI 9.2.1 등 실제 heading과 연결한다.
- 새로운 FTP 다운로드나 Word 원본 화면과의 픽셀 단위 비교를 수행한 결과는 아니다.

Drawing/relationship/ZIP 조사:

| Drawing relationship | Target | 원본 raster fallback |
|---|---|---|
| rId16 | word/media/image1.wmf | 없음 |
| rId17 | word/media/image2.wmf | 없음 |
| rId18 | word/media/image3.wmf | 없음 |

문서 ZIP 전체에 PNG/JPEG나 OLE embeddings가 없고, XML에 `mc:AlternateContent`/`mc:Fallback`도 없다.
이 세 Drawing에는 `a:blip`의 WMF 참조와 `useLocalDpi` 확장만 있다.
WMF의 MathType 주석은 12-byte baseline 메타데이터로, 여기서 편집 가능한 MTEF 수식은 발견하지 못했다.
이와 **별개로 OMML 수식 32개**가 있으며, 각 수식을 독립적으로 MathML로 변환했다.

## 구현

- `working_groups/ran1/agreements.py`: 파일명 미팅 ID 엄격 검사, Word heading 번호 복원, 아젠다 섹션 전체 원문 추출 및 TDoc 메타데이터 행 제외. 요약/LLM 없음.
- `shared/docx_html.py`: 기본·상속 서식, bold/italic/underline/strike/color/highlight, 상하첨자, 문단·들여쓰기, GFM 방식 중첩 목록(깊이별 disc/circle/square 및 decimal 번호), 병합 표, 링크/이미지 렌더링.
- `shared/docx_math.py`: [MarkItDown](https://github.com/microsoft/markitdown)의 고정 커밋 math 모듈 → LaTeX → MathML. 원본/파생 라이선스는 `shared/vendor/markitdown_math/` 보존.
- `shared/wmf2svg.py`: [wmf2svg](https://github.com/hidekatsu-izuno/wmf2svg) 0.10.6 JAR를 Python subprocess로 headless 호출. 30초 timeout, 128MB heap, shell 미사용, SHA-256 검사. exit code뿐 아니라 stderr/출력 존재/XML root를 확인.
- `shared/metafile_images.py`: Java 변환 후 생성 CSS를 허용된 SVG 속성으로 옮기고 외부 참조·script 등을 제거. JAR/Java가 없거나 실패하면 [metafile-render](https://pypi.org/project/metafile-render/) 0.3.0 fallback. 이 경우 Adobe Symbol 매핑으로 Unicode 문자를 복원한다.
- `.github/workflows/deploy.yml`: 빌드 단계에 Temurin 21 및 고정 JAR 준비 추가. 실제 GitHub Actions 실행/배포는 하지 않았다.
- `shared/agreement_assets.py`: agenda별 해시 HTML fragment와 출처/버전/SHA를 담은 소형 manifest. 원본 다운로드 파일은 생성하지 않는다. 각 HTML의 이미지와 MathML은 자체 포함.
- `templates/agreements-panel.js`: pinned CDN SolidJS 1.9.9. 첫 셀 선택 때 runtime import, 활성 AI 탭의 HTML만 fetch. 복수 AI 탭, 방향키/Home/End, 캐시, 빠른 선택의 이전 응답 무시, 재시도/빈 상태 지원.
- RAN1 셀 클릭 시 기존 작은 상세 팝업을 유지하고 하단 `Agreements for` AI 탭을 갱신한다. Enter/Space, 선택 표시, 요일 초기화, 모바일 터치 지원. 자동 스크롤·iframe·본문 내부 스크롤·원본 다운로드 UI를 제거했다. 제목 tooltip에는 출처 파일과 SHA를 남긴다.

## 갱신·격리

Chairman note 검색과 bytes SHA 검사는 Portal/Agenda/기존 note의 timezone 결정과 독립적이다.
동일 파일명·업로드 시각이라도 다운로드 재검증의 SHA가 바뀌면 변경으로 판단한다.
검색/다운로드 실패를 문서 없음으로 처리하여 기존 agreement를 지우지 않도록 오류를 전파한다.
수동 note를 제거하면 remote 검색으로 복귀한다.

파싱 캐시 키는 parser version + 이미지 backend 식별자 + 원본 SHA이다.
`.cache/ran1/agreements`는 기존 RAN1 lifecycle reset 범위에 포함된다.
`docs/ran1`의 HTML/state는 기존 실패 rollback 범위에 포함된다.
이전 해시 HTML은 열린 탭을 위해 남겨 둔다. 장기 운영 시 오래된 assets 정리가 별도로 필요할 수 있다.

DOCX 관계에서 http(s) 링크만 허용하고, 이미지의 외부 다운로드는 하지 않는다.
본문은 DOMPurify 3.4.16으로 정제한 HTML fragment를 Shadow DOM에 직접 표시한다.
허용된 문서/MathML 태그만 유지하고 script/iframe/이벤트 속성/외부 이미지/위험 URL을 제거한다.
인라인 스타일은 서식 속성만 허용하고 CSS URL을 제거한다. Shadow DOM은 CSS 격리를 담당하며
sandbox 보안 경계로 취급하지 않는다. 원본 DOCM 매크로를 실행하거나 HTML로 변환하지 않는다.

## 검증 재현

```bash
uv run pytest -q
uv run --with playwright python -m playwright install chromium
uv run --with playwright python scripts/verify_agreements_browser.py
```

전체 회귀 테스트와 새 문서/매칭/변경감지/보안/변환 테스트를 실행한다.
Java 실제 변환 테스트는 JAR/Java가 없으면 명시적으로 skip하며, Python fallback은 별도로 강제 검사한다.
브라우저 검사는 최초 요청 수, AI별 원문 전환, WMF 3개/MathML 로딩, 복수 AI,
미팅 불일치, 키보드/모바일, WG 이동/뒤로 가기, 빠른 선택, HTTP 오류 재시도, CDN 실패,
취소선 보존, 변경 일시·추가분 강조·탭 표시를 포함한다(20개).
화면 캡처는 `test_runs/agreements/desktop.png`, `mobile.png`에 생성된다.
최신 섹션 전체 추출 변경 후 (2026-09-29): **395 passed (11.02s)**.
Java 실제 WMF 변환 및 Python fallback 포함, skip 없음. JS 문법 검사 및 `git diff --check` 통과.
정적 미리보기 재생성 완료. 최신 브라우저 재실행은 `socket.bind`의 `PermissionError: Operation not permitted`로
시작하지 못했다. 앞선 **16개 브라우저 통과 기록과 PNG는 이전 구현**의 결과이며 이번 변경 검증으로 세지 않는다.


## 제한 사항

- 연속 흐름 HTML이다. Word 페이지 나누기, 떠 있는 도형, 텍스트 박스, 완전한 표 폭/테두리/셀 스타일, 복잡한 다단 번호의 모든 규칙은 재현하지 못한다. 모바일에서는 표 셀을 세로로 배치해 별도 스크롤 없이 내용을 읽게 한다.
- 섹션 경계는 식별 가능한 번호가 있는 agenda heading이다. 번호 없는 소제목은 그대로 유지한다.
- TDoc ID는 RAN1의 `R1-` + 6자리 이상 숫자(선택적 revision)로 판별한다. ID만 있거나 탭으로 분리된 메타데이터 행, 단순 표 행만 제거한다. 공백만으로 구분된 모호한 행·복잡한 병합 행은 손실 방지를 위해 남길 수 있다. 본문의 `See R1-…` 및 문장 형태 인용은 보존한다.
- 제목/아젠다 번호는 문서 heading에서 복원한다. 계층 전체를 느슨하게 prefix 매칭하지 않으며, 정확한 해당 번호가 없으면 그 사실을 표시한다.
- WMF는 SVG 시각 표현이며, 의미 있는 수식 AST/MathML로 복원한 것은 아니다. 브라우저 폰트에 따라 자간·글리프 크기가 달라질 수 있다. OMML도 변환이 성공했다는 사실이 Word와의 완전한 수학적·시각적 동일성을 보증하지는 않는다.
- Word와 대조한 90개 섹션 전체 수동 검수는 하지 않았다. 실제 DOCX를 파싱한 automated 검증과 Chromium 화면 검수 결과다.
- 현재 Python 3.12.9/macOS/Temurin 21에서 확인했다. `metafile-render` 지원 범위 때문에 fallback은 Python <3.15에 설치되며, 그 이상에서는 Java를 준비해야 한다. 변환 불가 이미지에는 원본 확인 안내가 표시된다.
- Linux CI 구성은 작성했지만 원격 CI를 실행하지 않았다. CDN이 차단되면 viewer 실패 안내 및 Reload 버튼을 표시한다.


## 글머리 기호·문단 경계 수정

원본 numbering의 Symbol/Wingdings 글리프(PUA 및 제어 코드)와 `o`/`-`를 문자로 출력하면서
누락된 폰트 때문에 글머리 모양이 깨졌다. 원본 glyph 대신 문단별 유효 들여쓰기(직접 설정 → numbering 단계 설정 → 상속 스타일)를 읽어
연속된 상대 깊이의 `ul`/`ol`/`li`로 렌더링한다. 들여쓰기 정보가 없을 때만 `ilvl`을 참고한다.
연속 목록 안에서 1pt 이하의 들여쓰기 차이는 정렬 오차로 보고 같은 깊이로 취급한다. 글머리는 disc → circle → square, 깊이별 들여쓰기는 2em이다.
번호 목록은 decimal로 통일하고 `start`/`value`로 원본 시작 번호 및 이어지는 번호를 유지한다.
표 셀 안의 목록도 동일하다. 본문 run의 굵기·색상·수식은 유지한다.

제보 이미지의 왼쪽 넘침은 `Proposal` 상위 스타일의 `hanging="1701"`과 문단에 직접 지정된
`firstLine="0"`을 단순 병합한 것이 원인이었다. 생성 CSS에 `text-indent:0pt` 다음
`text-indent:-85.05pt`가 중복되어, 본문·표의 첫 줄을 약 113px 왼쪽으로 밀었다.
서식 계층을 합칠 때 첫 줄 들여쓰기와 내어쓰기를 상호 배타적으로 해석하여 직접 문단 설정을
우선한다. 해당 실제 문단 두 개의 CSS 및 브라우저 텍스트 경계를 회귀 검사한다.
`test_runs/agreements/indent-fixed.png`에 수정된 해당 agreement 화면을 기록한다.


## 들여쓰기와 표식 없는 내용 확인

- AI 10.5.2.1의 PDCCH 상위 목록은 `ilvl=0`, CORESET 등 하위 목록은 `ilvl=3`이다.
  그러나 문단에 직접 지정된 왼쪽 들여쓰기는 각각 400twips(20pt), 709twips(35.45pt)다.
  단계 번호를 그대로 사용하면 빈 두 단계를 삽입하므로, 유효 들여쓰기 증가를 한 단계 중첩으로 정규화한다.
- AI 10.5.1.1은 원문 `Agreement` 표식이 세 개다. 마지막 idle mobility 단락 앞에는
  빈 문단 네 개만 있고 표식이나 삭제된 표식은 없다. 섹션 원문으로 보존하며 새 Agreement 제목은 만들지 않는다.
- 첫 표식 이전의 Note, Proposal/Potential agreement/Observation, TDoc 행 이후의 내용도 모두 유지한다.
- TDoc 목록은 구간 종료 조건이 아니다. 개별 메타데이터 행만 제외하며 나머지 본문 순서를 유지한다.

## 섹션 전체 보존 회귀 검증

합성 DOCX로 비정규 표식, TDoc 전후 문장, 번호 없는 소제목, ID로 시작하는 본문 인용,
ID 목록, 이미지가 있는 ID 문단, TDoc 전용 표 및 일반 내용이 섞인 표를 검증했다.
실제 #124 문서에서는 1,539행 제외, 90개 정확한 agenda 매칭, 10.5.1.1의 표식 없는 내용과
10.5.2.1의 들여쓰기 기반 중첩 목록을 검증했다. 추출 캐시 버전은 12다.
FTP의 새 중간 note 본문은 확보하지 못했으므로 비정규 표식 테스트는 합성 fixture임을 구분한다.

Orca comment 업데이트는 현재 Orca runtime 연결 오류로 기록하지 못했다. 이 파일이 최신 검증 기록이다.


## 제공된 RAN1#126 v00 + EOM 결과물

`test_runs/ran1-126/site/ran1/index.html`에 생성했다. 전체 기록은
`test_runs/ran1-126/BUILD_STATUS.md`, 셀별 원문·시간·AI 매칭은 `site/verification.json`에 있다.
`scripts/build_ran1_126_local.py`로 API 없이 재현한다. 원본 86개 셀 단위이며 별도 메인 상세표 18개 항목도 포함한다.
5일·5개 방, 연결 AI 45개(미매칭 0), EOM 70개 섹션 중 63개 본문, 제외 TDoc 1,733행이다.
제공 파일 기준이며 FTP의 최신 버전이라고 새로 확인한 것은 아니다.

EOM에서 새로 확인된 OLE 수식 11개는 내장 이미지와 VML 크기를 이용해 안전한 정적 preview로 표시한다.
이를 위해 parser version을 13으로 올렸고 합성 OLE 회귀 테스트를 추가했다.
OMML 307개 중 5개(9.1.2: 1, 10.8.3: 4)는 텍스트 fallback, Wingdings 기호 2개(9.3.1)는 placeholder다.
최신 전체 테스트 **398 passed (11.03s)**. 생성 JS 문법과 정적 파일/매칭 검사 통과.
서버 시작은 여전히 socket.bind 권한 제한으로 실패하여 최신 브라우저 검증은 미실행이다.


## 2026-09-30: AI 10.6.2 목록 들여쓰기 수정

EOM의 `List Paragraph`(aff) 스타일은 공통 `left=840`(42pt)을 가지지만,
목록 numId=41은 단계마다 `left=720/1440/2160/...`을 정의한다. 종전 깊이 판정은
공통 스타일 840을 단계별 위치 위에 덮어 모든 목록을 한 단계로 출력했다.
직접 문단 위치는 유지하고, 없으면 numbering 단계별 위치를 먼저 사용하도록 수정했다.
일반 문단 서식 cascade를 변경한 것이 아니라 GFM 목록의 깊이 판정 규칙을 보완한 것이다.

최소 재현 `tests/test_docx_list_indent.py`는 수정 전 실패하고 수정 후 통과한다.
직접 들여쓰기 우선순위와 일반 문단의 기존 42pt 유지도 검증했다.
실제 #126 10.6.2의 원문과 글머리 67개는 그대로이며, 중첩은 원본의 최대 5단계까지 복원됐다.
검증값은 `test_runs/ran1-126/indent-verification.json`에 기록했다.
parser version 14로 갱신했고 #126 전체 HTML을 재생성했다. 전체 **401 passed (10.97s)**.
브라우저의 실제 좌표 검사는 Chromium 실행 시 MachPortRendezvousServer의 Permission denied(1100)로 차단됐다.
재실행 가능한 확인 스크립트는 `test_runs/ran1-126/verify_indent_browser.py`에 있다.

## 2026-09-30 실제 취득 → Gemini 전체 파이프라인 시도

사용자의 실제 동작 검증은 브라우저가 아니라 온라인 문서 탐색/취득 및 Gemini 일정 생성이다.
다운로드 활성화, 19개 슬롯 캐시 초기화 상태로 main.py 실행. API 키는 값 출력 없이 사용했다.
기본 sync와 #126 보조 Inbox의 HTTP/FTP DNS 해석 실패 후 로컬 일정으로 대체했으나,
Gemini 첫 슬롯도 3회 모두 DNS 실패했다. 종료 코드 1, 새 사이트 생성 없음.
기존 캐시 19개 및 236세션 스냅샷 복원 확인. 온라인 최신성/전체 생성은 검증 완료가 아니다.
세부 로그: test_runs/ran1-126-online/pipeline.log 및 BUILD_STATUS.md.
재현: scripts/verify_ran1_online.py --env-file <API 키가 설정된 .env 경로>.
apple.com DNS도 실패하고 직접 IP TCP probe도 EPERM이므로 에이전트 네트워크 제한이 유력하다.

## 2026-09-30 샌드박스 해제 후 실제 온라인 검증 완료



- 메인: RAN1#126 online and offline schedules - v03.docx
- Hiroki: RAN1#126 schedule for Hiroki Adhoc2 sessions_v08_1.docx
- Sorour: RAN1#126 Sorour sessions online and offline schedules - v01.docx
- chairman note: Chair notes RAN1#126_v15.docx (sync의 v15가 보조위치의 v13보다 최신)

remote-sources.json에 URL, Last-Modified, 크기, SHA-256을 기록했다. 캐시 본문 바이트의 SHA와 모두 일치.
사용자가 제공한 v00/eom이나 이전 일정 JSON을 이번 신규 파싱의 대체 입력으로 사용하지 않았다.
API 키는 원본 .env에서 프로세스 환경으로 전달했으며 출력/결과물에 포함하지 않았다.

## 실행 및 결과

1. pipeline.log: 다운로드 활성화, --rebuild-slots. 19개 슬롯 모두 cold 파싱, Gemini 일정 호출 19회, 캐시 생략 0회.
   그룹 제목 정리도 실제 Gemini 호출. 238세션, 5일, Europe/Amsterdam, 2026-08-24~28 생성.
2. 실제 결과에서 wildcard/제목 접미어/끝 마침표/슬래시 표기 연결 문제 발견 후 결정론적 해결.
   명시적 .x만 존재하는 하위 AI로 확장. 10.5.3.1/4는 10.5.3.1 및 10.5.3.4, 6/8.1은 6 및 8.1.
   단순 번호는 prefix 확장하지 않는다. 원본 schedule agenda_item 및 팝업 문자열은 보존.
3. XML에서 Heading 4/ilvl=2/%1.%2.%3의 카운터 오류 발견. 번호 템플릿보다 깊은 스타일을 제한해
   9.3.1 R2D / 9.3.2 D2R / 9.3.3 Other procedures를 분리했다. parser version 15.
4. final-pipeline.log: 수정 후 다운로드 활성화로 정상 재빌드. 동일 입력이라 슬롯 19개 캐시 재사용, Gemini 일정 호출 0회.
   최초의 실제 Gemini 생성 결과를 보존하면서 chairman note의 변환 캐시만 갱신했다.
5. 최종 72개 AI 섹션, 본문 HTML 65개, TDoc 메타데이터 1,733행 제외.
   일정 참조 AI 45개 모두 정확히 연결, missing_exact_sections=[]; 238개 셀 렌더링 확인.
6. 일정 페이지와 본문 65개 모두 HTTP 200. 파일 바이트 및 HTML 파일명 hash 일치.
7. 원격 변경 감지 재실행: changed=false, errors=[] (update-verification.json).
8. 전체 pytest: 409 passed (13.96s). DOCX 번호 깊이 및 AI 표기 회귀 추가.
   agreement module 및 실제 렌더링된 inline JS syntax 검사, git diff --check 통과.
   templates/schedule.js 원본은 템플릿 placeholder 때문에 node --check 대상이 아니며 치환 후 결과를 검사했다.

## 미리보기 및 재현

현재 http://127.0.0.1:8875/ran1/ 에서 실행 중. 기존 사용자 서버는 변경하지 않았다.

```sh
.venv/bin/python scripts/verify_ran1_online.py --env-file /Users/karlla/programming/3GPPSchedule/.env
```

새 test_runs/ran1-online-<timestamp>에 로그, attempt.json, site/ 생성. 실제 API를 호출하는 명령이다.
기존 결과만 다시 서비스하려면:

```sh
.venv/bin/python -m http.server 8875 --bind 127.0.0.1 --directory test_runs/ran1-online-20260930-040319/site
```

## 남은 한계

- Gemini 일정 해석은 확률적이다. 이전 저장본 236세션과 이번 cold 결과 238세션의 차이는 있으므로
  원문 대비 모든 개별 세션의 의미/분 단위 시간이 수작업 인증된 것은 아니다.
- v15의 OMML 307개 중 302개가 MathML, 5개는 텍스트 fallback. OLE 11개는 정적 이미지 표시.
  레거시 기호 일부는 placeholder, WMF 변환 시 브라우저 폰트 대체로 간격이 달라질 수 있다.
- Word 페이지 배치/주석 풍선은 재현하지 않는다. 원문을 요약문으로 대체하지 않았다.
- 이번 검증의 대상은 실제 온라인 취득/Gemini 파이프라인이다. 실제 브라우저 시각 검증 완료를 주장하지 않는다.
- 배포/main 병합은 하지 않았다. 원본 checkout은 읽기만 했고 변경하지 않았다.

Orca worktree comment에 온라인 검증 완료 상태 기록.
