# 3GPP Schedule Viewer

3GPP FTP 서버에서 최신 회의 스케줄 DOCX 파일을 다운로드하고, Gemini API로 비정형 테이블 텍스트를 파싱하여 **CSS Grid 기반 간트차트 스타일의 정적 HTML 페이지**를 생성합니다.

## 주요 기능

- 3GPP FTP에서 최신 스케줄 DOCX 자동 다운로드 (ZIP 내 문서 자동 추출 지원)
- **다중 소스 스케줄 통합**: Chair_notes 외 부의장(Hiroki, Sorour 등) 폴더의 스케줄도 자동 탐색·다운로드
- `python-docx`로 테이블 구조 추출 및 병합 셀 처리 (TextBox 색상 기반 방 매칭)
- Gemini API를 사용한 비정형 텍스트 → 구조화 세션 데이터 변환 (결과 캐싱)
- **다중 소스 크로스레퍼런스**: 같은 시간대의 여러 스케줄 테이블을 하나의 LLM 호출로 통합하여 가장 상세한 세션 정보(AI 번호 등) 도출
- **회의 시간대 자동 감지**: Chair notes DOCX에서 개최지 정보를 추출하여 IANA 타임존 자동 설정
- 요일별 탭 전환, 오늘 날짜 자동 선택되는 단일 HTML 간트차트 생성 (그룹별 색상, 자동 새로고침)
- GitHub Actions를 통한 자동 빌드 및 GitHub Pages 배포 (평일 5분 간격 변경 감지)

