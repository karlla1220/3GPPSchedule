"""Fresh-runner lifecycle and deployment decisions without network or credentials."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import ci
from shared.lifecycle import CheckResult
from shared.schedule import load_schedule
from working_groups.ran_plenary.pipeline import build_schedule as demo


class FakeWG:
    def __init__(self, wg):
        self.wg = wg
        self.input_paths = (Path('inputs') / wg,)
        self.persistent_paths = (Path('docs') / wg, Path('downloads') / wg / 'source')
        self.cache_paths = (Path('.cache') / wg,)
        self.result = CheckResult()
        self.events = []
        self.fail = False
        self.input_paths[0].parent.mkdir(exist_ok=True)
        self.input_paths[0].write_text('v1')

    def check_updates(self):
        self.events.append('check')
        return self.result

    def prepare_build(self):
        self.events.append('prepare')

    def reset_cache(self):
        self.events.append('reset')

    def build_schedule(self, options):
        self.events.append('build')
        state = self.persistent_paths[0] / '.source-state.json'
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text('new-state')
        if self.fail:
            raise RuntimeError('parser failed after writing state')
        return replace(demo(), wg_id=self.wg, meeting_id=self.wg, generated_at=str(len(self.events)))


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    groups = {wg: FakeWG(wg) for wg in ['ran1', 'ran-plenary']}
    Path('site.json').write_text(json.dumps({'default_wg': 'ran1', 'working_groups': [
        {'id': 'ran1', 'enabled': True}, {'id': 'ran-plenary', 'enabled': True}]}))
    monkeypatch.setattr(ci, 'get_working_group', groups.__getitem__)
    return groups


def bootstrap():
    plan = ci.check_site()
    assert plan['build_ids'] == ['ran1', 'ran-plenary']
    result = ci.build_site()
    assert result['site_changed']
    return result


def test_first_build_then_unchanged_periodic_check_does_not_build(site):
    bootstrap()
    plan = ci.check_site()
    assert plan['build_ids'] == []
    assert not plan['build_required']
    assert not plan['has_errors']


def test_only_changed_wg_is_built_and_identical_schedule_does_not_deploy(site):
    bootstrap()
    prior = Path('docs/ran1/schedule.json').read_bytes()
    site['ran-plenary'].result = CheckResult(changed=True, reasons=['new remote version'])
    plan = ci.check_site()
    assert plan['build_ids'] == ['ran-plenary']
    before = len(site['ran1'].events)
    result = ci.build_site()
    assert len(site['ran1'].events) == before
    assert Path('docs/ran1/schedule.json').read_bytes() == prior
    assert not result['site_changed']  # generated_at alone is preserved


def test_disabled_wg_is_neither_imported_checked_nor_built(site):
    cfg = json.loads(Path('site.json').read_text())
    cfg['working_groups'][1]['enabled'] = False
    Path('site.json').write_text(json.dumps(cfg))
    plan = ci.check_site()
    assert plan['build_ids'] == ['ran1']
    ci.build_site()
    assert site['ran-plenary'].events == []
    assert 'ran-plenary' not in Path('docs/ran1/index.html').read_text()


def test_wg_code_change_rebuilds_only_its_owner(site):
    bootstrap()
    site['ran-plenary'].input_paths[0].write_text('v2')
    plan = ci.check_site()
    assert plan['build_ids'] == ['ran-plenary']


def test_default_change_only_renders_and_does_not_parse(site):
    bootstrap()
    cfg = json.loads(Path('site.json').read_text())
    cfg['default_wg'] = 'ran-plenary'
    Path('site.json').write_text(json.dumps(cfg))
    plan = ci.check_site()
    assert plan['build_ids'] == [] and plan['render_required']
    result = ci.build_site()
    assert result['site_changed']
    assert './ran-plenary/' in Path('docs/index.html').read_text()
    assert all(g.events.count('build') == 1 for g in site.values())


def test_failed_parser_rolls_back_all_persistent_state_and_retries(site):
    bootstrap()
    old = Path('docs/ran1/schedule.json').read_bytes()
    Path('docs/ran1/.source-state.json').write_text('old-state')
    previous_receipt = Path('docs/.build_state.json').read_bytes()
    site['ran1'].fail = True
    site['ran1'].input_paths[0].write_text('new-code')
    site['ran-plenary'].result = CheckResult(changed=True)
    ci.check_site()
    result = ci.build_site()
    assert result['has_errors']
    assert result['groups']['ran-plenary']['status'] == 'built'
    assert Path('docs/ran1/.source-state.json').read_text() == 'old-state'
    assert Path('docs/ran1/schedule.json').read_bytes() == old
    assert ci.check_site()['build_ids'] == ['ran1', 'ran-plenary']


def test_check_error_is_reported_and_other_wg_can_advance(site):
    bootstrap()
    site['ran1'].result = CheckResult(errors=['FTP unavailable'])
    site['ran-plenary'].result = CheckResult(changed=True)
    plan = ci.check_site()
    assert plan['has_errors'] and plan['groups']['ran1']['status'] == 'error'
    assert plan['build_ids'] == ['ran-plenary']
    result = ci.build_site()
    assert result['has_errors']
    assert result['groups']['ran-plenary']['status'] == 'built'


def test_force_build_uses_each_enabled_wgs_reset(site):
    result = ci.build_site('force-deploy')
    assert not result['has_errors']
    assert all(g.events == ['reset', 'build'] for g in site.values())


def test_stale_plan_is_rejected_and_does_not_advance_wg_state(site):
    bootstrap()
    site['ran1'].result = CheckResult(changed=True)
    ci.check_site()
    old = Path('docs/ran1/schedule.json').read_bytes()
    site['ran1'].input_paths[0].write_text('changed after check')
    result = ci.build_site()
    assert result['groups']['ran1']['status'] == 'failed'
    assert Path('docs/ran1/schedule.json').read_bytes() == old


def test_check_plan_survives_copy_to_fresh_runner(site, tmp_path, monkeypatch):
    import shutil
    ci.check_site()
    runner = tmp_path / 'fresh-runner'
    runner.mkdir()
    shutil.copytree('.ci', runner / '.ci')
    shutil.copytree('inputs', runner / 'inputs')
    shutil.copy2('site.json', runner / 'site.json')
    monkeypatch.chdir(runner)
    assert not ci.build_site()['has_errors']
    assert load_schedule(Path('docs/ran1/schedule.json')).wg_id == 'ran1'


def test_ran1_transfer_is_reused_on_fresh_runner_without_download(tmp_path, monkeypatch):
    from working_groups.ran1 import lifecycle, downloader
    monkeypatch.chdir(tmp_path)
    lifecycle.TRANSFER_DIR.mkdir(parents=True)
    name = 'RAN1#126 schedule.docx'
    url = 'https://example.org/schedule.docx'
    data = b'cached-document'
    from hashlib import sha256
    (lifecycle.TRANSFER_DIR / name).write_bytes(data)
    (lifecycle.TRANSFER_DIR / '.extra_files_state.json').write_text(json.dumps({
        'files': {url: {'filename': name, 'sha256': sha256(data).hexdigest()}}}))
    lifecycle.prepare_build()
    monkeypatch.setattr(downloader, '_download_external_file', lambda *a, **k: pytest.fail('downloaded twice'), raising=False)
    # The same cache used by the real RAN1 build now matches the staged bytes.
    monkeypatch.setattr(downloader.httpx, 'stream', lambda *a, **k: pytest.fail('downloaded twice'))
    results, _ = downloader.download_external_files([{'url': url, 'type': 'schedule'}])
    assert results[0][1].read_bytes() == data


def test_ran1_llm_failure_cannot_replace_last_successful_slot(tmp_path, monkeypatch):
    from working_groups.ran1 import session_parser, slot_state
    from working_groups.ran1.models import RoomInfo
    from working_groups.ran1.merger import TimeSlotData, SlotSource, SourceEntry
    from google import genai
    import time
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    monkeypatch.setattr(slot_state, 'SLOT_STATE_DIR', tmp_path)
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    client = MagicMock()
    client.models.generate_content.side_effect = RuntimeError('API unavailable')
    monkeypatch.setattr(genai, 'Client', lambda **kw: client)
    slot_state.save_slot_state(slot_state.SlotState(day='Monday', time_block_index=0,
        meeting_id='ran1#126', source_hashes={'Main Schedule': 'old'}, merged_sessions=[{'name': 'keep'}]))
    old = (tmp_path / 'Monday_00.json').read_bytes()
    room = RoomInfo(name='Main')
    slot = TimeSlotData(day='Monday', time_block_index=0, time_block_start='08:30',
        time_block_end='10:30', time_block_duration=120, main_rooms=[room],
        sources=[SlotSource('Main Schedule', [SourceEntry('Main', 'new schedule')])],
        source_freshness={'Main Schedule': 'FRESH'}, current_hashes={'Main Schedule': 'new'})
    with pytest.raises(RuntimeError, match='Could not parse'):
        session_parser.parse_time_slots([slot], {'Monday': [room]}, meeting_id='ran1#126')
    assert client.models.generate_content.call_count == 3
    assert (tmp_path / 'Monday_00.json').read_bytes() == old


def test_ran1_local_vice_chairs_cannot_mix_meetings(tmp_path):
    from working_groups.ran1.downloader import find_local_vice_chair_schedules
    folder = tmp_path / 'Hiroki_notes'
    folder.mkdir()
    (folder / 'RAN1#125 schedule v99.docx').write_bytes(b'old')
    current = folder / 'RAN1#126 schedule v01.docx'
    current.write_bytes(b'new')
    assert find_local_vice_chair_schedules(tmp_path, meeting_id='ran1#126') == {'Hiroki': current}
    assert find_local_vice_chair_schedules(tmp_path, meeting_id='ran1#127') == {}


def test_third_registered_wg_joins_lifecycle_without_workflow_changes(site, monkeypatch):
    from working_groups import registry
    monkeypatch.setitem(registry.PIPELINES, 'sa2', 'unused-test-module')
    site['sa2'] = FakeWG('sa2')
    cfg = json.loads(Path('site.json').read_text())
    cfg['working_groups'].append({'id': 'sa2', 'enabled': True})
    Path('site.json').write_text(json.dumps(cfg))
    assert ci.check_site()['build_ids'] == ['ran1', 'ran-plenary', 'sa2']
    result = ci.build_site()
    assert result['groups']['sa2']['status'] == 'built'
    assert '../sa2/' in Path('docs/ran1/index.html').read_text()


def test_corrupt_snapshot_is_recovered_by_selected_build(site):
    bootstrap()
    Path('docs/ran-plenary/schedule.json').write_text('{invalid')
    assert ci.check_site()['build_ids'] == ['ran-plenary']
    result = ci.build_site()
    assert not result['has_errors']
    assert load_schedule(Path('docs/ran-plenary/schedule.json')).wg_id == 'ran-plenary'


def test_downloaded_ran1_source_keeps_remote_provenance(tmp_path, monkeypatch):
    from working_groups.ran1.models import ScheduleSource
    from working_groups.ran1 import downloader
    source = ScheduleSource(folder_name='Chair_notes', person_name=None, is_main=True,
                            file_info={'name': 'RAN1#126 schedule.docx', 'uploaded_at': '2026-08-28'})
    document = tmp_path / source.file_info['name']
    document.write_bytes(b'document')
    monkeypatch.setattr(downloader, 'download_schedule_source', lambda *a: document)
    downloader.download_all_schedules([source])
    assert source.local_path == document
    assert source.origin == 'remote'
    state_path = tmp_path / 'state.json'
    downloader.save_schedule_state([s for s in [source] if s.origin == 'remote'], state_path)
    assert json.loads(state_path.read_text())['files'][0]['name'] == document.name


def test_ran1_partial_download_cannot_silently_drop_vice_chair(tmp_path, monkeypatch):
    from working_groups.ran1.models import ScheduleSource
    from working_groups.ran1 import downloader
    source = ScheduleSource(folder_name='Hiroki_notes', person_name='Hiroki', is_main=False,
                            file_info={'name': 'RAN1#126 Hiroki schedule.docx'})
    monkeypatch.setattr(downloader, 'download_schedule_source', lambda *a: None)
    with pytest.raises(RuntimeError, match='Could not download selected'):
        downloader.download_all_schedules([source])


def test_manual_ran1_source_is_still_local(tmp_path):
    from working_groups.ran1.models import ScheduleSource
    source = ScheduleSource('manual', None, True, {'name': 'schedule.docx'}, local_path=tmp_path / 'schedule.docx')
    assert source.origin == 'local'


def test_common_contact_change_renders_all_wgs_without_parsing(site):
    bootstrap()
    snapshots = {wg: (Path('docs') / wg / 'schedule.json').read_bytes() for wg in site}
    cfg = json.loads(Path('site.json').read_text())
    cfg['presentation'] = {'creator': 'Site author', 'contact_name': 'Support',
                           'contact_email': 'support@example.com'}
    Path('site.json').write_text(json.dumps(cfg))
    plan = ci.check_site()
    assert plan['build_ids'] == [] and plan['render_required']
    assert ci.build_site()['site_changed']
    for wg in site:
        assert site[wg].events.count('build') == 1
        assert (Path('docs') / wg / 'schedule.json').read_bytes() == snapshots[wg]
        html = (Path('docs') / wg / 'index.html').read_text()
        assert 'Created by Site author' in html
        assert 'mailto:support@example.com' in html
    assert not ci.check_site()['build_required']


@pytest.fixture(autouse=True)
def no_live_portal_lookup(monkeypatch):
    monkeypatch.setattr(ci, "fetch_site_meetings", lambda: None)


def test_portal_status_transition_renders_root_without_rebuilding_wgs(site, monkeypatch):
    from shared.schedule import save_schedule
    bootstrap()
    for wg, identity in [('ran1', 'ran1#126'), ('ran-plenary', 'ran#113')]:
        snapshot = Path('docs') / wg / 'schedule.json'
        save_schedule(replace(load_schedule(snapshot), meeting_id=identity, is_demo=False), snapshot)
    rows = [{'Id': 2, 'TBId': 373, 'Title': '3GPPRAN#113', 'MeetingPeriod': 'ONGOING'}]
    monkeypatch.setattr(ci, 'fetch_site_meetings', lambda: rows)
    plan = ci.check_site()
    assert plan['build_ids'] == [] and plan['render_required']
    assert ci.build_site()['site_changed']
    assert './ran-plenary/' in Path('docs/index.html').read_text()
    assert not ci.check_site()['build_required']
    rows[0]['MeetingPeriod'] = 'ENDED'
    assert ci.check_site()['render_required']
    assert ci.build_site()['site_changed']
    assert './ran1/' in Path('docs/index.html').read_text()
    assert all(group.events.count('build') == 1 for group in site.values())


def test_template_change_requires_render_without_rebuilding_wgs(site, monkeypatch):
    template = Path('templates/schedule.css')
    template.parent.mkdir()
    template.write_text('v1')
    monkeypatch.setattr(ci, 'SITE_INPUTS', (template.parent,))
    bootstrap()
    template.write_text('v2')
    plan = ci.check_site()
    assert plan['build_ids'] == [] and plan['render_required']


def test_force_reset_clears_ran1_generated_metadata(tmp_path, monkeypatch):
    from working_groups.ran1 import lifecycle, slot_state
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(slot_state, 'SLOT_STATE_DIR', Path('docs/ran1/slot_state'))
    paths = [Path('docs/ran1/.schedule_state.json'), Path('docs/ran1/agenda_item_description.json'),
             Path('docs/ran1/.extra_files_state.json'), Path('docs/ran1/slot_state/Monday_00.json'),
             Path('downloads/ran1/extra_files/document.docx'), Path('.cache/ran1/cached.json')]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('old')
    lifecycle.reset_cache()
    assert all(not path.exists() for path in paths)
