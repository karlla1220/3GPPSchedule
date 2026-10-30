import pytest

from shared import meeting_folders, remote_files


@pytest.fixture(autouse=True)
def fresh_listings(tmp_path, monkeypatch):
    """Each test sees its own mock server, not listings an earlier test fetched."""
    remote_files.new_run()
    # Nor meeting folders an earlier test (or a local build) found.
    monkeypatch.setattr(meeting_folders, 'CACHE_PATH', tmp_path / 'meeting-folders.json')
    # Never record found folders in the repository's config.json.
    monkeypatch.setattr(meeting_folders, 'CONFIG_PATHS',
                        {wg: tmp_path / 'absent' / wg / 'config.json' for wg in meeting_folders.CONFIG_PATHS})
    meeting_folders.new_run()
    # No test reads the mailing list or the repository's email records;
    # tests/test_email_updates.py turns the meeting back on with its own.
    from working_groups.ran1 import email_updates
    monkeypatch.setattr(email_updates, 'in_meeting', lambda *a, **k: False)
    monkeypatch.setattr(email_updates, 'RECORD_DIR', tmp_path / 'email' / 'docs')
    monkeypatch.setattr(email_updates, 'CACHE_DIR', tmp_path / 'email' / 'cache')
    # Venue timezones: never the repository's list, never a live LLM call.
    from shared import timezones
    monkeypatch.setattr(timezones, 'MAP_PATH', tmp_path / 'timezone_map.jsonl')
    monkeypatch.setattr(timezones, 'gemini_request', lambda *a, **k: None)
    # Office outlines come from python-docx in tests, whether or not docx-cli is installed.
    monkeypatch.setenv('DOCX_CLI', '')
    # Notices for the build's report go to the test's own folder.
    from shared import notices
    monkeypatch.setattr(notices, 'PATH', tmp_path / 'notices.json')
    # Agenda labels of cells without an AI: never a live LLM call, never the repository's lists.
    from shared import agenda_labels
    monkeypatch.setattr(agenda_labels, 'LABEL_MAP_ROOT', tmp_path / 'agenda_label_map')
    monkeypatch.setattr(agenda_labels, 'gemini_judge', lambda *a, **k: {})
    # RAN1 tests give their own folders; the lookup has its own tests.
    for module in ('check_update', 'pipeline'):
        monkeypatch.setattr(f'working_groups.ran1.{module}.with_meeting_folders', lambda cfg, *a, **k: cfg)
    yield
    remote_files.new_run()

