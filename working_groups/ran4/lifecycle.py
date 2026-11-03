"""RAN4's CI adapter; importing it loads no document parser."""
from pathlib import Path
from shared.lifecycle import CheckResult, clear_paths, restore_staged

input_paths = (Path('working_groups/ran4'),)
# config.json: each run records the meeting folders it found (meeting_specific).
persistent_paths = (Path('docs/ran4'), Path('working_groups/ran4/config.json'))
cache_paths = (Path('.cache/ran4'),)


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
        for key in ('schedule', 'topics', 'agenda'):
            new, old = manifest.get(key) or {}, previous.get(key) or {}
            if any(new.get(k) != old.get(k) for k in ('sha256', 'url')):
                reasons.append(f'{key} changed')
        portal = manifest.get('portal_metadata') or {}
        saved = previous.get('metadata', {})
        pinned = override_for(cfg, manifest['meeting_id'])
        if any(saved.get(k) != portal[k] for k in ('starts_on', 'ends_on', 'starts_at', 'ends_at', 'timezone',
                                                   'location') if k in portal and k not in pinned):
            reasons.append('meeting metadata changed')
        # Reported as errors so CI shows them, while the page still builds.
        return CheckResult(changed=bool(reasons), reasons=reasons, errors=bundle.warnings)
    except Exception as exc:
        return CheckResult(errors=[f'RAN4 source check failed: {exc}'])


def prepare_build():
    from .sources import DOWNLOADS, TRANSFER
    restore_staged(TRANSFER, DOWNLOADS / 'prepared', replace=True)


def reset_cache():
    from .sources import DOWNLOADS
    clear_paths((*cache_paths, DOWNLOADS))


def build_schedule(options=None):
    from .pipeline import build_schedule as build
    return build(options)
