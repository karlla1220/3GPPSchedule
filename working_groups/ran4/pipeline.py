"""Build the RAN4 schedule from the chairs' meeting schedule PPTX.

Rules read the document; Gemini is the fallback for the cells they are unsure
about (see llm.py). Without GEMINI_API_KEY the rules' reading is published as
is. Topic numbers become agenda items through the meeting's topic list.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import re
import shutil
from zoneinfo import ZoneInfo

from shared import officials, timezones
from shared.agenda import agenda_map
from shared.agenda_labels import infer_for_schedule
from shared.llm_cells import gemini_request, has_key
from shared.renderer import generate_html
from .document import extract_document
from .llm import CellRefiner
from .sessions import make_schedule
from .sources import (CACHE, DOWNLOADS, Bundle, client, config_hash, fetch_bundle, load_config, local_bundle,
                      override_for, portal_metadata, portal_rows, read_json, write_json)
from .topics import read_topics


def meeting_metadata(meeting: str, cfg: dict, previous: dict, live: dict | None) -> dict:
    """Dates and venue zone: config override > live Portal > last successful build.

    A venue zone the Portal label does not give is resolved by shared.timezones
    (venue list, then the LLM once)."""
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
        raise ValueError(f'Missing {", ".join(missing)} for RAN4#{number}; Portal has no usable entry. '
                         f'Add them under "meetings": {{"{number}": ...}} in working_groups/ran4/config.json')
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
        print('[ran4] GEMINI_API_KEY is not set; using the rule-based reading only')
        return None
    return request or gemini_request


def column_chairs(document: dict, cfg: dict, metadata: dict, history=None) -> tuple[dict, list[str]]:
    """The chair each session column names ("RAN4 Main (Yang Session)"), as a full name.

    The schedule writes the given name only; the officials in office on the
    meeting's first day give the full one ("Yang" -> "Yang TANG"). A label the
    officials do not resolve is shown as written, with a warning.
    """
    labels = {room['id']: room['session'] for day in document['days'] for room in day['rooms'] if room['session']}
    if not labels:
        return {}, []
    warnings = []
    if history is None:
        try:
            history = officials.fetch_history(cfg['officials'])
        except Exception as exc:   # the page is still right with the names as written
            history = []
            warnings.append(f'RAN4 officials history unavailable ({exc}); chairs shown as written')
    serving = officials.in_office(history, date.fromisoformat(metadata['starts_on'])) if history else []
    chairs = {}
    for room, label in labels.items():
        name = officials.resolve(label, serving, cfg['chair_aliases']) if serving else None
        if name is None and serving:
            warnings.append(f'RAN4 column chair {label!r} is not an official in office; shown as written')
        chairs[room] = name or label
    return chairs, list(dict.fromkeys(warnings))


def source_labels(manifest: dict) -> tuple[list[str], dict]:
    """Header names: "Schedule v11", "Schedule THU afternoon", "Topics", "Agenda"."""
    schedule = manifest['schedule']['name']
    label = manifest['schedule'].get('label') or ''
    # shared/page.py adds a vNN found in the file name itself.
    names = [schedule]
    labels = {schedule: 'Schedule' if re.fullmatch(r'v\d+', label, re.I) or not label
              else f'Schedule {label.replace("_", " ")}'}
    for key, shown in (('topics', 'Topics'), ('agenda', 'Agenda')):
        info = manifest.get(key)
        if info:
            names.append(info['name'])
            labels[info['name']] = shown
    return names, labels


def build_schedule(options=None, *, request=None, history=None):
    cfg = load_config()
    root = Path(getattr(options, 'output_dir', None) or 'docs') / 'ran4'
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
                raise ValueError('Prepared inputs do not match the current RAN4 configuration')
        finally:
            shutil.rmtree(prepared, ignore_errors=True)
    else:
        with client() as http:
            bundle = fetch_bundle(cfg, previous, http, rows=portal_rows())
    bundle.save(DOWNLOADS / 'inputs')

    manifest = bundle.manifest
    document = extract_document(bundle.schedule, manifest['schedule']['name'])
    meeting = document['meeting_id']
    if meeting != manifest['meeting_id']:
        raise ValueError('Input bundle meeting identity mismatch')
    agenda = agenda_map(bundle.agenda) if bundle.agenda else None
    topics = read_topics(bundle.topics, agenda) if bundle.topics else None
    live = manifest.get('portal_metadata')
    cached = previous.get('meeting_id') == meeting and previous.get('metadata')
    if live is None and (not offline or not (override_for(cfg, meeting) or cached)):
        # Offline builds use saved dates; Portal is the last resort for a new meeting.
        live = portal_metadata(portal_rows() or [], meeting)
    metadata = meeting_metadata(meeting, cfg, previous, live)

    request = llm_request(cfg, request)
    if getattr(options, 'rebuild_slots', False):
        for path in CACHE.glob('*.json'):
            path.unlink()
    refine = CellRefiner(cfg['model'], request) if request else None
    chairs, warnings = column_chairs(document, cfg, metadata, history)
    generated = datetime.now(ZoneInfo(metadata['timezone'])).strftime('%Y-%m-%d %H:%M')
    names, labels = source_labels(manifest)
    schedule, read_warnings = make_schedule(document, agenda, topics, metadata, names, labels, generated,
                                            column_chairs=chairs, refine=refine)
    warnings = list(bundle.warnings) + warnings + read_warnings
    for warning in warnings:
        print(f'[ran4] {warning}')
    # Cells the schedule gives no AI: an LLM reads their label for the filter (shared/agenda_labels.py).
    infer_for_schedule(schedule, 'ran4', 'RAN4', agenda, enabled=not offline and request is not None)
    # No checkpoint before parsing and rendering both succeed.
    generate_html(schedule)
    state = {k: v for k, v in manifest.items() if k != 'portal_metadata'}
    write_json(state_path, {**state, 'metadata': metadata, 'warnings': warnings,
                            'llm': refine.summary if refine else {'used': False}})
    return schedule