## 요구사항

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) 패키지 매니저
- Google Gemini API 키 ([Google AI Studio](https://aistudio.google.com/apikey)에서 발급)

## 설치

```bash
# 저장소 클론
git clone https://github.com/<your-username>/3GPPSchedule.git
cd 3GPPSchedule

# 의존성 설치
uv sync
```

## 환경 설정

프로젝트 루트에 `.env` 파일을 생성하고 필요한 환경 변수를 설정합니다:

```bash
cp .env.example .env
```

```dotenv
GEMINI_API_KEY=your-api-key-here
SCHEDULE_CONTACT_NAME=Your Name
SCHEDULE_CONTACT_EMAIL=your.email@example.com
```

- `GEMINI_API_KEY`: Gemini API 키 ([Google AI Studio](https://aistudio.google.com/apikey)에서 발급)
- `SCHEDULE_CONTACT_NAME`: 생성된 HTML에 표시될 담당자 이름
- `SCHEDULE_CONTACT_EMAIL`: 생성된 HTML에 표시될 담당자 이메일

## 사용법

### 전체 파이프라인 (다운로드 → 파싱 → HTML 생성)

```bash
uv run python main.py
```

3GPP FTP에서 최신 스케줄 파일을 다운로드하고, 파싱 후 `docs/index.html`을 생성합니다.

### 로컬 DOCX 파일 사용

```bash
uv run python main.py --local "Chair_notes/RAN1#124 online and offline schedules - v00.docx"
```

이미 다운로드된 DOCX 파일을 직접 지정하여 HTML을 생성합니다.

### 다운로드 건너뛰기

```bash
uv run python main.py --no-download
```

FTP 다운로드를 건너뛰고 `downloads/Chair_notes/` 폴더에 있는 가장 최신 로컬 파일을 사용합니다.

### 출력 경로 지정

```bash
uv run python main.py --output output/schedule.html
```

기본 출력 경로는 `docs/index.html`입니다.

## CLI 옵션 요약

| 옵션 | 설명 |
|---|---|
| (없음) | FTP 다운로드 → 파싱 → HTML 생성 전체 파이프라인 |
| `--local <path>` | 지정한 로컬 DOCX 파일로 HTML 생성 |
| `--no-download` | 다운로드 없이 최신 로컬 파일 사용 |
| `--output <path>` | HTML 출력 경로 (기본: `docs/index.html`) |

## 프로젝트 구조

```
main.py             # CLI 진입점, 전체 파이프라인 오케스트레이션
downloader.py       # 3GPP FTP에서 스케줄 DOCX 다운로드 (다중 폴더 탐색, ZIP 자동 추출)
parser.py           # python-docx로 DOCX 테이블 구조 추출 (TextBox 색상 매칭, 회의 장소 추출)
merger.py           # 다중 소스 스케줄 데이터를 (day, time_block) 단위로 수집·통합
session_parser.py   # Gemini API로 셀 텍스트 → 세션 데이터 파싱 (타임존 감지, 방 매칭, 그룹 정규화)
models.py           # 데이터 모델 (Session, DaySchedule, Schedule, ScheduleSource 등)
generator.py        # CSS Grid 기반 HTML 간트차트 생성 (그룹별 색상, 자동 새로고침)
check_update.py     # FTP 변경 감지 (GitHub Actions cron용, 다중 폴더 비교)
.env.example        # 환경 변수 템플릿
pyproject.toml      # 프로젝트 의존성 (uv)
docs/
  index.html        # 생성된 정적 사이트 (GitHub Pages 배포용)
  .schedule_state.json  # FTP 변경 감지 상태 캐시
.github/workflows/
  deploy.yml        # 스케줄 빌드 및 배포 워크플로우 (변경 감지 + 빌드 + Pages 배포)
  pages.yml         # docs/ 변경 시 GitHub Pages 자동 배포
```

## 다중 소스 통합 파이프라인

```
FTP Inbox/
├── Chair_notes/     → 메인 스케줄 (방 레이아웃 기준)
├── Hiroki_notes/    → 부의장 상세 스케줄
└── Sorour_notes/    → 부의장 상세 스케줄
         ↓
    discover_schedule_sources()    # Inbox/ 폴더 탐색, 스케줄 파일 발견
    download_all_schedules()       # 모든 소스 다운로드 (ZIP 자동 추출)
         ↓
    parse_docx(main, max_tables=2)         # 메인 테이블 구조 추출
    parse_docx(vc, max_tables=None)        # 부의장 전체 테이블 추출
         ↓
    extract_meeting_location()     # Chair notes에서 개최지 추출
    get_timezone_from_location()   # Gemini로 IANA 타임존 결정
         ↓
    collect_time_slot_data()       # (day, time_block)별 데이터 수집 + 중복 제거
         ↓
    parse_time_slots()             # 시간대별 1회 Gemini 호출 → 통합 세션 리스트
    normalize_group_headers()      # 그룹명 정규화
    fill_missing_groups()          # 누락된 그룹 이름 보완
         ↓
    save_html()                    # CSS Grid 간트차트 생성
```

부의장 스케줄은 메인 스케줄과 다른 테이블 구조를 가질 수 있으며, AI 번호(예: 9.1.1, 10.3.2) 같은 상세 정보를 포함합니다. 시스템은 LLM과 문서 컨텍스트를 활용하여 부의장 상세 정보를 메인 스케줄의 올바른 방에 매핑합니다.

## GitHub Actions 자동 배포

두 개의 워크플로우가 설정되어 있습니다:

### `deploy.yml` — 스케줄 빌드 및 배포

- **자동 실행**: 평일 5분마다 FTP 변경 감지 → 변경 시 재빌드 및 배포
- **수동 실행**: GitHub Actions 탭에서 `workflow_dispatch`로 트리거 가능
  - `check-and-deploy`: 변경 감지 후 변경 시에만 빌드/배포 (기본값)
  - `force-deploy`: 변경 여부 무시, 강제 빌드/배포
  - `deploy-only`: 빌드 없이 현재 `docs/` 그대로 배포
- **변경 감지**: `check_update.py`가 모든 스케줄 폴더의 파일 메타데이터를 비교
- **배포 방식**: `docs/index.html` 생성 → 상태 저장 → 자동 커밋 & 푸시 → GitHub Pages 배포

### `pages.yml` — GitHub Pages 배포

- `docs/` 경로 변경 시 또는 수동 트리거로 GitHub Pages 배포

### GitHub Secrets 설정

Repository Settings → Secrets and variables → Actions에서 다음 시크릿을 추가하세요:

- `GEMINI_API_KEY`: Gemini API 키

### GitHub Pages 설정

Repository Settings → Pages에서:
- **Source**: GitHub Actions

## 라이선스

MIT

## Bug report or Feature request

Please send email to duckhyun.bae@lge.com or use issue in repo. 
