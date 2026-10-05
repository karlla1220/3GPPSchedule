"""Build the RAN2 schedule from the chair's session schedule.

Rules read the document; Gemini is the fallback for what they are unsure about
(see llm.py). Without GEMINI_API_KEY the rules' reading is published as is.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

from shared.portal_meetings import timezone_from_label
from shared.renderer import generate_html
from .document import LayoutError, agenda_map, extract_document
from .llm import CellRefiner, gemini_request, has_key, read_document
from .sessions import make_schedule
from .sources import (CACHE, DOWNLOADS, Bundle, client, config_hash, fetch_bundle, load_config, local_bundle,
                      override_for, portal_metadata, portal_rows, read_json, write_json)


def meeting_metadata(meeting: str, cfg: dict, previous: dict, live: dict | None) -> dict:
    """Dates and venue zone: config override > live Portal > last successful build."""
    override = override_for(cfg, meeting)
    cached = previous.get('metadata', {}) if previous.get('meeting_id') == meeting else {}
    metadata = {**(live or cached), **override}
    if not metadata.get('timezone'):
        metadata['timezone'] = timezone_from_label(metadata.get('portal_timezone'), metadata.get('country'))
    missing = [k for k in ('starts_on', 'ends_on', 'timezone') if not metadata.get(k)]
    if missing:
        number = meeting.split('#', 1)[1]
        raise ValueError(f'Missing {", ".join(missing)} for RAN2#{number}; Portal has no usable entry. '
                         f'Add them under "meetings": {{"{number}": ...}} in working_groups/ran2/config.json')
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
    if request is None and not has_key():
        print('[ran2] GEMINI_API_KEY is not set; using the rule-based reading only')
        return None
    return request or gemini_request


def build_schedule(options=None, *, request=None):
    cfg = load_config()
    root = Path(getattr(options, 'output_dir', None) or 'docs') / 'ran2'
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
                raise ValueError('Prepared inputs do not match the current RAN2 configuration')
        finally:
            shutil.rmtree(prepared, ignore_errors=True)
    else:
        with client() as http:
            bundle = fetch_bundle(cfg, previous, http, rows=portal_rows())
    bundle.save(DOWNLOADS / 'inputs')

    name = bundle.manifest['schedule']['name']
    request = llm_request(cfg, request)
    if getattr(options, 'rebuild_slots', False):
        for path in CACHE.glob('*.json'):
            path.unlink()
    prebuilt, refine = None, None
    try:
        document = extract_document(bundle.schedule, name)
        refine = CellRefiner(cfg['model'], request) if request else None
    except LayoutError:
        if request is None:
            raise
        document, prebuilt = read_document(bundle.schedule, name, cfg['model'], request)
    meeting = document['meeting_id']
    if meeting != bundle.manifest['meeting_id']:
        raise ValueError('Input bundle meeting identity mismatch')
    agenda = agenda_map(bundle.agenda) if bundle.agenda else None
    live = bundle.manifest.get('portal_metadata')
    cached = previous.get('meeting_id') == meeting and previous.get('metadata')
    if live is None and (not offline or not (override_for(cfg, meeting) or cached)):
        # Offline builds use saved dates; Portal is the last resort for a new meeting.
        live = portal_metadata(portal_rows() or [], meeting)
    metadata = meeting_metadata(meeting, cfg, previous, live)

    generated = datetime.now(ZoneInfo(metadata['timezone'])).strftime('%Y-%m-%d %H:%M')
    sources = [name] + (['agenda.csv'] if bundle.agenda else [])
    schedule = make_schedule(document, agenda, metadata, sources, generated, refine=refine, sessions=prebuilt)
    # No checkpoint before parsing and rendering both succeed.
    generate_html(schedule)
    state = {k: v for k, v in bundle.manifest.items() if k != 'portal_metadata'}
    llm = (refine.summary if refine else {'model': cfg['model'], 'document': True} if prebuilt
           else {'used': False})
    write_json(state_path, {**state, 'metadata': metadata, 'llm': llm})
    return schedule
