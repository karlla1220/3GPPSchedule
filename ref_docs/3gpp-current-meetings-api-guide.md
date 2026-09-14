# 3GPP 현재 개최 중인 미팅 조회 매뉴얼

검증일: 2026-09-15. 대상: 다른 프로그램의 서버에서 3GPP Portal을 직접 조회하는 개발자.

## 1. 연동 방식

**`GetMeetings`에 POST 요청을 한 번 보내 여러 WG의 미팅 목록을 받고, 응답 중 `MeetingPeriod == "ONGOING"`인 항목을 선택한다.** 필터링과 중복 제거에는 추가 API 호출이 필요 없다.

현재 저장소에서 확인한 범위는 RAN 전체회의와 RAN1~4다. SA/CT를 포함한 3GPP 전체 조직 조회 방법이나 TB ID는 이 문서에서 검증하지 않았다.

현재 코드는 현재 진행 중인 미팅만 요청하는 서버 측 필터를 사용하지 않는다. 아래 방식은 일정 범위의 첫 페이지를 한 번 조회하는 방식이며, 모든 미팅의 누락 없는 조회를 항상 보장하는 방식은 아니다. 조회 기간과 페이지 제한은 §6을 참고한다.

## 2. HTTP 요청

```text
POST https://portal.3gpp.org/webservices/Rest/Meetings.svc/GetMeetings
```

아래 요청은 그대로 실행할 수 있는 검증 당시 예시다. 운영에서는 날짜를 매 실행 시 갱신한다.

```bash
curl --fail-with-body --silent --show-error --max-time 60 \
  'https://portal.3gpp.org/webservices/Rest/Meetings.svc/GetMeetings' \
  -H 'Accept: application/json, text/plain, */*' \
  -H 'Content-Type: application/json;charset=UTF-8' \
  -H 'Origin: https://portal.3gpp.org' \
  -H 'Referer: https://portal.3gpp.org/' \
  -H 'User-Agent: Mozilla/5.0 (3gpp-tdoc-viewer) PortalMeetingsClient/1.0' \
  --data-raw '{
    "getMeetingsInput": {
      "StartRow": 0,
      "ResultsPerPage": 100,
      "SortBy": "Date",
      "SortAscending": false,
      "StartDate": "2026-08-15 00:00:00",
      "EndDate": "2026-09-16 23:59:59",
      "Tbs": [373, 379, 380, 381, 382],
      "IncludeChildTbs": false,
      "IncludeNonTBMeetings": false,
      "Reference": "",
      "Registered": false
    }
  }'
```

API키, Authorization, Cookie 없이 공개 목록 조회에 성공했다. 헤더는 기존 코드에 맞췄으며 모두 필수인지는 미검증이다. 브라우저 직접 호출의 CORS 동작은 검증하지 않았으므로 서버에서 실행한다.

| 파라미터 | 설정 및 용도 |
|---|---|
| `getMeetingsInput` | 기존 코드가 사용하는 JSON 최상위 래퍼 |
| `StartRow` | 첫 페이지는 `0` |
| `ResultsPerPage` | 기존 기본값 `100`. 서버 최대값은 미검증 |
| `SortBy` / `SortAscending` | `"Date"` / `false`. 날짜 내림차순 |
| `StartDate` / `EndDate` | 검색 기간. `YYYY-MM-DD HH:mm:ss` 형식 |
| `Tbs` | 조직 ID 배열. 복수 지정 실측 완료 |
| `IncludeChildTbs` | `false`. 대상 WG를 배열에 명시 |
| `IncludeNonTBMeetings` | `false`. 기존 코드와 동일 |
| `Reference` / `Registered` | `""` / `false`. 기존 코드와 동일 |

| 대상 | `Tbs`에 지정할 ID |
|---|---:|
| RAN 전체회의 | 373 |
| RAN1 | 379 |
| RAN2 | 380 |
| RAN3 | 381 |
| RAN4 | 382 |

RAN1만 조회하려면 `"Tbs": [379]`를 사용한다. 앱 내부 WG 번호인 `1`을 그대로 보내지 않는다.

## 3. 응답과 현재 개최 중 판정

응답 최상위는 JSON 배열이며 `data`나 `results` 래퍼가 없다. 검증 시 9행 중 1행이 `ONGOING`이었다. 다음은 실제 응답의 필요한 필드만 발췌한 예시다. 실행 날짜가 달라지면 결과도 달라진다.

