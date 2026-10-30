"""Build the RAN3 schedule from the chair's living agenda and the offline room sheet.

Rules read the time plan; Gemini is the fallback for the cells they flag
(see llm.py). Without GEMINI_API_KEY the rules' reading is published as is.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

from shared import officials, timezones
from shared.agenda import agenda_map
from shared.renderer import generate_html
from .document import extract_document, file_stem, offline_sheets, source_label
from .llm import CellRefiner, gemini_request, has_key
from .sessions import make_schedule
from .sources import (DOWNLOADS, Bundle, client, config_hash, fetch_bundle, load_config, local_bundle,
                      override_for, portal_metadata, portal_rows, source_names, write_json,
                      read_json)


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
        raise ValueError(f'Missing {", ".join(missing)} for RAN3#{number}; Portal has no usable entry. '
                         f'Add them under "meetings": {{"{number}": ...}} in working_groups/ran3/config.json')
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


def remembered_cbs(previous: dict, meeting: str, found: dict) -> dict:
    """CB blocks seen in this meeting's versions; the EOM version has none left."""
    seen = previous.get('cbs', {}) if previous.get('meeting_id') == meeting else {}
    return {**seen, **found}


def chair_resolver(cfg, starts_on: str, online: bool):
    """Initial -> full name from the officials in office at the meeting; aliases name the initials.

    The aliases alone give the names, so a failed officials lookup only loses
    the check that the person still held office.
    """
    serving = []
    if online:
        try:
            serving = officials.in_office(officials.fetch_history(cfg['officials_code']),
                                          date.fromisoformat(starts_on))
        except Exception as exc:
            print(f'[ran3] Officials unavailable ({exc}); using the configured initials')
    return lambda label: officials.resolve(label, serving, cfg['chair_aliases']) if label else None


def llm_request(cfg, request=None):
    """The Gemini call to fall back on, or None when it is off or has no key."""
    if not cfg['llm_fallback']:
        return None
    if request is None and not has_key():
        print('[ran3] GEMINI_API_KEY is not set; using the rule-based reading only')
        return None
    return request or gemini_request


def source_labels(manifest: dict) -> dict[str, str]:
    labels = {}
    if manifest.get('schedule'):
        labels[manifest['schedule']['name']] = source_label(manifest['schedule']['name'])
    if manifest.get('offline'):
        labels[manifest['offline']['name']] = file_stem(manifest['offline']['name'])
    if manifest.get('agenda'):
        labels[manifest['agenda']['name']] = 'agenda.csv'
    return labels


def build_schedule(options=None, *, request=None):
    cfg = load_config()
    root = Path(getattr(options, 'output_dir', None) or 'docs') / 'ran3'
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
                raise ValueError('Prepared inputs do not match the current RAN3 configuration')
        finally:
            shutil.rmtree(prepared, ignore_errors=True)
    else:
        with client() as http:
            bundle = fetch_bundle(cfg, previous, http, rows=portal_rows())
    bundle.save(DOWNLOADS / 'inputs')

    name = bundle.manifest['schedule']['name']
    document = extract_document(bundle.schedule, name)
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
    cbs = remembered_cbs(previous, meeting, document['cbs'])

    warnings = list(bundle.warnings)
    slots = []
    if bundle.offline:
        slots, unread = offline_sheets(bundle.offline)
        if unread:
            warnings.append(f'offline sheets not in the expected form: {", ".join(unread)}')
    request = llm_request(cfg, request)
    if getattr(options, 'rebuild_slots', False):
        for path in Path('.cache/ran3').glob('*.json'):
            path.unlink()
    refine = CellRefiner(cfg['model'], request, agenda=agenda, cbs=cbs) if request else None
    generated = datetime.now(ZoneInfo(metadata['timezone'])).strftime('%Y-%m-%d %H:%M')
    names = source_names(bundle.manifest)
    schedule = make_schedule(document, agenda, metadata, names, source_labels(bundle.manifest), generated,
                             slots=slots, cbs=cbs, chair=chair_resolver(cfg, metadata['starts_on'], not offline),
                             refine=refine, warnings=warnings)
    for warning in warnings:
        print(f'[ran3] {warning}')
    # No checkpoint before parsing and rendering both succeed.
    generate_html(schedule)
    state = {k: v for k, v in bundle.manifest.items() if k != 'portal_metadata'}
    write_json(state_path, {**state, 'metadata': metadata, 'cbs': cbs,
                            'llm': refine.summary if refine else {'used': False}, 'warnings': warnings})
    return schedule
