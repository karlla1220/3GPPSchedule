Plan: 3GPP Schedule DOCX → Static Gantt Site
TL;DR: Python으로 3GPP FTP에서 최신 스케줄 DOCX를 다운로드 → python-docx로 테이블 추출 → Gemini API로 비정형 셀 텍스트를 구조화된 세션 데이터로 파싱 → CSS Grid 기반 간트차트 스타일의 단일 HTML 페이지 생성 (요일 탭 전환, 오늘 날짜 자동 선택) → GitHub Actions로 자동 빌드/배포 (GitHub Pages).

Steps
1. 의존성 추가 — pyproject.toml

python-docx (DOCX 파싱)
httpx (FTP 페이지 다운로드)
beautifulsoup4 (FTP 디렉토리 리스팅 HTML 파싱)
google-generativeai (Gemini API)
2. FTP 다운로더 모듈 — 새 파일 downloader.py

https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/Inbox/Chair_notes 페이지를 fetch하여 파일 목록 파싱
*schedules* 패턴 파일 중 가장 높은 버전(v00, v01 등) 선택하여 다운로드
이미 최신 파일이 로컬에 있으면 스킵 (파일명 기반 비교 또는 Last-Modified 헤더)
다운로드된 파일을 Chair_notes 에 저장
3. DOCX 테이블 추출 — 새 파일 parser.py

python-docx로 DOCX 열기, 모든 document.tables 순회
테이블 구조 파악: 행=시간대, 열=장소×요일
첫 번째 행(들)에서 요일 헤더 추출 (Monday, Tuesday, ...)
두 번째 행에서 룸/장소 이름 추출 (Room 이름 등)
병합 셀 처리 (python-docx의 cell.grid_span 등 활용)
각 셀의 raw 텍스트를 (day, room, time_block_index) 키로 매핑
결과: dict[tuple[str, str, int], str] — (요일, 장소, 시간블록인덱스) → 셀 텍스트
4. LLM 기반 세션 파싱 — 새 파일 session_parser.py

Gemini API (google-generativeai)를 사용하여 각 셀의 raw 텍스트를 구조화
프롬프트: 셀 텍스트 + 해당 시간 블록의 시작/종료 시간 정보를 제공하고, JSON 형태로 세션 목록을 반환하도록 요청
파싱 결과 모델:
시간블록의 시작 시간으로부터 duration을 누적하여 각 세션의 start_time, end_time 계산
비용 최적화: 모든 셀 텍스트를 배치로 하나의 API 콜로 처리하거나, 테이블 단위로 묶어서 호출
파싱 결과를 JSON 캐시 파일로 저장하여 동일 DOCX 재처리 방지
5. 데이터 모델 — models.py 또는 parser.py 내 dataclass

Session: name, chair, agenda_item, start_time (datetime.time), end_time, duration_minutes, room, day
DaySchedule: day_name, rooms (list), sessions (list[Session])
Schedule: meeting_name, days (list[DaySchedule]), source_file, generated_at
6. HTML 생성기 — 새 파일 generator.py

Python string으로 단일 index.html 생성 (template engine 미사용)
레이아웃 구조:
상단: 미팅 이름, 소스 파일명, 생성 시각
요일 탭 바: Mon | Tue | Wed | Thu | Fri (JavaScript로 탭 전환, 오늘 요일 자동 선택)
각 요일 탭 내용:
가로축(열) = 장소 (Room 이름)
세로축(행) = 시간 (08:30 ~ 19:30, 5분 단위 그리드)
커피/점심 브레이크 행을 회색 등으로 표시
각 세션 = CSS Grid grid-row span으로 시간 범위 표현
CSS Grid 구현:
Grid rows: 08:30부터 19:45까지 5분 단위 = (11시간 15분 = 675분) / 5 = 135 rows
브레이크 시간대 (10:30-11:00, 13:00-14:30, 16:30-17:00)도 그리드에 포함, 별도 스타일
세션 블록: grid-row: start / end 로 위치 결정
Grid columns: 장소 수 (3+2=5개 또는 동적)
색상 체계:
카테고리별 색상 자동 배정 (HSL 색상환에서 균등 분배)
같은 카테고리 세션은 모든 장소/요일에서 동일 색상
시간 겹침 시각화: 같은 시간대(가로줄)에 여러 장소에 세션이 있으면 자연스럽게 나란히 보여 겹침 확인 가능
반응형: 모바일에서도 가로 스크롤로 확인 가능
모든 CSS/JS는 HTML 파일에 인라인 포함 (단일 파일 배포)
출력: docs/index.html (GitHub Pages의 /docs 소스)
7. CLI 진입점 — main.py 수정

argparse로 CLI 구성:
python main.py — 전체 파이프라인 (다운로드 → 파싱 → 생성)
python [main.py](http://_vscodecontentref_/3) --local <docx_path> — 로컬 파일에서 직접 생성
python [main.py](http://_vscodecontentref_/4) --no-download — 다운로드 스킵, 최신 로컬 파일 사용
환경변수: GEMINI_API_KEY
8. GitHub Actions 워크플로우 — .github/workflows/build.yml

트리거:
schedule: cron — 매 2시간마다 또는 하루 4~6회 (3GPP 미팅 기간에만 의미 있으므로 적절한 빈도)
workflow_dispatch — 수동 트리거 가능
Steps:
Checkout repo
Setup Python 3.12 + uv 캐시
uv sync (의존성 설치)
python main.py 실행 (GEMINI_API_KEY는 GitHub Secrets에서 주입)
생성된 docs/index.html을 commit & push (변경 있을 때만)
GitHub Pages 설정: repo Settings → Pages → Source: Deploy from a branch, branch main, folder /docs
9. .gitignore 업데이트

Chair_notes/*.docx — DOCX 파일은 FTP에서 매번 다운로드하므로 git에 미포함 (또는 포함할지 선택)
__pycache__/, .venv/ 등 기본 Python 패턴
Verification
로컬 테스트: python [main.py](http://_vscodecontentref_/6) --local "Chair_notes/RAN1#124 online and offline schedules - v00.docx" 실행 → docs/index.html 생성 확인 → 브라우저에서 열어 간트차트 확인
세션 파싱 검증: Gemini API 반환 JSON이 각 시간블록의 총 시간과 일치하는지 검증 (세션 duration 합 ≤ 블록 duration)
시각 검증: 겹치는 세션이 같은 가로줄에 나란히 표시되는지 브라우저에서 확인
CI 검증: GitHub Actions에서 workflow_dispatch로 수동 트리거 → GitHub Pages에 배포 확인
Decisions
LLM 파싱: 셀 텍스트가 비정형이므로 regex 대신 Gemini API 사용 — 비용은 낮음 (테이블 셀 텍스트가 소량)
단일 HTML 파일: 외부 CSS/JS dependency 없이 인라인으로 모든 것 포함 — 배포 단순화
5분 그리드: 08:30~19:45 범위의 135칸 그리드로 시간 해상도 표현
GitHub Pages /docs: 별도 빌드 브랜치 없이 main 브랜치의 docs/ 폴더 사용