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
