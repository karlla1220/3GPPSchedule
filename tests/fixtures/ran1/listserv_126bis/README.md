# RAN1#126bis 이메일 리스트 테스트셋

`3GPP_TSG_RAN_WG1` 리스트(list.etsi.org, LISTSERV 17.5)의 최신 메일 50통과, 같은 주의 피드 268통이다.
2026-10-13 20:28 ~ 10-14 12:08(제주 시각, 회의 수요일 오전)에 받았다. 메일에서 세부 일정을 뽑는 방식을 시험하는 데 쓴다.

## 파일

- `feed_rss2_limit50.xml`: 피드 원본(`wa.exe?RSS&L=3GPP_TSG_RAN_WG1&v=2.0&LIMIT=50`).
- `mails.json`: 메일마다 피드 항목(`title`, `author`, `date`, `link`, `snippet`)과 본문 전체(`body`, `body_url`)를 담았다.
  - `n`은 피드 순서(1이 최신)이고, `id`는 LISTSERV 메시지 ID다.
  - 본문은 메일 페이지(`A2`)에 있는 첫 `text/plain` 부분의 `A3` 링크에서 `&header=1`을 빼고 받았다. 메일 주소는 아카이브가 `[log in to unmask]`로 가린다.
- `message_page_83dadb14.html`: 메일 페이지(`A2`) 하나. 본문 부분 링크를 찾는 테스트용이고, 큰 인라인 `<style>`만 비웠다.
- `feed_rss2_week.xml`: 같은 날 받은 피드 268통(`LIMIT=500`, 이번 주 아카이브 전체, 10-08 ~ 10-14). 본문은 없다. 제목 태그와 AI 번호 규칙을 넓게 시험하는 데 쓴다.
- `email_threads_chair.json`: Chair notes(`Chair notes RAN1#126bis_v06.docx`)의 AI 절마다 적힌 이메일 토론 줄 38개(`[126bis-…] Email discussion on … – FL (회사)`). 태그, AI, FL 이름, 회사로 나눴다.
  `agreements.py`의 `email_threads`와 같은 형식이다. 두 줄은 노트에 `Ema-il discussion`으로 적혀 있다(8.3, 9.1).
- `agenda_items.json`: 이 회의의 Agenda 번호 68개(`agenda.csv`).
- `grid.json`: 정답을 붙일 때 쓴 시간표(커밋 `3d489af`). 세션 `id`는 `요일 시작-끝 방`이다(`Wed 12:30-13:00 Yeongju A Hall`). 방은 `room_col_start − 2`번째다(1열은 시간 축, 렌더러와 같다).
  수요일 칸은 10-13 15:38(제주 시각)부터 바뀌지 않았고, 50통은 모두 그 뒤에 왔다.