```json
[
  {
    "Id": 60799,
    "TBId": 373,
    "Title": "3GPPRAN#113",
    "StartDate": "2026-09-14 09:00:00",
    "EndDate": "2026-09-17 17:30:00",
    "MeetingPeriod": "ONGOING",
    "Status": "",
    "Location": "Madrid",
    "Country": "ES",
    "MtgDocURL": "https://ftp.3gpp.org/tsg_ran/TSG_RAN/TSGR_113/Docs/"
  }
]
```

판정 규칙은 다음과 같다.

1. `MeetingPeriod == "ONGOING"`인 행을 선택한다. 종료된 항목의 `"ENDED"` 값도 실응답에서 확인했다.
2. 같은 `Id`를 하나로 합친다. 여러 TB에 속하는 동일 이벤트가 TB별로 반환되므로 `TBId`는 배열로 모아 보존한다.
3. 동시 개최에 대응하도록 결과는 항상 배열로 반환한다.
4. `MeetingPeriod`가 없거나 알 수 없는 값이면 개최 중으로 추측하지 않고 모니터링한다. `Status`는 별도 필드이며 실응답에서는 빈 문자열이었다.

이는 **Portal이 개최 중으로 분류한 미팅**이다. 해당 순간에 개별 세션이 진행되고 있다는 보장은 아니다.

| 필드 | 용도 및 주의점 |
|---|---|
| `Id` | Portal 미팅 ID. 자체 DB의 ID와 구별 |
| `TBId` | 소속 조직 ID |
| `Title` / `ShortTitle` | 미팅 이름 |
| `StartDate` / `EndDate` | 일정. 문자열에 UTC 오프셋이 없음 |
| `StartTimeZone` / `EndTimeZone` | 실응답에 존재하지만 값의 규격과 시간 변환 방식은 미검증 |
| `MeetingPeriod` | 개최 기간 분류. 현재 개최 중 필터에 사용 |
| `Location` / `LocFreeText` / `Country` | 장소, 자유 기재 장소, 국가 |
| `Type` | 미팅 종류. 기존 코드에 `OR`, `AH`, `WS` 설명이 있음 |
| `MtgDocURL` | 미팅별 자료 URL. `null`일 수 있음 |
| `DocURL` | WG의 자료 URL. 미팅별 URL로 사용하지 않음 |

## 4. Python 구현 예제: 외부 HTTP 호출 한 번

Python 3.9 이상 표준 라이브러리만 사용한다. 검색 기간은 예시로 한국 날짜 기준 31일 전부터 다음 날까지 설정했다. 검색 후보를 확보하기 위한 운영 설정이며, Portal의 날짜가 한국 시간이라는 의미는 아니다.

```python
import json
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


def get_current_meetings():
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    page_size = 100
    payload = {
        "getMeetingsInput": {
            "StartRow": 0,
            "ResultsPerPage": page_size,
            "SortBy": "Date",
            "SortAscending": False,
            "StartDate": f"{today - timedelta(days=31)} 00:00:00",
            "EndDate": f"{today + timedelta(days=1)} 23:59:59",
            "Tbs": [373, 379, 380, 381, 382],
            "IncludeChildTbs": False,
            "IncludeNonTBMeetings": False,
            "Reference": "",
            "Registered": False,
        }
    }
    request = Request(
        "https://portal.3gpp.org/webservices/Rest/Meetings.svc/GetMeetings",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://portal.3gpp.org",
            "Referer": "https://portal.3gpp.org/",
            "User-Agent": "Mozilla/5.0 (3gpp-tdoc-viewer) PortalMeetingsClient/1.0",
        },
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        rows = json.load(response)
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("Unexpected Portal response shape")
    if len(rows) >= page_size:
        raise RuntimeError("First page may be truncated; completeness is unknown")

    meetings = {}
    for row in rows:
        if row.get("MeetingPeriod") != "ONGOING":
            continue
        meeting_id = row.get("Id")
        if type(meeting_id) is not int:
            raise ValueError("Missing or invalid Portal meeting Id")
        if meeting_id not in meetings:
            meetings[meeting_id] = {**row, "tb_ids": []}
        tb_id = row.get("TBId")
        if tb_id is not None and tb_id not in meetings[meeting_id]["tb_ids"]:
            meetings[meeting_id]["tb_ids"].append(tb_id)
    return list(meetings.values())


if __name__ == "__main__":
    print(json.dumps(get_current_meetings(), ensure_ascii=False, indent=2))
```

