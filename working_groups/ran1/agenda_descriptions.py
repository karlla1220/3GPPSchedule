"""Fetch and apply 3GPP agenda-item descriptions."""

from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from urllib.parse import unquote

import pandas as pd
from bs4 import BeautifulSoup

from shared import ftp_transport

from .downloader import _iter_local_files, _local_doc_preference


TDOC_LIST_URL = "https://www.3gpp.org/ftp/Meetings_3GPP_SYNC/RAN1/Inbox/Tdoc_list"
DEFAULT_JSON_PATH = Path("docs/ran1/agenda_item_description.json")
DEFAULT_DOWNLOAD_DIR = Path("downloads/ran1/Tdoc_list")
DEFAULT_AGENDA_DOWNLOAD_DIR = Path("downloads/ran1/Agenda")

AGENDA_ITEM_COLUMN = "Agenda item"
AGENDA_DESCRIPTION_COLUMN = "Agenda item description"
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": WORD_NS}

_DERIVED_SESSION_FIELDS = {
    "description",
    "agenda_descriptions",
    "agenda_description",
}


@dataclass(frozen=True)
class TdocXlsx:
    """One XLSX file advertised in the TDoc_list directory."""

    name: str
    url: str


@dataclass(frozen=True)
class StyleInfo:
    """Subset of Word paragraph-style metadata needed for heading detection."""

    style_id: str
    type: str | None
    name: str | None
    based_on: str | None
    outline_level: int | None
    num_id: str | None
    ilvl: int | None


@dataclass(frozen=True)
class NumberingLevel:
    """One Word numbering level definition."""

    num_fmt: str | None
    lvl_text: str | None
    start: int


@dataclass(frozen=True)
class ParagraphInfo:
    """Word paragraph information used by the agenda-heading extractor."""

    text: str
    style_id: str | None
    outline_level: int | None
    num_id: str | None
    ilvl: int | None


