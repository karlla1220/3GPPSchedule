# RAN1#126bis 금요일 08:30 슬롯의 칸

증분 병합이 실제로 바뀐 칸만 다시 만드는지 시험하는 데 쓴다(`tests/test_cell_changes.py`).
2026-10-15에 이 슬롯의 증분 병합이 두 번 틀렸다(용어는 `GLOSSARY.md`).

- Main v04 → v05: Main에서 바뀐 칸은 203(순서만)뿐인데, 바뀌지 않은 Yeongju B Hall까지 Main의 `A-IoT Phase2 (120)`로 덮여 Hiroki의 9.3.1~9.3.3이 사라졌다.
- Hiroki v07 → v08: Hiroki에서 바뀐 칸은 Yeongju B Hall뿐인데, Hiroki 문서 안의 옛 Main 사본 칸이 Tamna BC Hall·Yeongju A Hall을 Main v02 내용으로 되돌렸다.

## 파일

- `friday_0830.json`: 세 조합의 금요일 첫 시간 블록(08:30–10:30)을 `merger.collect_time_slot_data()`가 만든 그대로 담았다.
  - `slots`: 조합 이름(`Main v05 + Hiroki v07 + Sorour v01`)마다 소스 목록이다. 소스마다 `label`과 칸(`room_label`, `cell_text`, `fallback_start_time`)이 있다.
    부의장 칸 가운데 그 조합의 Main과 글자까지 같은 칸은 이미 빠져 있다(Hiroki의 Yeongju B Hall 사본 `R20 (120) / A-IoT Phase2 (120)`).
  - `main_rooms`: 그날의 방(Tamna BC Hall, Yeongju A Hall, Yeongju B Hall은 online, 201, 203은 offline).
  - `documents`: 각 버전의 원본 파일 이름. 3GPP FTP `Meetings_3GPP_SYNC/RAN1/Inbox/`의 `Chair_notes`, `Hiroki_notes`, `Sorour_notes`에 있다.
