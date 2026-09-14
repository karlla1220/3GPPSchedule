"""Shared check/build lifecycle for all enabled WGs; no WG-specific logic here."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from build import render_site, render_root, fetch_site_meetings
from shared.lifecycle import BuildOptions
from shared.renderer import generate_html
from shared.schedule import load_schedule, save_schedule
from shared.site_config import load_site_config
from working_groups.registry import get_working_group

STATE_PATH = Path('docs/.build_state.json')
PLAN_PATH = Path('.ci/plan.json')
COMMON_INPUTS = tuple(map(Path, ('shared/schedule.py', 'shared/lifecycle.py',
    'shared/portal_meetings.py', 'working_groups/registry.py', 'pyproject.toml', 'uv.lock')))
SITE_INPUTS = tuple(map(Path, ('shared/renderer.py', 'shared/navigation.py',
    'shared/site_config.py', 'shared/page.py', 'templates', 'build.py', 'ci.py')))


def read_json(path, default=None):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else ({} if default is None else default)
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def fingerprint(paths):
    """Content hashes work identically on check and fresh build runners."""
    digest = hashlib.sha256()
    for root in sorted(map(Path, paths)):
        files = sorted(root.rglob('*')) if root.is_dir() else [root]
        for path in files:
            if '__pycache__' in path.parts or path.suffix in {'.pyc', '.pyo'}:
                continue
            if path.is_file():
                digest.update(str(path).encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def wg_fingerprint(group):
    return fingerprint((*group.input_paths, *COMMON_INPUTS))


def site_fingerprint(config):
    return hashlib.sha256((fingerprint(SITE_INPUTS) + json.dumps(config, sort_keys=True)).encode()).hexdigest()


def check_site(config_path=Path('site.json')):
    config = load_site_config(config_path)
    from shared.portal_meetings import clear_meetings_cache
    clear_meetings_cache()
    meetings = fetch_site_meetings()
    previous = read_json(STATE_PATH)
    shutil.rmtree('.ci/transfers', ignore_errors=True)
    plan = {'schema_version': 1, 'site_config': str(config_path),
            'site_fingerprint': site_fingerprint(config), 'groups': {}, 'build_ids': [],
            'portal_meetings': meetings}
    for entry in config['working_groups']:
        wg = entry['id']
        reasons, errors = [], []
        input_hash = None
        changed = False
        try:
            group = get_working_group(wg)
            input_hash = wg_fingerprint(group)
            result = group.check_updates()
            changed = result.changed
            reasons.extend(result.reasons if changed else [])
            if changed and not reasons:
                reasons.append('source changed')
            errors.extend(result.errors)
            if previous.get('groups', {}).get(wg) != input_hash:
                reasons.append('WG code, configuration or dependencies changed')
            snapshot = Path('docs') / wg / 'schedule.json'
            try:
                if load_schedule(snapshot).wg_id != wg:
                    raise ValueError('wrong WG snapshot')
            except (OSError, ValueError, TypeError, KeyError):
                reasons.append('missing or invalid schedule snapshot')
        except Exception as exc:
            errors.append(str(exc))
        # Local source changes may still be buildable during a remote outage.
        if reasons and (not errors or changed):
            plan['build_ids'].append(wg)
        plan['groups'][wg] = {'status': 'error' if errors else 'changed' if reasons else 'unchanged',
                              'input_fingerprint': input_hash, 'reasons': reasons, 'errors': errors}
        print(f'[{wg}] {plan["groups"][wg]["status"]}: {reasons or errors or "no changes"}')
    missing_html = any(not (Path('docs') / g['id'] / 'index.html').exists() for g in config['working_groups'])
    plan['render_required'] = (previous.get('site') != plan['site_fingerprint'] or missing_html
                               or not Path('docs/index.html').exists())
    # Meeting date transitions can change navigation even without new documents.
    if not plan['render_required']:
        plan['render_required'] = navigation_changed(config, meetings)
    plan['build_required'] = bool(plan['build_ids'] or plan['render_required'])
    plan['has_errors'] = any(g['errors'] for g in plan['groups'].values())
    write_json(PLAN_PATH, plan)
    return plan


def navigation_changed(config, meetings=None):
    try:
        schedules = {g['id']: load_schedule(Path('docs') / g['id'] / 'schedule.json') for g in config['working_groups']}
        return (Path('docs/index.html').read_text(encoding='utf-8') != render_root(config, schedules, meetings) or
                any((Path('docs') / wg / 'index.html').read_text(encoding='utf-8') !=
                   generate_html(schedule, schedules=schedules, groups=config['working_groups'], presentation=config.get('presentation'))
                   for wg, schedule in schedules.items()))
    except (OSError, ValueError, TypeError, KeyError):
        return True


@contextmanager
def preserve_on_failure(paths):
    """Rollback committed WG state as well as HTML on failed builds."""
    with tempfile.TemporaryDirectory(prefix='wg-build-') as directory:
        saved = []
        for i, path in enumerate(paths):
            path = Path(path)
            backup = Path(directory) / str(i)
            exists = path.exists()
            if exists:
                if path.is_dir():
                    shutil.copytree(path, backup)
                else:
                    shutil.copy2(path, backup)
            saved.append((path, backup, exists))
        try:
            yield
        except BaseException:
            for path, backup, existed in saved:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
                if existed:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if backup.is_dir():
                        shutil.copytree(backup, path)
                    else:
                        shutil.copy2(backup, path)
            raise


def public_digest():
    # Internal parsing checkpoints are committed but do not require a Pages deploy.
    paths = [p for p in Path('docs').rglob('*') if p.is_file()
             and not any(part.startswith('.') for part in p.parts)
             and p.suffix != '.json']
    return fingerprint(paths)


def build_site(action='check-build-deploy', config_path=Path('site.json'), plan_path=PLAN_PATH):
    config = load_site_config(config_path)
    enabled = [g['id'] for g in config['working_groups']]
    state = read_json(STATE_PATH)
    state.setdefault('groups', {})
    plan = read_json(plan_path) if action == 'check-build-deploy' else None
    if plan is not None:
        if plan.get('schema_version') != 1 or plan.get('site_fingerprint') != site_fingerprint(config):
            raise ValueError('Missing or stale CI plan; run check again')
        if set(plan.get('groups', {})) != set(enabled):
            raise ValueError('CI plan does not match enabled WGs')
        targets = plan['build_ids']
        if not set(targets).issubset(enabled):
            raise ValueError('CI plan contains an inactive WG')
    else:
        targets = enabled
    before_public = public_digest()
    persistent = [Path('docs')]
    results = {}
    for wg in enabled:
        if wg not in targets:
            results[wg] = {'status': 'skipped', 'errors': []}
            continue
        try:
            group = get_working_group(wg)
            persistent.extend(group.persistent_paths)
            input_hash = wg_fingerprint(group)
            if plan is not None and plan['groups'][wg]['input_fingerprint'] != input_hash:
                raise ValueError('WG inputs changed after check; refusing stale plan')
            with preserve_on_failure(group.persistent_paths):
                if action == 'force-deploy':
                    group.reset_cache()
                else:
                    group.prepare_build()
                schedule = group.build_schedule(BuildOptions())
                if schedule.wg_id != wg or not schedule.days or not any(day.sessions for day in schedule.days):
                    raise ValueError('WG returned an invalid or empty schedule')
                generate_html(schedule)
                snapshot = Path('docs') / wg / 'schedule.json'
                # Updating remote metadata alone must not manufacture a page change.
                try:
                    previous = load_schedule(snapshot)
                except (OSError, ValueError, TypeError, KeyError):
                    previous = None
                if previous is not None:
                    old, new = asdict(previous), asdict(schedule)
                    old.pop('generated_at'); new.pop('generated_at')
                    if old == new:
                        schedule.generated_at = previous.generated_at
                save_schedule(schedule, snapshot)
            state['groups'][wg] = input_hash
            results[wg] = {'status': 'built', 'errors': []}
        except (Exception, SystemExit) as exc:
            results[wg] = {'status': 'failed', 'errors': [str(exc)]}
        print(f'[{wg}] {results[wg]["status"]}: {results[wg]["errors"]}')
    schedules = {}
    for wg in enabled:
        try:
            schedule = load_schedule(Path('docs') / wg / 'schedule.json')
            if schedule.wg_id != wg:
                raise ValueError('wrong WG snapshot')
            schedules[wg] = schedule
        except (OSError, ValueError, TypeError, KeyError):
            continue
    # Assemble only complete, successfully rendered HTML. A failed WG with a
    # previous snapshot stays visible, while other WGs can still advance.
    with preserve_on_failure([Path('docs')]):
        meetings = plan.get('portal_meetings') if plan is not None else fetch_site_meetings()
        render_site(config, Path('docs'), schedules, meetings=meetings)
    state['site'] = site_fingerprint(config)
    write_json(STATE_PATH, state)
    has_errors = any(v['errors'] for v in results.values()) or bool(plan and plan['has_errors'])
    report = {'groups': results, 'checks': plan['groups'] if plan else {},
              'site_changed': before_public != public_digest(),
              'has_errors': has_errors, 'persistent_paths': sorted({str(p) for p in persistent})}
    write_json(Path('.ci/build-report.json'), report)
    return report


def outputs(values):
    for key, value in values.items():
        encoded = json.dumps(value, separators=(',', ':'))
        print(f'{key}={encoded}')
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                stream.write(f'{key}={encoded}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'build', 'stage'])
    parser.add_argument('--action', choices=['check-build-deploy', 'build-deploy', 'force-deploy'], default='check-build-deploy')
    parser.add_argument('--site-config', type=Path, default=Path('site.json'))
    args = parser.parse_args()
    if args.command == 'check':
        result = check_site(args.site_config)
        outputs({key: result[key] for key in ['build_required', 'build_ids', 'has_errors']})
    elif args.command == 'build':
        result = build_site(args.action, args.site_config)
        outputs({key: result[key] for key in ['site_changed', 'has_errors']})
    else:
        import subprocess
        # Only paths owned by the build are staged; deletions are included.
        paths = read_json(Path('.ci/build-report.json'))['persistent_paths']
        for path in paths:
            if Path(path).exists() or subprocess.check_output(['git', 'ls-files', '--', path]).strip():
                subprocess.run(['git', 'add', '-A', '--', path], check=True)


if __name__ == '__main__':
    main()
