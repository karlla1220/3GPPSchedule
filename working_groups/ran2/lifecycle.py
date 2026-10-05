"""RAN2's CI adapter; importing it loads no document parser."""
from pathlib import Path
from shared.lifecycle import CheckResult, clear_paths, restore_staged

input_paths = (Path('working_groups/ran2'),)
persistent_paths = (Path('docs/ran2'),)
cache_paths = (Path('.cache/ran2'),)


def check_updates():
    from .sources import (OUTPUT, TRANSFER, client, fetch_bundle, load_config, override_for,
                          portal_rows, read_json)
    cfg = load_config()
    previous = read_json(OUTPUT / '.schedule_state.json')
    try:
        with client() as http:
            bundle = fetch_bundle(cfg, previous, http, rows=portal_rows())
        bundle.save(TRANSFER)
        manifest = bundle.manifest
        reasons = [f'{key} changed' for key in ('meeting_id', 'version', 'config_hash')
                   if manifest[key] != previous.get(key)]
        for key in ('schedule', 'agenda'):
            new, old = manifest.get(key) or {}, previous.get(key) or {}
            if any(new.get(k) != old.get(k) for k in ('sha256', 'url')):
                reasons.append(f'{key} changed')
        portal = manifest.get('portal_metadata') or {}
        saved = previous.get('metadata', {})
        pinned = override_for(cfg, manifest['meeting_id'])
        if any(saved.get(k) != portal[k] for k in ('starts_on', 'ends_on', 'starts_at', 'ends_at', 'timezone',
                                                   'location') if k in portal and k not in pinned):
            reasons.append('meeting metadata changed')
        return CheckResult(changed=bool(reasons), reasons=reasons)
    except Exception as exc:
        return CheckResult(errors=[f'RAN2 source check failed: {exc}'])


def prepare_build():
    from .sources import DOWNLOADS, TRANSFER
    restore_staged(TRANSFER, DOWNLOADS / 'prepared', replace=True)


def reset_cache():
    from .sources import DOWNLOADS
    clear_paths((*cache_paths, DOWNLOADS))


def build_schedule(options=None):
    from .pipeline import build_schedule as build
    return build(options)