def find_tdoc_xlsx_files(listing_url: str = TDOC_LIST_URL) -> list[TdocXlsx]:
    """Return XLSX links from a 3GPP TDoc_list directory listing."""
    resp = ftp_transport.get(listing_url, listing=True, follow_redirects=True, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    files: list[TdocXlsx] = []
    for link in soup.find_all("a"):
        href = link.get("href") or ""
        text = link.get_text(" ", strip=True)
        if not href.lower().endswith(".xlsx"):
            continue
        name = unquote(text or href.rsplit("/", 1)[-1])
        files.append(TdocXlsx(name=name, url=href))
    return files


def _natural_sort_key(value: str) -> list[int | str]:
    parts = re.split(r"(\d+)", value)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def _w_attr(element: ET.Element | None, name: str) -> str | None:
    if element is None:
        return None
    return element.get(f"{{{WORD_NS}}}{name}")


def _w_int_attr(element: ET.Element | None, name: str) -> int | None:
    value = _w_attr(element, name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class StyleMap:
    """Resolve Word paragraph styles to effective outline levels."""

    def __init__(self, styles_xml: bytes):
        root = ET.fromstring(styles_xml)
        self.styles: dict[str, StyleInfo] = {}
        for style in root.findall(".//w:style", NS):
            style_id = _w_attr(style, "styleId")
            if not style_id:
                continue
            ppr = style.find("w:pPr", NS)
            num_pr = ppr.find("w:numPr", NS) if ppr is not None else None
            self.styles[style_id] = StyleInfo(
                style_id=style_id,
                type=_w_attr(style, "type"),
                name=_w_attr(style.find("w:name", NS), "val"),
                based_on=_w_attr(style.find("w:basedOn", NS), "val"),
                outline_level=_w_int_attr(
                    ppr.find("w:outlineLvl", NS) if ppr is not None else None,
                    "val",
                ),
                num_id=_w_attr(num_pr.find("w:numId", NS), "val")
                if num_pr is not None
                else None,
                ilvl=_w_int_attr(num_pr.find("w:ilvl", NS), "val")
                if num_pr is not None
                else None,
            )

    def get_outline_level(
        self,
        style_id: str | None,
        paragraph_outline_level: int | None,
    ) -> int | None:
        if paragraph_outline_level == 9:
            return None
        if paragraph_outline_level is not None:
            return paragraph_outline_level

        current = style_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            style = self.styles.get(current)
            if style is None or style.type != "paragraph":
                return None
            if style.outline_level == 9:
                return None
            if style.outline_level is not None:
                return style.outline_level
            current = style.based_on

        return self._heuristic_outline_level(style_id)

    def get_style_num_pr(self, style_id: str | None) -> tuple[str | None, int | None]:
        current = style_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            style = self.styles.get(current)
            if style is None or style.type != "paragraph":
                return None, None
            if style.num_id is not None or style.ilvl is not None:
                return style.num_id, style.ilvl
            current = style.based_on
        return None, None

    def _heuristic_outline_level(self, style_id: str | None) -> int | None:
        style = self.styles.get(style_id or "")
        if style is None or style.type != "paragraph":
            return None

        candidates = [style.style_id, style.name or ""]
        for candidate in candidates:
            normalized = candidate.strip().lower()
            match = re.search(
                r"(?:heading|überschrift|标题|見出し|제목)\s*([1-9])",
                normalized,
                re.IGNORECASE,
            )
            if match:
                return int(match.group(1)) - 1
            if normalized == "title":
                return 0
        return None


class NumberingMap:
    """Render agenda heading numbers from Word numbering metadata."""

    def __init__(self, numbering_xml: bytes | None):
        self.num_to_abstract: dict[str, str] = {}
        self.levels: dict[tuple[str, int], NumberingLevel] = {}
        self.start_overrides: dict[tuple[str, int], int] = {}
        self._applied_overrides: set[tuple[str, int]] = set()
        self.heading_counters: dict[int, int] = {}

        if not numbering_xml:
            return

        root = ET.fromstring(numbering_xml)
        for abstract in root.findall(".//w:abstractNum", NS):
            abstract_id = _w_attr(abstract, "abstractNumId")
            if abstract_id is None:
                continue
            for lvl in abstract.findall("w:lvl", NS):
                ilvl = _w_int_attr(lvl, "ilvl")
                if ilvl is None:
                    continue
                start = _w_int_attr(lvl.find("w:start", NS), "val")
                self.levels[(abstract_id, ilvl)] = NumberingLevel(
                    num_fmt=_w_attr(lvl.find("w:numFmt", NS), "val"),
                    lvl_text=_w_attr(lvl.find("w:lvlText", NS), "val"),
                    start=0 if start is None else start,
                )

        for num in root.findall(".//w:num", NS):
            num_id = _w_attr(num, "numId")
            abstract_id = _w_attr(num.find("w:abstractNumId", NS), "val")
            if num_id is None or abstract_id is None:
                continue
            self.num_to_abstract[num_id] = abstract_id
            for override in num.findall("w:lvlOverride", NS):
                ilvl = _w_int_attr(override, "ilvl")
                start = _w_int_attr(override.find("w:startOverride", NS), "val")
                if ilvl is not None and start is not None:
                    self.start_overrides[(num_id, ilvl)] = start

    def apply_explicit_marker(self, marker: str) -> None:
        parts = [int(part) for part in marker.split(".") if part.isdigit()]
        if not parts:
            return
        for level, value in enumerate(parts):
            self.heading_counters[level] = value
        self._reset_deeper(len(parts) - 1)

    def get_heading_marker(
        self,
        *,
        num_id: str | None,
        ilvl: int | None,
        outline_level: int,
    ) -> str | None:
        if num_id is None or ilvl is None:
            return None

        abstract_id = self.num_to_abstract.get(num_id)
        if abstract_id is None:
            return None

        level = self.levels.get((abstract_id, ilvl))
        if level is None or not level.lvl_text:
            return None

        counter_level = max(outline_level, ilvl)
        self._reset_deeper(counter_level)
        override_start = self._consume_start_override(num_id, ilvl)
        current = self.heading_counters.get(counter_level)
        if override_start is not None:
            self.heading_counters[counter_level] = override_start
        else:
            start = self._level_start(abstract_id, ilvl)
            self.heading_counters[counter_level] = (
                start if current is None else current + 1
            )

        for parent_level in range(counter_level):
            if parent_level not in self.heading_counters:
                self.heading_counters[parent_level] = self._level_start(
                    abstract_id,
                    parent_level,
                )

        return self._render_lvl_text(level.lvl_text)

    def _consume_start_override(self, num_id: str, ilvl: int) -> int | None:
        override_key = (num_id, ilvl)
        if (
            override_key in self.start_overrides
            and override_key not in self._applied_overrides
        ):
            self._applied_overrides.add(override_key)
            return self.start_overrides[override_key]
        return None

    def _level_start(self, abstract_id: str, ilvl: int) -> int:
        level = self.levels.get((abstract_id, ilvl))
        return level.start if level is not None else 0

    def _reset_deeper(self, level: int) -> None:
        for existing in list(self.heading_counters):
            if existing > level:
                del self.heading_counters[existing]

    def _render_lvl_text(self, lvl_text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            level = int(match.group(1)) - 1
            return str(self.heading_counters.get(level, 0))

        marker = re.sub(r"%([1-9])", replace, lvl_text)
        return marker.strip().rstrip(".")


def pick_tdoc_xlsx(files: list[TdocXlsx]) -> TdocXlsx:
    """Pick one XLSX file from the listing.

    The current policy picks the naturally-last filename. The caller does not
    depend on this being the newest file; it just provides a deterministic
    useful default.
    """
    if not files:
        raise ValueError("No .xlsx files found in TDoc_list listing")
    return sorted(files, key=lambda f: _natural_sort_key(f.name))[-1]


def download_tdoc_xlsx(
    xlsx: TdocXlsx,
    download_dir: Path = DEFAULT_DOWNLOAD_DIR,
) -> Path:
    """Download a TDoc-list XLSX file and return its local path."""
    download_dir.mkdir(parents=True, exist_ok=True)
    path = download_dir / xlsx.name
    resp = ftp_transport.get(xlsx.url, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    return path


def load_agenda_description_dataframe(xlsx_path: Path) -> pd.DataFrame:
    """Load the agenda-item columns from a TDoc-list XLSX file."""
    df = pd.read_excel(xlsx_path, sheet_name=0, dtype=str)
    missing = {
        col
        for col in (AGENDA_ITEM_COLUMN, AGENDA_DESCRIPTION_COLUMN)
        if col not in df.columns
    }
    if missing:
        raise ValueError(
            f"Missing required column(s) in {xlsx_path}: {', '.join(sorted(missing))}"
        )
    return df[[AGENDA_ITEM_COLUMN, AGENDA_DESCRIPTION_COLUMN]]


def _normalize_cell(value: object) -> str:
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def build_agenda_description_pairs(df: pd.DataFrame) -> list[dict[str, str]]:
    """Build unique agenda-item/description pairs from a dataframe."""
    pairs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for _, row in df.iterrows():
        agenda_item = _normalize_cell(row.get(AGENDA_ITEM_COLUMN))
        description = _normalize_cell(row.get(AGENDA_DESCRIPTION_COLUMN))
        if not agenda_item or not description:
            continue
        key = (agenda_item, description)
        if key in seen:
            continue
        seen.add(key)
        pairs.append({"agenda_item": agenda_item, "description": description})

    pairs.sort(key=lambda p: _natural_sort_key(p["agenda_item"]))
    return pairs


def _normalize_agenda_item(value: object) -> str:
    agenda_item = _normalize_cell(value)
    if re.fullmatch(r"\d+(?:\.\d+)*\.", agenda_item):
        agenda_item = agenda_item.rstrip(".")
    return agenda_item


def build_agenda_description_pairs_from_csv(csv_path: Path) -> list[dict[str, str]]:
    """Build agenda-item descriptions from a two-column Agenda CSV."""
    pairs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 2:
                continue

            agenda_item = _normalize_agenda_item(row[0])
            description = _normalize_cell(row[1])
            if not agenda_item or not description:
                continue

            lower_item = agenda_item.lower().replace("_", " ")
            lower_description = description.lower().replace("_", " ")
            if (
                lower_item in {"agenda item", "item"}
                and lower_description in {"agenda item description", "description"}
            ):
                continue

            key = (agenda_item, description)
            if key in seen:
                continue
            seen.add(key)
            pairs.append({"agenda_item": agenda_item, "description": description})

    pairs.sort(key=lambda p: _natural_sort_key(p["agenda_item"]))
    return pairs


def _paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{{{WORD_NS}}}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{{{WORD_NS}}}tab":
            parts.append(" ")
        elif node.tag == f"{{{WORD_NS}}}br":
            parts.append(" ")
    return _normalize_text("".join(parts))


def _iter_docx_paragraphs(docx_path: Path) -> tuple[list[ParagraphInfo], StyleMap, NumberingMap]:
    with zipfile.ZipFile(docx_path) as docx:
        document_xml = docx.read("word/document.xml")
        styles = StyleMap(docx.read("word/styles.xml"))
        try:
            numbering_xml = docx.read("word/numbering.xml")
        except KeyError:
            numbering_xml = None

    root = ET.fromstring(document_xml)
    numbering = NumberingMap(numbering_xml)
    paragraphs: list[ParagraphInfo] = []
    for paragraph in root.findall(".//w:body/w:p", NS):
        text = _paragraph_text(paragraph)
        if not text:
            continue

        ppr = paragraph.find("w:pPr", NS)
        style_id = (
            _w_attr(ppr.find("w:pStyle", NS), "val")
            if ppr is not None
            else None
        )
        outline_level = (
            _w_int_attr(ppr.find("w:outlineLvl", NS), "val")
            if ppr is not None
            else None
        )
        num_pr = ppr.find("w:numPr", NS) if ppr is not None else None
        num_id = (
            _w_attr(num_pr.find("w:numId", NS), "val")
            if num_pr is not None
            else None
        )
        ilvl = (
            _w_int_attr(num_pr.find("w:ilvl", NS), "val")
            if num_pr is not None
            else None
        )
        if num_id is None and ilvl is None:
            num_id, ilvl = styles.get_style_num_pr(style_id)
        paragraphs.append(
            ParagraphInfo(
                text=text,
                style_id=style_id,
                outline_level=outline_level,
                num_id=num_id,
                ilvl=ilvl,
            )
        )
    return paragraphs, styles, numbering


def _extract_leading_agenda_marker(text: str) -> tuple[str, str] | None:
    match = re.match(r"^(\d+(?:\.\d+)*)(?:\.|\s)*(.*)$", text)
    if not match:
        return None

    description = _normalize_text(match.group(2))
    if not description:
        return None
    return match.group(1), description


def build_agenda_description_pairs_from_docx(docx_path: Path) -> list[dict[str, str]]:
    """Build agenda-item descriptions from Word agenda DOCX headings."""
    paragraphs, styles, numbering = _iter_docx_paragraphs(docx_path)
    pairs: list[dict[str, str]] = []
    seen_items: set[str] = set()

    for paragraph in paragraphs:
        outline_level = styles.get_outline_level(
            paragraph.style_id,
            paragraph.outline_level,
        )
        if outline_level is None:
            continue

        explicit = _extract_leading_agenda_marker(paragraph.text)
        if explicit is not None:
            agenda_item, description = explicit
            numbering.apply_explicit_marker(agenda_item)
        else:
            marker = numbering.get_heading_marker(
                num_id=paragraph.num_id,
                ilvl=paragraph.ilvl,
                outline_level=outline_level,
            )
            if marker is None:
                continue
            agenda_item = marker
            description = paragraph.text

        description = _normalize_text(description)
        if not agenda_item or not description or agenda_item in seen_items:
            continue
        seen_items.add(agenda_item)
        pairs.append({"agenda_item": agenda_item, "description": description})

    pairs.sort(key=lambda p: _natural_sort_key(p["agenda_item"]))
    return pairs


def save_agenda_description_json(
    pairs: list[dict[str, str]],
    output_path: Path = DEFAULT_JSON_PATH,
    *,
    source_file: str | None = None,
    source_url: str | None = None,
    source_type: str | None = None,
    source_uploaded_at: str | None = None,
    source_agenda_file: str | None = None,
) -> None:
    """Save agenda descriptions in both list and lookup-map form."""
    descriptions: dict[str, str] = {}
    conflicts: dict[str, list[str]] = {}

    for pair in pairs:
        agenda_item = pair["agenda_item"]
        description = pair["description"]
        if agenda_item in descriptions and descriptions[agenda_item] != description:
            conflicts.setdefault(agenda_item, [descriptions[agenda_item]])
            if description not in conflicts[agenda_item]:
                conflicts[agenda_item].append(description)
            continue
        descriptions[agenda_item] = description

    payload: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_type": source_type,
        "source_file": source_file,
        "source_url": source_url,
        "source_uploaded_at": source_uploaded_at,
        "source_agenda_file": source_agenda_file,
        "agenda_items": pairs,
        "descriptions": descriptions,
    }
    if conflicts:
        payload["conflicts"] = conflicts

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def find_local_latest_agenda_docx(
    download_dir: Path = DEFAULT_AGENDA_DOWNLOAD_DIR,
) -> Path | None:
    """Return the latest cached Agenda DOCX, if present.

    Selection is deterministic (filename version, then name) — mtime is
    not stable across CI checkouts.
    """
    agenda_files = _iter_local_files(download_dir, (".docx",))
    if not agenda_files:
        return None
    return max(agenda_files, key=_local_doc_preference)


def find_local_latest_agenda_file(
    download_dir: Path = DEFAULT_AGENDA_DOWNLOAD_DIR,
) -> Path | None:
    """Return the latest cached Agenda CSV or DOCX, if present.

    Selection is deterministic (filename version, then name) — mtime is
    not stable across CI checkouts.
    """
    agenda_files = _iter_local_files(download_dir, (".csv", ".docx"))
    if not agenda_files:
        return None
    return max(agenda_files, key=_local_doc_preference)


def update_agenda_description_json(
    listing_url: str = TDOC_LIST_URL,
    output_path: Path = DEFAULT_JSON_PATH,
    download_dir: Path = DEFAULT_DOWNLOAD_DIR,
    agenda_docx_path: Path | None = None,
    agenda_source_info: dict | None = None,
) -> Path:
    """Build agenda_item_description.json, preferring the Agenda CSV/DOCX."""
    if agenda_docx_path is None:
        agenda_docx_path = find_local_latest_agenda_file()

    if agenda_docx_path is not None:
        uploaded_at = agenda_source_info.get("uploaded_at") if agenda_source_info else None
        if isinstance(uploaded_at, datetime):
            uploaded_at = uploaded_at.isoformat()
        suffix = agenda_docx_path.suffix.lower()
        if suffix == ".csv":
            pairs = build_agenda_description_pairs_from_csv(agenda_docx_path)
            source_type = "agenda_csv"
        elif suffix == ".docx":
            pairs = build_agenda_description_pairs_from_docx(agenda_docx_path)
            source_type = "agenda_docx"
        else:
            raise ValueError(f"Unsupported agenda file type: {agenda_docx_path}")
        save_agenda_description_json(
            pairs,
            output_path,
            source_file=agenda_docx_path.name,
            source_url=agenda_source_info.get("url") if agenda_source_info else None,
            source_type=source_type,
            source_uploaded_at=uploaded_at,
            source_agenda_file=(
                agenda_source_info.get("name") if agenda_source_info else agenda_docx_path.name
            ),
        )
        return output_path

    xlsx = pick_tdoc_xlsx(find_tdoc_xlsx_files(listing_url))
    xlsx_path = download_tdoc_xlsx(xlsx, download_dir)
    df = load_agenda_description_dataframe(xlsx_path)
    pairs = build_agenda_description_pairs(df)
    save_agenda_description_json(
        pairs,
        output_path,
        source_file=xlsx.name,
        source_url=xlsx.url,
        source_type="tdoc_list_xlsx",
    )
    return output_path


def load_agenda_description_map(
    path: Path = DEFAULT_JSON_PATH,
) -> dict[str, str]:
    """Load agenda item descriptions from JSON."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    descriptions = data.get("descriptions")
    if isinstance(descriptions, dict):
        return {str(k): str(v) for k, v in descriptions.items() if v is not None}

    items = data.get("agenda_items", [])
    result: dict[str, str] = {}
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            agenda_item = item.get("agenda_item")
            description = item.get("description")
            if agenda_item and description:
                result[str(agenda_item)] = str(description)
    return result


def strip_derived_description_fields(sessions: list[dict]) -> list[dict]:
    """Remove generated description fields before LLM merge comparison."""
    stripped: list[dict] = []
    for session in sessions:
        stripped.append(
            {k: v for k, v in session.items() if k not in _DERIVED_SESSION_FIELDS}
        )
    return stripped


def _split_agenda_item_field(value: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"\s*(?:,|;|\bor\b)\s*", value)
        if part.strip()
    ]


def _expand_slash_shorthand(token: str) -> list[str]:
    if "/" not in token:
        return [token]

    head, tail = token.split("/", 1)
    tail = tail.strip()
    if not tail:
        return [head]

    if "." in tail:
        return [head, tail]

    prefix = head.rsplit(".", 1)[0] if "." in head else ""
    expanded_tail = f"{prefix}.{tail}" if prefix else tail
    return [head, expanded_tail]


def _extract_agenda_tokens_from_field(value: str | None) -> list[str]:
    if not value:
        return []
    tokens: list[str] = []
    for part in _split_agenda_item_field(value):
        for match in re.finditer(
            r"\b\d+(?:\.(?:\d+|[xX]))*(?:/\d+(?:\.\d+)*)?\b",
            part,
        ):
            tokens.extend(_expand_slash_shorthand(match.group(0)))
    return tokens


def _extract_agenda_tokens_from_name(value: str | None) -> list[str]:
    if not value:
        return []

    stripped = value.strip()
    leading = re.match(
        r"^(?:AI\s+)?\.?\s*(\d+(?:\.(?:\d+|[xX]))*(?:/\d+(?:\.\d+)*)?)\b",
        stripped,
        re.IGNORECASE,
    )
    if leading and ("." in leading.group(1) or stripped.upper().startswith("AI ")):
        return _expand_slash_shorthand(leading.group(1))

    tokens: list[str] = []
    for match in re.finditer(r"\b\d+(?:\.(?:\d+|[xX]))+(?:/\d+)?\b", stripped):
        tokens.extend(_expand_slash_shorthand(match.group(0)))
    return tokens


def _ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip().strip(".")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def extract_session_agenda_items(session: dict) -> list[str]:
    """Extract agenda item candidates from a merged session dict."""
    agenda_item = session.get("agenda_item")
    name = session.get("name")

    tokens = _extract_agenda_tokens_from_field(agenda_item)
    if not tokens:
        tokens = _extract_agenda_tokens_from_name(name)
    return _ordered_unique(tokens)


def _parent_candidates(agenda_item: str) -> list[str]:
    candidates = [agenda_item]
    if agenda_item.lower().endswith(".x"):
        candidates.append(agenda_item[:-2])

    base = candidates[-1]
    while "." in base:
        base = base.rsplit(".", 1)[0]
        candidates.append(base)
    return _ordered_unique(candidates)


def _description_hierarchy(
    agenda_item: str,
    descriptions: dict[str, str],
) -> dict[str, object] | None:
    matched = None
    for candidate in _parent_candidates(agenda_item):
        if candidate in descriptions:
            matched = candidate
            break

    if matched is None:
        return None

    hierarchy: list[dict[str, object]] = []
    parts = matched.split(".")
    prefixes = [".".join(parts[: i + 1]) for i in range(len(parts))]
    for prefix in prefixes:
        description = descriptions.get(prefix)
        hierarchy.append(
            {
                "agenda_item": prefix,
                "description": description,
                "has_description": description is not None,
            }
        )

    return {
        "agenda_item": agenda_item,
        "matched_agenda_item": matched,
        "description": descriptions[matched],
        "hierarchy": hierarchy,
    }


def annotate_sessions_with_agenda_descriptions(
    sessions: list[dict],
    descriptions: dict[str, str] | None = None,
) -> list[dict]:
    """Add generated agenda description fields to merged session dicts."""
    descriptions = descriptions if descriptions is not None else load_agenda_description_map()
    stripped = strip_derived_description_fields(sessions)
    if not descriptions:
        return stripped

    annotated: list[dict] = []
    for session in stripped:
        items = []
        for agenda_item in extract_session_agenda_items(session):
            item = _description_hierarchy(agenda_item, descriptions)
            if item:
                items.append(item)

        if items:
            session = dict(session)
            session["description"] = items[0]["description"]
            session["agenda_descriptions"] = items
        annotated.append(session)

    return annotated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build agenda descriptions JSON from a 3GPP RAN1 Agenda CSV/DOCX."
    )
    parser.add_argument("--listing-url", default=TDOC_LIST_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON_PATH)
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument(
        "--agenda-docx",
        "--agenda-file",
        type=Path,
        default=None,
        help=(
            "Agenda CSV or DOCX to parse. Defaults to the latest cached "
            "downloads/ran1/Agenda/*.csv or *.docx."
        ),
    )
    args = parser.parse_args()

    output = update_agenda_description_json(
        listing_url=args.listing_url,
        output_path=args.output,
        download_dir=args.download_dir,
        agenda_docx_path=args.agenda_docx,
    )
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
