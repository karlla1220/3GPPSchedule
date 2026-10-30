"""Build the SA1 schedule from the chair file (research/sa1/DECISIONS.md).

Rules read the table; Gemini is the fallback for the cells they are unsure
about, and for the whole table when the rules cannot read its layout (llm.py).
Without GEMINI_API_KEY the rules' reading is published as is, and a table they
cannot read fails the build so the last page stays.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

from shared import llm_cells, officials, timezones
from shared.agenda_labels import infer_for_schedule
from shared.renderer import generate_html
from .document import UnreadTable, extract_agenda, extract_document
from .llm import CellRefiner, read_document
from .sessions import make_schedule
from .sources import (CACHE, DOWNLOADS, Bundle, client, config_hash, fetch_bundle, load_config, local_bundle,
                      override_for, portal_metadata, portal_rows, read_json, source_label, write_json)


def meeting_metadata(meeting: str, cfg: dict, previous: dict, live: dict | None) -> dict:
    """Dates and venue zone: config override > live Portal > last successful build."""
    override = override_for(cfg, meeting)
    cached = previous.get('metadata', {}) if previous.get('meeting_id') == meeting else {}
    metadata = {**(live or cached), **override}
    if not metadata.get('timezone') and metadata.get('starts_on'):
        metadata['timezone'] = timezones.resolve(
            metadata.get('portal_timezone'), metadata.get('country'), metadata.get('location'),
            on=date.fromisoformat(metadata['starts_on']))
    missing = [k for k in ('starts_on', 'ends_on', 'timezone') if not metadata.get(k)]
    if missing:
        number = meeting.split('#', 1)[1]
        raise ValueError(f'Missing {", ".join(missing)} for SA1#{number}; Portal has no usable entry. '
                         f'Add them under "meetings": {{"{number}": ...}} in working_groups/sa1/config.json')
    zone = ZoneInfo(metadata['timezone'])
    for boundary in ('starts', 'ends'):
        key = f'{boundary}_at'
        if metadata.get(key):
            instant = datetime.fromisoformat(metadata[key])
            instant = instant.replace(tzinfo=zone) if instant.tzinfo is None else instant.astimezone(zone)
            if instant.date().isoformat() != metadata[f'{boundary}_on']:
                metadata.pop(key)  # an overridden date must not keep the old venue's instant
            else:
                metadata[key] = instant.isoformat()
    if metadata['ends_on'] < metadata['starts_on']:
        raise ValueError('Meeting end precedes its start')
    return metadata


def llm_request(cfg, request=None):
    """The Gemini call to fall back on, or None when it is off or has no key."""
    if not cfg['llm_fallback']:
        return None
    if request is None and not llm_cells.has_key():
        print('[sa1] GEMINI_API_KEY is not set; using the rule-based reading only')
        return None
    return request or llm_cells.gemini_request


def chair_resolver(cfg, starts_on: str, history=None):
    """NOTE names in the officials' spelling ("Qun Wei" -> "Qun WEI"); others as written.

    The officials page is only a spelling aid: when it cannot be read the
    names stay as the NOTE writes them.
    """
    try:
        history = history if history is not None else officials.fetch_history(cfg['officials_code'])
    except Exception as exc:
        print(f'[sa1] Officials unavailable ({exc}); chairs as the NOTE writes them')
        return None
    present = officials.in_office(history, date.fromisoformat(starts_on))
    return lambda name: officials.resolve(name, present)


def build_schedule(options=None, *, request=None, history=None):
    cfg = load_config()
    root = Path(getattr(options, 'output_dir', None) or 'docs') / 'sa1'
    state_path = root / '.schedule_state.json'
    previous = read_json(state_path)
    local = getattr(options, 'local', None)
    offline = bool(getattr(options, 'no_download', False) or local)
    prepared = DOWNLOADS / 'prepared'
    if local:
        bundle = local_bundle(Path(local), cfg)
    elif offline:
        bundle = Bundle.load(DOWNLOADS / 'inputs')
    elif (prepared / 'manifest.json').exists():
        try:
            bundle = Bundle.load(prepared)
            if bundle.manifest['config_hash'] != config_hash(cfg):
                raise ValueError('Prepared inputs do not match the current SA1 configuration')
        finally:
            shutil.rmtree(prepared, ignore_errors=True)
    else:
        with client() as http:
            bundle = fetch_bundle(cfg, previous, http, rows=portal_rows())
    bundle.save(DOWNLOADS / 'inputs')

    manifest = bundle.manifest
    schedule_doc, chair_doc = bundle.documents()
    unread = None
    try:
        document = extract_document(schedule_doc, manifest['schedule']['name'])
    except UnreadTable as exc:
        # A weekday table in a layout the rules do not know: the LLM reads it below.
        unread, document = exc, exc.partial
    meeting = manifest['meeting_id']
    if document['meeting_id'] not in (None, meeting):
        raise ValueError(f"Input bundle meeting identity mismatch: {document['meeting_id']} != {meeting}")
    document['meeting_id'] = meeting
    if chair_doc is not None:
        # A timetable-only file: the agenda is the chair file's.
        document['agenda'] = extract_agenda(chair_doc)
    live = manifest.get('portal_metadata')
    cached = previous.get('meeting_id') == meeting and previous.get('metadata')
    if live is None and (not offline or not (override_for(cfg, meeting) or cached)):
        live = portal_metadata(portal_rows() or [], meeting)
    metadata = meeting_metadata(meeting, cfg, previous, live)

    request = llm_request(cfg, request)
    if getattr(options, 'rebuild_slots', False):
        for path in CACHE.glob('*.json'):
            path.unlink()
    read = None
    if unread is not None:
        if request is None:
            raise unread
        document, read = read_document(schedule_doc, manifest['schedule']['name'], document, cfg['model'], request)
    refine = CellRefiner(document, cfg['model'], request) if request and read is None else None
    resolve = None if offline and history is None else chair_resolver(cfg, metadata['starts_on'], history)
    generated = datetime.now(ZoneInfo(metadata['timezone'])).strftime('%Y-%m-%d %H:%M')
    files = [manifest['schedule']] + ([manifest['chair']] if manifest.get('chair') else [])
    schedule = make_schedule(document, metadata, [f['name'] for f in files],
                             {f['name']: source_label(f['name']) for f in files}, generated,
                             refine=refine, resolve_chair=resolve, read=read)
    # Cells the schedule gives no AI: an LLM reads their label for the filter (shared/agenda_labels.py).
    infer_for_schedule(schedule, 'sa1', 'SA1', document['agenda'], enabled=not offline and request is not None)
    # No checkpoint before parsing and rendering both succeed.
    generate_html(schedule)
    state = {k: v for k, v in manifest.items() if k != 'portal_metadata'}
    summary = ({'model': cfg['model'], 'document': True} if read else refine.summary if refine else {'used': False})
    write_json(state_path, {**state, 'metadata': metadata, 'llm': summary})
    return schedule
