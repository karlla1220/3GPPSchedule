# 3GPP Schedule Viewer

3GPP FTP 서버에서 최신 회의 스케줄 DOCX 파일을 다운로드하고, Gemini API로 비정형 테이블 텍스트를 파싱하여 **CSS Grid 기반 간트차트 스타일의 정적 HTML 페이지**를 생성합니다.

## 주요 기능

- 3GPP FTP에서 최신 스케줄 DOCX 자동 다운로드
- `python-docx`로 테이블 구조 추출 및 병합 셀 처리
- Gemini API를 사용한 비정형 텍스트 → 구조화 세션 데이터 변환 (결과 캐싱)
- 요일별 탭 전환, 오늘 날짜 자동 선택되는 단일 HTML 간트차트 생성
- GitHub Actions를 통한 자동 빌드 및 GitHub Pages 배포

## 요구사항

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) 패키지 매니저
- Google Gemini API 키

## 설치

```bash
# 저장소 클론
git clone https://github.com/<your-username>/3GPPSchedule.git
cd 3GPPSchedule

# 의존성 설치
uv sync
```

## 환경 설정

프로젝트 루트에 `.env` 파일을 생성하고 Gemini API 키를 설정합니다:

```bash
cp .env.example .env
```

```dotenv
GEMINI_API_KEY=your-api-key-here
```

API 키는 [Google AI Studio](https://aistudio.google.com/apikey)에서 발급받을 수 있습니다.

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

FTP 다운로드를 건너뛰고 `Chair_notes/` 폴더에 있는 가장 최신 로컬 파일을 사용합니다.

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
downloader.py       # 3GPP FTP에서 스케줄 DOCX 다운로드
parser.py           # python-docx로 DOCX 테이블 구조 추출
session_parser.py   # Gemini API로 셀 텍스트 → 세션 데이터 파싱
models.py           # 데이터 모델 (Session, DaySchedule, Schedule 등)
generator.py        # CSS Grid 기반 HTML 간트차트 생성
docs/index.html     # 생성된 정적 사이트 (GitHub Pages 배포용)
```

## GitHub Actions 자동 배포

`.github/workflows/deploy.yml` 워크플로우가 설정되어 있습니다:

- **자동 실행**: 평일 3시간마다 (UTC 기준)
- **수동 실행**: GitHub Actions 탭에서 `workflow_dispatch`로 트리거 가능
- **배포 방식**: `docs/index.html` 생성 후 변경사항이 있으면 자동 커밋 & 푸시

### GitHub Secrets 설정

Repository Settings → Secrets and variables → Actions에서 다음 시크릿을 추가하세요:

- `GEMINI_API_KEY`: Gemini API 키

### GitHub Pages 설정

Repository Settings → Pages에서:
- **Source**: Deploy from a branch
- **Branch**: `main`, folder `/docs`

## 라이선스

MIT
