import pytest

from shared import meeting_folders, remote_files


@pytest.fixture(autouse=True)
def fresh_listings(tmp_path, monkeypatch):
    """Each test sees its own mock server, not listings an earlier test fetched."""
    remote_files.new_run()
    # Nor meeting folders an earlier test (or a local build) found.
    monkeypatch.setattr(meeting_folders, 'CACHE_PATH', tmp_path / 'meeting-folders.json')
    # RAN1 tests give their own folders; the lookup has its own tests.
    for module in ('check_update', 'pipeline'):
        monkeypatch.setattr(f'working_groups.ran1.{module}.with_meeting_folders', lambda cfg, *a, **k: cfg)
    yield
    remote_files.new_run()

