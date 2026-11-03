# RAN1#126bis Main 버전과 부의장 칸

부의장 문서 안의 옛 Main 사본을 빼는 규칙(`working_groups/ran1/main_history.py`)을 시험하는 데 쓴다(`tests/test_main_history.py`).
2026-11-03에 3GPP FTP에서 받은 원본을 `parser.parse_docx()`로 읽은 그대로다. 파일마다 원본의 SHA-256이 있다.

- `main_versions.json`: Main 10개 버전을 Draft v00~v03, v00~v05 순서로 담았다. 버전마다 블록(`Friday_00`)별 칸 글(공백 정리)이 있다.
  Draft는 `tsg_ran/WG1_RL1/TSGR1_126b/Inbox/Chair_notes/`, 정식본은 `Meetings_3GPP_SYNC/RAN1/Inbox/Chair_notes/`에서 받았다.
- `vice_chair_cells.json`: Hiroki v08과 Sorour v01의 모든 표의 칸(표 번호, 요일, 블록, 원문)이다. `Inbox/Hiroki_notes/`, `Inbox/Sorour_notes/`에 있다.
