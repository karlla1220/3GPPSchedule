"""Plenary's small CI adapter; importing it requires no document parser or LLM."""
from pathlib import Path
import shutil

from shared.lifecycle import CheckResult

input_paths = (Path('working_groups/ran_plenary'),)
persistent_paths = (Path('docs/ran-plenary'),)
cache_paths = (Path('.cache/ran-plenary'),)


def check_updates():
    from .sources import (OUTPUT, TRANSFER, client, fetch_bundle, load_config,
                          portal_meeting, read_json)
    cfg = load_config()
    previous = read_json(OUTPUT / '.schedule_state.json')
    try:
        with client() as http:
            bundle = fetch_bundle(cfg, previous, http)
            number = bundle.manifest['meeting_number']
            override = cfg['meetings'].get(str(number), {})
            if not (all(override.get(k) for k in ('starts_on', 'ends_on')) and (override.get('timezone') or override.get('location'))):
                try:
                    bundle.manifest['portal_metadata'] = portal_meeting(http, number)
                except Exception:
                    if previous.get('meeting_number') != number or not previous.get('metadata'):
                        raise
            bundle.save(TRANSFER)
        reasons = []
        for key in ('meeting_number', 'version', 'config_hash'):
            if bundle.manifest[key] != previous.get(key):
                reasons.append(f'{key} changed')
        for key in ('timeplan', 'agenda'):
            if any(bundle.manifest[key].get(k) != previous.get(key, {}).get(k) for k in ('sha256', 'url')):
                reasons.append(f'{key} changed')
        portal = bundle.manifest.get('portal_metadata')
        if portal and any(v != previous.get('metadata', {}).get(k) for k, v in portal.items() if k not in override):
            reasons.append('meeting metadata changed')
        return CheckResult(changed=bool(reasons), reasons=reasons)
    except Exception as exc:
        return CheckResult(errors=[f'RAN P source check failed: {exc}'])


def prepare_build():
    from .sources import DOWNLOADS, TRANSFER
    prepared = DOWNLOADS / 'prepared'
    shutil.rmtree(prepared, ignore_errors=True)
    if (TRANSFER / 'manifest.json').exists():
        shutil.copytree(TRANSFER, prepared)


def reset_cache():
    from .sources import DOWNLOADS
    for path in (*cache_paths, DOWNLOADS):
        shutil.rmtree(path, ignore_errors=True)


def build_schedule(options=None):
    from .pipeline import build_schedule as build
    return build(options)