- `labels.json`: 사람이 읽고 붙인 정답이다.
  - `mails`: 일정과 관련된 항목이 있는 메일 26통, 항목 35개. 메일 번호(`n`)마다 항목 목록이다.
    - `kind: session` (26개): online 또는 offline 세션. 이 둘은 늘 방에서 열리므로 시간표의 세션 하나를 가리킨다. `session`이 그 세션의 `id`이고, `detail`이 셀에 더할 내용이다(다룰 Proposal, 시간 나눔, FL summary 등).
      시각이 없어도 세션을 가리키면 여기에 든다("for Wednesday's online discussion", "our offline session today").
    - `kind: tentative` (2개): 시간표에 없는 시각의 online·offline. 의장·부의장과 정해 두고 시간표가 아직 바뀌지 않은 것으로 본다.
      그 AI의 다른 세션이 열리는 방에서 그 시각에 다른 AI가 잡힌 셀(`session`)에 "maybe <AI>"로 붙이고, 그 AI의 필터에도 걸리게 한다.
      #15 "online session this morning from 12:00"(10.3.1): 10.3.1 online은 모두 Yeongju A Hall이고, 그 방 12:00 칸이 AI 8.1(Sorour)이다. #3은 그 세션용 FL proposal이다.
    - `kind: informal` (7개): off-offline이나 FL이 기다리는 곳. 방이 없거나 방 밖이다("outside Sorour's session room", "in front of Yeongju A Hall", "near the cute bears"). Agreement 아래 부차 정보로 갈 것들이다.
    - 메일 날짜는 보낸 사람의 시간대다(`+0800`, `+0000`, `+0900`). "today", "tomorrow"는 회의 시각(Asia/Seoul)으로 바꾼 송신 시각으로 읽는다.
      #32는 `00:42 +0800`(제주 수 01:42)에 보낸 메일이라 "PM session tomorrow"가 수요일 저녁 세션이다.
    - `quote`는 그 항목의 근거가 된 원문이다.
  - 마감만 적은 메일(#23, #43, #44, #49), 지난 세션 이야기(#8, #40, #50), 다음 세션에서 다루지 않는다는 말(#41의 10.6.1.1 online, #45)은 항목이 없다.
  - `superseded`: 뒤 메일이 앞 메일의 계획을 바꾼 경우(#36이 #38을 바꿨다).
  - `need_full_body`: 아래 규칙으로 본문을 더 받아야 하는 메일 22통.

## 메일의 AI를 정하는 규칙 (`email_sessions.agenda_of`)

1. **제목의 첫 대괄호 태그를 Chair notes의 이메일 토론과 맞춘다.** 태그는 회의 부분을 떼고 나머지로 비교한다.
   - `126b-`와 `126bis-`는 같은 회의다. 268통 중 6통이 `126b-`다.
   - 이번 회의 토론과 나머지가 같은데 회의가 지난 번호(`126-`)인 태그는 지난 회의 태그를 그대로 쓴 것으로 보고 그 토론에 붙인다(`match: earlier_meeting`, 4통, 모두 10.3.2 FL).
   - `Post-126-…`은 지난 회의의 후속 토론이라 붙이지 않는다.
2. **메일에 적힌 AI 번호를 모은다.** 이 회의 Agenda에 있는 번호만 센다.
   - 형식: `AI 8.2`, `agenda item 9.4.1`, `(Agenda 9.5.1)`, `[9.3.3]`, 번호만 적은 것(`10.5.2.2`, 첫 줄의 `9.3.1`), drafts 폴더 경로의 가장 깊은 번호.
   - 번호 바로 앞에 다른 것임을 밝히는 낱말이 있으면 세지 않는다(`Proposal`, `Section`, `Clause`, `Table`, `observation`, `v`, `TS` 등).
     50통 전체 본문에서 이 낱말 때문에 빠진 Agenda 번호는 `Clause 8.1` 하나다.
3. **합친다.**
   - 토론이 있으면 그 AI다. 적힌 번호가 그 아래이면 좁힌다(9.3 토론의 `[9.3.3]` → 9.3.3). 바깥 번호는 `other`로 따로 둔다(#41이 10.6.x 세션을 짜면서 적은 10.6.1.2, 10.6.3).
   - 토론이 없으면 제목의 번호, 없으면 본문의 번호다(`match: stated`). TDoc 개정 안내 20통이 모두 하나의 AI를 얻는다.
   - Agenda가 없으면 번호는 믿지 않는다.
4. **Chair notes 쪽:** `Email discussion` 낱말은 글자 사이에 하이픈이나 공백이 들어가도 읽는다(`Ema-il`, `E-mail`). 태그 없이 낱말만 있는 줄은 `tag: null`로 남겨 스냅숏에서 보이게 한다.

## 피드 미리보기가 잘렸는지 판정하는 규칙

피드 본문은 100단어 안팎에서 잘리고 끝에 `[...]`가 붙는다. URL 인자로는 길이를 바꿀 수 없다.
가장 최근 답장만 있으면 되므로, 다음 두 조건을 모두 만족할 때만 본문을 받는다.

1. 미리보기가 `[...]`로 끝난다.
2. 미리보기 안에 인용 머리글(`From:`, `发件人`, `-----原始邮件-----`, `Original`, `> On … wrote:` 등)이 없다. 인용 표시 `>`로 시작하는 줄도 찾고, 줄바꿈은 `\r\n`을 `\n`으로 맞춘 뒤 찾는다.

50통 중 43통이 잘렸고, 이 규칙으로 본문을 받는 메일은 22통이다.
그중 4통(#5, #9, #23, #33)은 최근 답장이 이미 다 들어 있었지만, 미리보기만으로는 그걸 알 수 없어서 받는다.
반대로 받지 않기로 한 메일 중에 최근 답장이 잘린 경우는 없다.
정답이 있는 메일 26통 중 본문을 받는 것은 8통이다(#5, #9, #19, #21, #28, #32, #39, #46).

미리보기와 본문을 비교할 때는 미리보기에 들어 있는 `<https://…>` 링크 표기를 먼저 지워야 한다.

## 접근할 때 주의할 점 (2026-10-14 확인)

- **Python 기본 User-Agent는 막힌다.** UA가 `Python-urllib/…`, `python-requests/…`이거나 비어 있으면 서버가 응답 없이 연결을 끊는다.
  `3GPPSchedule/1.0 (+https://github.com/karlla1220/3GPPSchedule)`, `Mozilla/5.0`, `curl`, `Wget`은 된다.
- **TLS 연결이 가끔 멈춘다.** 18번 중 1번꼴로 TLS 핸드셰이크에서 응답이 없다가 약 11초 뒤 끊긴다.
  UA를 보내기 전 단계라 UA와는 상관없다. 같은 경로로 연 www.3gpp.org에서는 생기지 않았다. 재시도는 간격을 두고 한 번만 한다.
- 응답에 rate-limit 헤더나 WAF 흔적은 없다(IIS 10). 그래도 요청은 순차로, 간격을 두고 보낸다. 병렬 부하 시험은 하지 않았다.
