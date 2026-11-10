# RAN1 chairman note verification input

`Chair notes RAN1#124 - v09.docx` is an actual public 3GPP document copied
from the user's existing download cache. SHA-256:
`0089af1622cee5168e8b8c815a9ec8fa9345878ab8ebcb023b7f82cbbcdf4476`.
No fresh FTP download or Microsoft Word visual comparison is claimed.

The document has 90 numbered agenda sections, 68 standalone `Agreement`
markers and one `Way forward agreement` marker. Extraction now preserves all
90 agenda sections (80 have content), excluding 1,539 TDoc metadata rows; it
does not gate content on agreement markers. Its three embedded figures use legacy WMF;
there are no PNG/JPEG fallbacks or embedded OLE equations. Separately, 32
OMML equations exist. Tests cover both headless Java SVG (when installed)
and portable Python SVG conversion, plus independent OMML conversion.
The source remains this local fixture; the viewer does not offer a download.

`scripts/preview_agreements.py` generates an explicitly labelled verification
schedule around these real agreements; session timings are demo fixtures,
not a re-parsed RAN1#124 timetable. See `AGREEMENTS_VERIFICATION.md` for scope
and limits.

## RAN1#126bis schedule

`RAN1#126b online and offline schedules - v05.docx` is the actual public
chair schedule (Chair_notes, uploaded 2026-10-15), supplied by the user.
SHA-256: `751696ae773f906842757e6814ad61031183c64e182be19162895d372a77395e`.
Its Tuesday 17:00-19:30 block has two rows: one cell per room in the first,
`Early dinner (60)` in the second, once in the online table and once in the
offline table. `tests/test_parser.py` checks the rows the parser records;
`tests/test_slot_placement.py` places sessions from them.

## RAN1#127 schedule

`Draft RAN1#127 online and offline schedules - v00.docx` is the actual public
chair schedule mailed to the RAN1 list on 2026-11-08 (before FTP had it).
SHA-256: `4386c117175d50c74fe5a913ac593d514b5eec54eff38bf548dbc7e337fc7d2f`.
It moves the breaks: 08:00-10:30, coffee 10:30-11:00, 11:00-12:30, lunch
12:30-14:00, 14:00-16:30, coffee 16:30-17:00, 17:00-19:00 (RAN1#126bis:
08:30-10:30, 11:00-13:00, lunch 13:00-14:30, 14:30-16:30, 17:00-19:30).
Its third table, the old "Detailed Schedule for RAN1 Main Session" template,
still writes the RAN1#126bis times. `tests/test_time_blocks.py` reads the
blocks from it, and Friday's two cells merged from 14:00 down to 19:00 (over
an afternoon coffee row that covers Monday-Thursday only) as irregular cells.

`llm_127_friday_1100.json` is the slot merge's answer for that schedule's
Friday 11:00 block, recorded once (gemini-3.8-flash, low; merge prompt v8).
Brk2 lists `6GR (TBD) / .10.8 Sensing ()` and `R20 (TBD) / A-IoT Phase2 (TBD)`
with no lengths; the answer gives each item its own session with a null
duration, and `tests/test_time_blocks.py` places them as parts 1/2 and 2/2.

`llm_127_tuesday_0800.json` is the slot merge's answer for Tuesday 08:00,
recorded the same way. Brk2 writes `6GR (60)` over `.10.8 Sensing (70)`, so the
cell adds up to 160 min of 150; the header's 60 wins (3 of 3 runs). A build
that took the 70 failed placement (10.8 Sensing past 10:30).
