"""Build a real RAN Plenary schedule from timeplan + agenda, independently of RAN1."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

import httpx

from shared.renderer import generate_html
from .document import agenda_map, extract_document
from .interpreter import gemini_request, interpret, make_schedule, resolve_timezone
from .sources import (CACHE, DOWNLOADS, Bundle, client, config_hash, fetch_bundle,
                      load_config, local_bundle, portal_meeting, read_json, write_json)


def meeting_metadata(number, cfg, previous, http, request, supplied=None):
    override = cfg['meetings'].get(str(number), {})
    allowed = {'starts_on', 'ends_on', 'starts_at', 'ends_at', 'location', 'country', 'timezone'}
    if not isinstance(override, dict) or set(override) - allowed:
        raise ValueError(f'Invalid meeting metadata override for RAN#{number}')
    cached = previous.get('metadata', {}) if previous.get('meeting_number') == number else {}
    if all(override.get(k) for k in ('starts_on', 'ends_on')) and (override.get('timezone') or override.get('location')):
        metadata = {**override}
    else:
        live = supplied
        if live is None and http is not None:
            try:
                live = portal_meeting(http, number)
            except (ValueError, OSError, httpx.HTTPError) as exc:
                if not cached:
                    raise ValueError('Cannot resolve meeting metadata; configure meetings or retry Portal') from exc
                print(f'[ran-plenary] Portal unavailable; using RAN#{number} metadata cache')
        metadata = {**(live or cached), **override}
        if 'timezone' not in override and any(metadata.get(k) != cached.get(k)
                                              for k in ('location', 'country', 'portal_timezone')):
            metadata.pop('timezone', None)
        # Reuse the resolved zone only when the venue has not changed.
        if 'timezone' not in metadata and cached.get('timezone') and all(
                metadata.get(k) == cached.get(k) for k in ('location', 'country', 'portal_timezone')):
            metadata['timezone'] = cached['timezone']
    for key in ('starts_on', 'ends_on'):
        if not metadata.get(key):
            raise ValueError(f'Missing {key} for RAN#{number}')
        date.fromisoformat(metadata[key])
    metadata['timezone'] = resolve_timezone(metadata, cfg['model'], request)
    zone = ZoneInfo(metadata['timezone'])
    for boundary in ('starts', 'ends'):
        key = f'{boundary}_at'
        if metadata.get(key):
            instant = datetime.fromisoformat(metadata[key])
            instant = instant.replace(tzinfo=zone) if instant.tzinfo is None else instant.astimezone(zone)
            if instant.date().isoformat() != metadata[f'{boundary}_on']:
                # A date override must not retain an instant from the old venue dates.
                metadata.pop(key)
            else:
                metadata[key] = instant.isoformat()
    if metadata.get('starts_at') and metadata.get('ends_at'):
        if datetime.fromisoformat(metadata['ends_at']) < datetime.fromisoformat(metadata['starts_at']):
            raise ValueError('Meeting end precedes its start')
    return metadata


def build_schedule(options=None, *, request=gemini_request):
    cfg = load_config()
    root = Path(getattr(options, 'output_dir', None) or 'docs') / 'ran-plenary'
    state_path = root / '.schedule_state.json'
    previous = read_json(state_path)
    offline = bool(getattr(options, 'no_download', False) or getattr(options, 'local', None))
    prepared = DOWNLOADS / 'prepared'
    with client() as http:
        if getattr(options, 'local', None):
            bundle = local_bundle(Path(options.local), cfg, previous)
        elif offline:
            bundle = Bundle.load(DOWNLOADS / 'inputs')
        elif (prepared / 'manifest.json').exists():
            try:
                bundle = Bundle.load(prepared)
                if bundle.manifest['config_hash'] != config_hash(cfg):
                    raise ValueError('Prepared inputs do not match current RAN P configuration')
            finally:
                shutil.rmtree(prepared, ignore_errors=True)
        else:
            bundle = fetch_bundle(cfg, previous, http)
        bundle.save(DOWNLOADS / 'inputs')
        document = extract_document(bundle.timeplan, bundle.manifest['timeplan']['name'])
        if document['meeting_number'] != bundle.manifest['meeting_number']:
            raise ValueError('Input bundle meeting identity mismatch')
        descriptions = agenda_map(bundle.agenda)
        metadata = meeting_metadata(document['meeting_number'], cfg, previous,
                                    None if offline else http, request,
                                    supplied=bundle.manifest.get('portal_metadata'))
        if getattr(options, 'rebuild_slots', False):
            for path in CACHE.glob('parse-*.json'):
                path.unlink()
        result = interpret(document, cfg['model'], request)
        generated = datetime.now(ZoneInfo(metadata['timezone'])).strftime('%Y-%m-%d %H:%M')
        schedule = make_schedule(document, result, descriptions, metadata,
                                 bundle.manifest['timeplan']['name'], generated)
        # No successful checkpoint before interpretation and rendering both pass.
        generate_html(schedule)
        write_json(state_path, {**bundle.manifest, 'metadata': metadata})
        return schedule
