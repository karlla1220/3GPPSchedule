# RAN#113 v04 regression fixtures

Retrieved 2026-09-15 (Asia/Seoul):

- Timeplan: https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN/Inbox/Chair/RAN%23113%20time%20plan%20v04.zip
- Agenda: https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN/Agenda/agenda.csv

The ZIP contains `RAN#113 time plan v04.docx`. It has 34 non-empty schedule
cells, three shared break rows, and 22 untimed topic rows. The CSV is a snapshot
of a mutable URL, not a meeting-versioned archive.

`interpretation.json` is a real Gemini result reviewed against the DOCX and the
user-provided screenshot. Tests replay it without API calls. Key assertions:
three physical rooms; Tuesday Breakout relocation at 12:30; generic black
blocks span the day's grid; explicit Main text overrides black font; red
opening/closing notes do not assign a Breakout room; 51 timed blocks; Tuesday
ends at 19:00 and Thursday at 17:00. Untimed topics remain unscheduled.

A broad 6G label is insufficient evidence for an AI link. In particular, the
6G spec modernization block must not inherit the unrelated Study on 6GR AI.
