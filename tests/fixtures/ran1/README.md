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
