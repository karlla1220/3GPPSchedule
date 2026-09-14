import json
from zipfile import ZipFile

import pandas as pd

from working_groups.ran1.agenda_descriptions import (
    AGENDA_DESCRIPTION_COLUMN,
    AGENDA_ITEM_COLUMN,
    annotate_sessions_with_agenda_descriptions,
    build_agenda_description_pairs,
    build_agenda_description_pairs_from_csv,
    build_agenda_description_pairs_from_docx,
    save_agenda_description_json,
    strip_derived_description_fields,
)


def test_build_agenda_description_pairs_deduplicates_rows():
    df = pd.DataFrame(
        [
            {AGENDA_ITEM_COLUMN: "10", AGENDA_DESCRIPTION_COLUMN: "Rel-20 Study of 6GR"},
            {AGENDA_ITEM_COLUMN: "10", AGENDA_DESCRIPTION_COLUMN: "Rel-20 Study of 6GR"},
            {AGENDA_ITEM_COLUMN: "10.5.4", AGENDA_DESCRIPTION_COLUMN: "Scheduling"},
        ]
    )

    assert build_agenda_description_pairs(df) == [
        {"agenda_item": "10", "description": "Rel-20 Study of 6GR"},
        {"agenda_item": "10.5.4", "description": "Scheduling"},
    ]


def test_build_agenda_description_pairs_from_csv_handles_plain_agenda_csv(tmp_path):
    csv_path = tmp_path / "agenda.csv"
    csv_path.write_text(
        '"1","Opening of the meeting"\n'
        '"9.","Release 20 NR"\n'
        '"9.1","Artificial Intelligence for NR"\n',
        encoding="utf-8",
    )

    assert build_agenda_description_pairs_from_csv(csv_path) == [
        {"agenda_item": "1", "description": "Opening of the meeting"},
        {"agenda_item": "9", "description": "Release 20 NR"},
        {"agenda_item": "9.1", "description": "Artificial Intelligence for NR"},
    ]


def test_build_agenda_description_pairs_from_agenda_docx_restores_heading_numbers(tmp_path):
    docx_path = tmp_path / "agenda.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="13"/></w:numPr></w:pPr><w:r><w:t>Opening of the meeting</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading2"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="16"/></w:numPr></w:pPr><w:r><w:t>Call for IPR</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading2"/><w:numPr><w:ilvl w:val="1"/><w:numId w:val="13"/></w:numPr></w:pPr><w:r><w:t>Competition Law Statement</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>10Rel-20 Study of 6GR</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>10.5Multi-antenna system</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading3"/><w:numPr><w:ilvl w:val="2"/><w:numId w:val="43"/></w:numPr></w:pPr><w:r><w:t>General aspects and frameworks</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/><w:numPr><w:ilvl w:val="1"/><w:numId w:val="43"/></w:numPr></w:pPr><w:r><w:t>WUS and operation</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>
</w:styles>
"""
    numbering_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="34">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
    <w:lvl w:ilvl="1"><w:start w:val="2"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2"/></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="45">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.1"/></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="41">
    <w:lvl w:ilvl="1"><w:start w:val="5"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2"/></w:lvl>
    <w:lvl w:ilvl="2"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2.%3"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="13"><w:abstractNumId w:val="34"/></w:num>
  <w:num w:numId="16"><w:abstractNumId w:val="45"/></w:num>
  <w:num w:numId="43"><w:abstractNumId w:val="41"/></w:num>
</w:numbering>
"""
    with ZipFile(docx_path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)
        docx.writestr("word/styles.xml", styles_xml)
        docx.writestr("word/numbering.xml", numbering_xml)

    assert build_agenda_description_pairs_from_docx(docx_path) == [
        {"agenda_item": "1", "description": "Opening of the meeting"},
        {"agenda_item": "1.1", "description": "Call for IPR"},
        {"agenda_item": "1.2", "description": "Competition Law Statement"},
        {"agenda_item": "10", "description": "Rel-20 Study of 6GR"},
        {"agenda_item": "10.5", "description": "Multi-antenna system"},
        {"agenda_item": "10.5.0", "description": "General aspects and frameworks"},
        {"agenda_item": "10.6", "description": "WUS and operation"},
    ]


def test_annotate_falls_back_from_x_to_parent():
    sessions = [
        {
            "room_name": "RAN1_main",
            "name": "10.5.4.x",
            "duration_minutes": 30,
            "specified_start_time": None,
            "chair": None,
            "group_header": "6G",
            "agenda_item": None,
        }
    ]

    annotated = annotate_sessions_with_agenda_descriptions(
        sessions,
        {
            "10": "Rel-20 Study of 6GR",
            "10.5.4": "Downlink control channel, scheduling and HARQ operation",
        },
    )

    assert annotated[0]["description"] == (
        "Downlink control channel, scheduling and HARQ operation"
    )
    assert annotated[0]["agenda_descriptions"][0]["matched_agenda_item"] == "10.5.4"
    assert annotated[0]["agenda_descriptions"][0]["hierarchy"] == [
        {
            "agenda_item": "10",
            "description": "Rel-20 Study of 6GR",
            "has_description": True,
        },
        {
            "agenda_item": "10.5",
            "description": None,
            "has_description": False,
        },
        {
            "agenda_item": "10.5.4",
            "description": "Downlink control channel, scheduling and HARQ operation",
            "has_description": True,
        },
    ]


def test_strip_derived_description_fields():
    assert strip_derived_description_fields(
        [
            {
                "name": "10.5.4.x",
                "description": "generated",
                "agenda_descriptions": [],
            }
        ]
    ) == [{"name": "10.5.4.x"}]


def test_build_agenda_description_pairs_from_docx_extracts_deep_number_pattern(tmp_path):
    docx_path = tmp_path / "agenda.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading4"/></w:pPr><w:r><w:t>9.3.2.1 Deep agenda item</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Heading4"><w:name w:val="heading 4"/><w:pPr><w:outlineLvl w:val="3"/></w:pPr></w:style>
</w:styles>
"""
    with ZipFile(docx_path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)
        docx.writestr("word/styles.xml", styles_xml)

    assert build_agenda_description_pairs_from_docx(docx_path) == [
        {"agenda_item": "9.3.2.1", "description": "Deep agenda item"},
    ]


def test_save_agenda_description_json_records_source_metadata(tmp_path):
    output_path = tmp_path / "agenda_item_description.json"

    save_agenda_description_json(
        [{"agenda_item": "1", "description": "Opening"}],
        output_path,
        source_file="R1-2601750_Draft agenda.docx",
        source_url="https://example.com/Agenda/R1-2601750.zip",
        source_type="agenda_docx",
        source_uploaded_at="2026-05-18T08:30:00",
        source_agenda_file="R1-2601750.zip",
    )

    data = json.loads(output_path.read_text())
    assert data["source_file"] == "R1-2601750_Draft agenda.docx"
    assert data["source_url"] == "https://example.com/Agenda/R1-2601750.zip"
    assert data["source_uploaded_at"] == "2026-05-18T08:30:00"
    assert data["source_agenda_file"] == "R1-2601750.zip"