`[]`는 조회된 검색 범위에 개최 중인 미팅이 없다는 뜻이다. HTTP 오류, 타임아웃, JSON 형식 오류, 페이지 상한 도달은 예외로 처리하고 ‘개최 중인 미팅 없음’으로 바꾸지 않는다. 자동 재시도나 추가 페이지 요청은 하지 않는다.

## 5. 현행 앱과의 차이

- `fetch_meetings_page()`는 동일한 POST를 한 번 수행하며 `tb_ids` 복수 지정이 가능하다.
- `iter_meetings()` / `collect_meetings()`는 WG별로 페이지를 순회하므로 그대로 사용하면 외부 호출이 여러 번 발생할 수 있다.
- `normalize_meeting_row()`는 유효한 RAN `MtgDocURL`이 없는 행을 제외한다. 미팅 존재 여부만 필요한 연동에서는 이 제외 규칙을 복사하지 않는다. 자료 URL이 없는 이벤트가 누락될 수 있다.
- 자체 `GET /api/meetings`는 DB 저장 목록을 반환한다. 매번 3GPP에 실시간 조회하지 않으며 현재 개최 중 전용 필터도 없다.
- 자료 목록 Excel을 받는 `GenerateDocumentList.aspx?meetingId=...` 호출은 이 용도에 필요 없다.

## 6. 한 번 조회의 제약과 운영 확인

- **검색 기간:** `StartDate` / `EndDate` 조건이 시작일 기준인지 기간 중첩 기준인지는 미확정이다. 실측에서는 검색 종료일 이후에 끝나는 개최 중 미팅도 반환됐다. 오늘만 지정하지 않고 과거 기간을 포함하지만, 31일 전보다 먼저 시작한 장기 이벤트의 조회를 보장하지는 못한다.
- **페이지 제한:** 100행이 반환되면 다음 페이지가 있을 수 있다. 기존 코드는 반환 행 수만큼 `StartRow`를 늘려 추가 조회한다. 한 번만 호출한다면 불완전할 수 있음을 알려야 한다. 서버가 별도 상한을 적용할 가능성도 있어 100행 미만이라는 사실만으로 절대적인 전수 조회를 보장하지 않는다.
- **시간:** UTC나 현지 시간을 추측해 날짜를 비교하지 않고, 이 용도에서는 `MeetingPeriod`를 사용한다.
- **이벤트 범위:** `IncludeNonTBMeetings: false`여도 TB에 연결된 Social Event가 반환됐다. 기술 회의만 원한다면 실제 종류 값을 확인해 별도 필터를 정의해야 한다.
- **호환성:** 현행 코드와 공개 엔드포인트 실측에 기반한 연동 절차다. 공식적인 안정성 보장을 확인한 API 명세는 아니다. 미지의 `MeetingPeriod`, 응답 형식 변경, 조회 실패를 감지한다.

‘이용 프로그램에서 한 번 호출하면 항상 전체 결과를 받아야 한다’는 엄격한 요구라면 중계 API에서 페이지 조회와 집계를 수행하는 설계가 필요하다. 이 경우 3GPP에 보내는 HTTP 요청은 여러 번일 수 있다.

## 7. 근거

- 검증한 공개 엔드포인트: [3GPP Portal GetMeetings](https://portal.3gpp.org/webservices/Rest/Meetings.svc/GetMeetings). POST API이며 브라우저에서 GET으로 열면 같은 결과가 나오지 않을 수 있다.
- 참조 코드: `karlla1220/tdoc-service` commit `c3d5d887d8525c30366fc429065cfebb60daf845`.
- [조회 및 정규화 코드](../repos/tdoc-service/packages/tdoc-service/src/tdoc_service/services/portal_meetings.py): URL, 헤더, TB ID, 요청 구조, 페이지 처리, 필드 매핑.
- [정규화 테스트](../repos/tdoc-service/packages/tdoc-service/tests/test_portal_meetings.py): 날짜, ID, `MeetingPeriod` 등의 저장.
- [자체 API 라우터](../repos/tdoc-service/packages/tdoc-service/src/tdoc_service/api/routers/meetings.py): `/api/meetings`의 DB 조회 동작.
- 2026-09-15 실제 HTTP POST: §2의 JSON으로 9행 조회. `Id=60799`의 `ONGOING`과 동일 `Id=86014`의 TB별 중복을 확인했다. API키, Cookie, Authorization은 사용하지 않았다.
- Python 예제는 저장한 실제 응답으로 개최 중 항목 추출을 검증했고, 중복 행을 이용해 ID별 병합도 확인했다.
