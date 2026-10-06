# RAN1 비정규 note 조사 — 2026-09-29

## 확인 수준

이번 조사에서 **새로운 FTP DOCX/ZIP 본문은 취득하지 못했다**. 직접 다운로드를 마친 조사로 해석하면 안 된다.

- HTTPS 디렉터리 조회: 403/502 또는 web 도구 접근 실패.
- 작업 환경 httpx 다운로드: `ConnectError [Errno 8] nodename nor servname provided, or not known`.
- 검색 도구에 수집된 공식 FTP 목록은 확인했다. 크롤링 시점은 지난주이며 현재 FTP 실시간 상태와 같다고 단정하지 않는다.
- 기존 로컬 다운로드의 DOCX도 확인했으나, EOM #124 v09 외에는 대부분 일정 문서였다. 실제 중간 chair/vice-chair note의 Potential agreement/Proposal 사례를 확보하지 못했다.

## 공식 FTP 목록에서 확인한 범위

[현재 SYNC Inbox, 이름 역순](https://www.3gpp.org/ftp/meetings_3gpp_sync/RAN1/Inbox?sortby=namerev):
`Chair_notes`, `Hiroki_notes`, `Sorour_notes`, `drafts` 폴더가 별도로 존재한다.
루트에는 `Chair notes RAN1#126_v13.docx`도 보인다. 따라서 Chair_notes 하위만 찾는 현재 탐색은 루트 파일을 놓칠 수 있다.

[아젠다별 draft 목록 예시](https://www.3gpp.org/ftp/meetings_3gpp_sync/RAN1/Inbox/drafts/7%28NR%20Maint.%29/%5BRel-18%20MOB%5D%20A-CSI%20reporting%20configuration%20in%20TS%2038.214):
`R1-250xxxx Summary on aperiodic CSI reporting configuration for Rel-18 LTM in TS 38.214_v00_Mod.docx`,
`..._v01_Mod_Nokia.docx`, `..._v02_Nokia_HW.docx`라는 개별 수정본이 목록에 보인다.
이 예시는 2025년 자료이며 #126 자료와 섞지 않는다. 파일명/버전 존재만 확인한 것으로 본문 표식 확인은 아니다.

## 변경 전 마커 기반 구현에서 재현한 문제

`working_groups.ran1.agreements.START.fullmatch` / `STOP.match`로 점검한 결과다.
아래 문자열은 **로직 검사 입력**이며 FTP 본문에서 새로 추출한 인용이 아니다.

| 입력 | 추출 시작 | 추출 종료 |
|---|---|---|
| Agreement | 예 | 아니오 |
| Potential agreement | 아니오 | 아니오 |
| Potential agreement 1: | 아니오 | 아니오 |
| Proposal | 아니오 | 예 |
| Proposal 1: | 아니오 | 아니오 |
| Observation 1: | 아니오 | 아니오 |
| Tentative agreement | 아니오 | 아니오 |
| Agreement on channel design | 아니오 | 아니오 |
| Way forward agreement on UE sided data collection: | 예 | 아니오 |
| R1-2601577 + tab + FL summary | 아니오 | 예 |

첫 표식 이전의 potential/proposal은 누락될 수 있고, 이미 Agreement 구간 안이면 번호 붙은
proposal/potential이 앞선 합의 내용에 붙을 수 있다. `Proposal` 단독 표식 이후는 다음 Agreement까지
통째로 제외된다. EOM #124의 69건 검증을 이 모든 문서 유형의 지원 근거로 삼을 수 없다.

취득 경로도 현재는 meeting ID가 이름에 들어간 chairman note에 초점을 맞춘다.
TDoc 번호만 있는 moderator summary, vice-chair의 AI별 note는 파일명만으로 회의를 검증할 수 없으므로
미팅 디렉터리 및 원문 metadata를 함께 확인하는 별도 연결이 필요하다.

## 조사 당시 제안과 적용 상태

1. 같은 미팅·정확한 AI에 속한 chair/vice-chair/moderator 자료를 출처와 버전별로 관리한다.
   문서 수정 시각이 최신이라는 이유만으로 EOM 확정 합의보다 우선하지 않는다.
2. 보여 줄 범위는 아젠다 section 원문을 기준으로 결정한다. `Agreement` 표식은 섹션에서 내용을
   제거하는 필터가 아니라 원문의 문단 유형을 구분하는 단서로 사용한다.
3. `Agreement`, `Potential agreement`, `Proposal`, `Observation`, `Way forward`의 원래 표식과
   본문 순서를 유지한다. 비확정 내용을 확정 Agreement로 재명명하거나 승격하지 않는다.
4. `R1-…`는 본문 참조일 수도 있다. 제목·저자와 탭/열 구조를 가진 TDoc 목록 행임을 확인한 경우
   그 행만 제외하는 방식을 검토한다. 단순 `R1-` 시작만으로 이후 section을 버리지 않는다.
5. 제목 없는 단락은 원문 문맥으로 유지하고 상태를 임의로 추정하지 않는다. 색상/굵기는 보조 단서이며
   합의 상태를 확정하는 유일한 규칙으로 삼지 않는다.

실제 중간 문서 본문을 확보하기 전에는 위 방향의 경계 규칙을 검증했다고 주장할 수 없다.
후속 요청에 따라 2–5번의 섹션 보존/TDoc 행 필터를 구현했다(parser version 12).
실제 #124와 합성 fixture로 검증했으며, 1번의 다중 문서 취득·선택 확장은 적용하지 않았다.
