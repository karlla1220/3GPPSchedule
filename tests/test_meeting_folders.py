"""Meeting folders found from the meeting id, without hand-edited config."""
from datetime import datetime, timedelta, timezone
import json

import httpx
import pytest

from shared import meeting_folders, remote_files
from working_groups.ran1 import config as ran1_config

WG1 = 'https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/'
WG2 = 'https://www.3gpp.org/ftp/tsg_ran/WG2_RL2/'
NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_ftp(monkeypatch):
    """A real failure goes to FTP, which is unreachable here."""
    def fail(url, **kwargs):
        raise httpx.ConnectError(f'FTP fallback failed for {url}: timed out', request=httpx.Request('GET', url))
    monkeypatch.setattr(remote_files.ftp_transport, 'ftp_response', fail)


def server(folders, seen, status=403):
    """Listings for folders (url -> child names); status for anything else."""
    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url in folders:
            rows = ''.join(f'<tr><td><a href="{url}{name}">{name}</a></td></tr>' for name in folders[url])
            return httpx.Response(200, text=f'<table>{rows}</table>')
        return httpx.Response(status)
    return httpx.Client(transport=httpx.MockTransport(handler))


def row(title, start, end, url=None, tb=380):
    return {'Title': title, 'TBId': tb, 'Id': hash(title), 'StartDate': f'{start} 09:00:00',
            'EndDate': f'{end} 17:00:00', 'MtgDocURL': url}


def resolve(wg, meetings, folders, seen, **kwargs):
    remote_files.new_run()   # each call is a separate CI run
    meeting_folders.new_run()
    with server(folders, seen) as http:
        return meeting_folders.resolve(wg, meetings, http=http, **{'now': NOW, **kwargs})


def test_candidate_names_spell_bis_both_ways():
    assert meeting_folders.candidate_names('ran1', 'ran1#126bis') == ['TSGR1_126b', 'TSGR1_126bis']
    assert meeting_folders.candidate_names('ran2', 'ran2#135bis') == ['TSGR2_135bis', 'TSGR2_135b']
    assert meeting_folders.candidate_names('ran2', 'ran2#136') == ['TSGR2_136']
    assert meeting_folders.candidate_names('ran-plenary', 'ran#113') == ['TSGR_113']
    assert meeting_folders.folder_key('ran2', 'TSGR2_133b') == 'ran2#133bis'
    assert meeting_folders.folder_key('ran2', 'TSGR2_135bis') == 'ran2#135bis'
    # E-meeting folders still name the meeting.
    assert meeting_folders.folder_key('ran1', 'TSGR1_104b-e') == 'ran1#104bis'
    assert meeting_folders.folder_key('ran-plenary', 'TSGR_88e') == 'ran#88'
    assert meeting_folders.folder_key('ran2', 'Invitation') is None
    assert meeting_folders.folder_key('ran2', 'TSGR1_126') is None


def test_ran3_ran4_and_sa_folder_names():
    assert meeting_folders.candidate_names('ran3', 'ran3#133bis') == ['TSGR3_133-bis', 'TSGR3_133bis']
    assert meeting_folders.candidate_names('ran4', 'ran4#120bis') == ['TSGR4_120bis', 'TSGR4_120b']
    for wg, name, key in [('ran3', 'TSGR3_133-bis', 'ran3#133bis'), ('ran3', 'TSGR3_101bis', 'ran3#101bis'),
                          ('ran3', 'TSGR3_107_e', 'ran3#107'), ('ran3', 'TSGR3_114bis-e', 'ran3#114bis'),
                          ('ran4', 'TSGR4_101-bis-e', 'ran4#101bis'), ('ran4', 'TSGR4_94Bis', 'ran4#94bis'),
                          ('sa1', 'TSGS1_116_Calgary', 'sa1#116'), ('sa1', 'TSGS1_107bis_online', 'sa1#107bis'),
                          ('sa2', 'TSGS2_177_Prague_2026-10', 'sa2#177')]:
        assert meeting_folders.folder_key(wg, name) == key, name
    # Ad hoc folders are no regular meeting; RAN names do not run on.
    for wg, name in [('sa1', 'TSGS1_112-Ad_Hoc-e'), ('sa2', 'TSGS2_175-AH-e_Electronic_2026-06'),
                     ('sa2', 'TSGS2_154AHE_Electronic'), ('ran1', 'TSGR1_126_Prague')]:
        assert meeting_folders.folder_key(wg, name) is None, name
    # SA names carry the venue, so none can be built from the meeting id.
    assert meeting_folders.candidate_names('sa2', 'sa2#178') == []


def test_sa_folder_comes_from_portal_or_the_root_listing():
    sa2 = 'https://www.3gpp.org/ftp/tsg_sa/WG2_Arch/'
    rows = [row('3GPPSA1#116', '2026-11-16', '2026-11-20',
                'https://www.3gpp.org/ftp//tsg_sa/WG1_Serv/TSGS1_116_Calgary/', tb=384)]
    seen = []
    assert resolve('sa1', ['sa1#116'], {}, seen, rows=rows) == {
        'sa1#116': 'https://www.3gpp.org/ftp/tsg_sa/WG1_Serv/TSGS1_116_Calgary/'}
    assert seen == []
    listing = {sa2: ['TSGS2_175-AH-e_Electronic_2026-06/', 'TSGS2_178_Calgary_2026-11/']}
    assert resolve('sa2', ['sa2#178'], listing, seen) == {'sa2#178': sa2 + 'TSGS2_178_Calgary_2026-11/'}
    assert seen == [sa2]


def test_portal_mtgdocurl_needs_no_request():
    rows = [row('3GPPRAN1#126-bis', '2026-10-12', '2026-10-16',
                'https://ftp.3gpp.org/tsg_ran/WG1_RL1/TSGR1_126b/', tb=379),
            # RAN P points at the Docs/ subfolder.
            row('3GPPRAN#113', '2026-09-14', '2026-09-17',
                'https://ftp.3gpp.org/tsg_ran/TSG_RAN/TSGR_113/Docs/', tb=373),
            # A URL for another meeting is not taken.
            row('3GPPRAN1#127', '2026-11-16', '2026-11-20',
                'https://ftp.3gpp.org/tsg_ran/WG1_RL1/TSGR1_126b/', tb=379)]
    seen = []
    assert resolve('ran1', ['ran1#126bis'], {}, seen, rows=rows) == {'ran1#126bis': WG1 + 'TSGR1_126b/'}
    assert resolve('ran-plenary', ['ran#113'], {}, seen, rows=rows) == {
        'ran#113': 'https://www.3gpp.org/ftp/tsg_ran/TSG_RAN/TSGR_113/'}
    assert seen == []
    assert meeting_folders.portal_folders('ran1', rows) == {'ran1#126bis': WG1 + 'TSGR1_126b/'}


def test_found_folders_are_recorded_in_meeting_specific(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'sync_url': 'x', 'meeting_specific': [], 'meeting_specific_override': []}))
    monkeypatch.setitem(meeting_folders.CONFIG_PATHS, 'ran2', path)
    seen = []
    # RAN2 used both spellings; the second one exists here. RAN2#136 is not made yet.
    assert resolve('ran2', ['ran2#133bis', 'ran2#136'], {WG2 + 'TSGR2_133b/': ['Agenda/']}, seen) == {
        'ran2#133bis': WG2 + 'TSGR2_133b/'}
    assert seen[:2] == [WG2 + 'TSGR2_133bis/', WG2 + 'TSGR2_133b/']
    # The repository shows what was found; a meeting not listed was not found.
    recorded = json.loads(path.read_text())
    assert recorded == {'sync_url': 'x', 'meeting_specific': [WG2 + 'TSGR2_133b/'], 'meeting_specific_override': []}
    assert meeting_folders.updates('ran2') == [f'meeting folders now {WG2}TSGR2_133b/']
    # The next run reads the record instead of asking again, and changes nothing.
    seen.clear()
    assert resolve('ran2', ['ran2#133bis'], {}, seen, recorded=recorded['meeting_specific']) == {
        'ran2#133bis': WG2 + 'TSGR2_133b/'}
    assert seen == [] and meeting_folders.updates('ran2') == []
    # Once RAN2#136 is made, it is added.
    resolve('ran2', ['ran2#133bis', 'ran2#136'], {WG2 + 'TSGR2_136/': ['Invitation/']}, seen,
            recorded=recorded['meeting_specific'], now=NOW + meeting_folders.ROOT_EVERY)
    assert json.loads(path.read_text())['meeting_specific'] == [WG2 + 'TSGR2_133b/', WG2 + 'TSGR2_136/']


def test_the_root_is_listed_when_no_candidate_exists():
    seen = []
    folders = {WG2: ['TSGR2_111-e/', 'TSGR2_110/']}
    assert resolve('ran2', ['ran2#111'], folders, seen) == {'ran2#111': WG2 + 'TSGR2_111-e/'}
    assert seen == [WG2 + 'TSGR2_111/', WG2]


def test_an_absent_folder_is_retried_but_the_root_only_now_and_then():
    seen = []
    # Before the meeting: nothing yet. Not remembered as absent.
    assert resolve('ran2', ['ran2#136'], {WG2: ['TSGR2_135bis/']}, seen) == {}
    assert seen == [WG2 + 'TSGR2_136/', WG2]
    # The next run asks for the folder again, but does not list the large root.
    seen.clear()
    assert resolve('ran2', ['ran2#136'], {WG2: ['TSGR2_135bis/']}, seen,
                   now=NOW + timedelta(minutes=20)) == {}
    assert seen == [WG2 + 'TSGR2_136/']
    # Once the folder is made, the next run finds it.
    seen.clear()
    assert resolve('ran2', ['ran2#136'], {WG2 + 'TSGR2_136/': ['Invitation/']}, seen,
                   now=NOW + timedelta(minutes=40)) == {'ran2#136': WG2 + 'TSGR2_136/'}
    # The root is listed again after ROOT_EVERY.
    seen.clear()
    assert resolve('ran2', ['ran2#137'], {}, seen, now=NOW + meeting_folders.ROOT_EVERY) == {}
    assert seen == [WG2 + 'TSGR2_137/', WG2]


def test_override_wins_over_portal_and_cache():
    moved = 'https://www.3gpp.org/ftp/tsg_ran/WG2_RL2/Old/TSGR2_135bis/'
    rows = [row('3GPPRAN2#135-bis', '2026-10-12', '2026-10-16', 'https://ftp.3gpp.org/tsg_ran/WG2_RL2/TSGR2_135bis/')]
    seen = []
    recorded = [WG2 + 'TSGR2_135bis/']
    assert resolve('ran2', ['ran2#135bis'], {}, seen, rows=rows, override=[moved], recorded=recorded) == {
        'ran2#135bis': moved}
    assert seen == []


def test_a_real_failure_is_not_absence():
    remote_files.new_run()
    with server({}, [], status=500) as http, pytest.raises(httpx.HTTPError):
        meeting_folders.resolve('ran2', ['ran2#136'], http=http, now=NOW)


def test_ran1_lists_the_next_meeting_and_the_built_one(tmp_path, monkeypatch):
    monkeypatch.delenv('SCHEDULE_MEETING_SPECIFIC', raising=False)
    monkeypatch.delenv('SCHEDULE_MEETING_SPECIFIC_OVERRIDE', raising=False)
    monkeypatch.delenv('SCHEDULE_MEETING_SYNC', raising=False)
    path = tmp_path / 'config.json'
    path.write_text('{"meeting_specific": []}')
    cfg = ran1_config.load_config(path)
    rows = [row('3GPPRAN1#126', '2026-08-24', '2026-08-28', WG1 + 'TSGR1_126/', tb=379),
            row('3GPPRAN1#127', '2026-11-16', '2026-11-20', tb=379),
            row('3GPPRAN1#128', '2027-02-08', '2027-02-12', tb=379)]
    seen = []
    with server({WG1 + 'TSGR1_127/': ['Inbox/'], WG1 + 'TSGR1_126b/': ['Inbox/']}, seen) as http:
        found = ran1_config.with_meeting_folders(cfg, 'ran1#126bis', rows=rows, today='2026-10-20', http=http)
    # The next meeting (by name; Portal has no URL yet) and the built one; not RAN1#128.
    assert found['agenda_urls'] == [WG1 + 'TSGR1_127/Agenda/', WG1 + 'TSGR1_126b/Agenda/']
    assert found['inbox_urls'] == [ran1_config.DEFAULT_MEETING_SYNC + 'Inbox/',
                                   WG1 + 'TSGR1_127/Inbox/', WG1 + 'TSGR1_126b/Inbox/']
    assert WG1 not in seen
    assert found['meeting_specific'] == [WG1 + 'TSGR1_127/', WG1 + 'TSGR1_126b/']
    # An override wins for its meeting and is not recorded as found.
    path.write_text('{"meeting_specific_override": ["https://example.org/TSGR1_127/"]}')
    found = ran1_config.with_meeting_folders(ran1_config.load_config(path), None, rows=rows,
                                             today='2026-10-20', http=None)
    assert found['agenda_urls'] == ['https://example.org/TSGR1_127/Agenda/']
    # Offline: the recorded folder of the overridden meeting is not read either.
    path.write_text(json.dumps({'meeting_specific': [WG1 + 'TSGR1_127/', WG1 + 'TSGR1_126b/'],
                                'meeting_specific_override': ['https://example.org/TSGR1_127/']}))
    assert ran1_config.load_config(path)['agenda_urls'] == [
        'https://example.org/TSGR1_127/Agenda/', WG1 + 'TSGR1_126b/Agenda/']
    # Offline builds use the recorded folders as they are.
    path.write_text(json.dumps({'meeting_specific': [WG1 + 'TSGR1_126b/']}))
    assert ran1_config.load_config(path)['agenda_urls'] == [WG1 + 'TSGR1_126b/Agenda/']


def test_ran1_keeps_the_sync_folder_when_the_lookup_fails(tmp_path, monkeypatch):
    monkeypatch.delenv('SCHEDULE_MEETING_SPECIFIC', raising=False)
    monkeypatch.delenv('SCHEDULE_MEETING_SPECIFIC_OVERRIDE', raising=False)
    path = tmp_path / 'config.json'
    path.write_text('{}')
    cfg = ran1_config.load_config(path)
    remote_files.new_run()
    with server({}, [], status=500) as http:
        found = ran1_config.with_meeting_folders(cfg, 'ran1#126bis', rows=[], today='2026-10-20', http=http)
    assert found['inbox_urls'] == cfg['inbox_urls'] and found['agenda_urls'] == []


def test_an_overridden_meeting_is_still_looked_up_and_recorded(tmp_path, monkeypatch):
    """The override is used; what was found is recorded so it can be compared and dropped."""
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'meeting_specific': [], 'meeting_specific_override': []}))
    monkeypatch.setitem(meeting_folders.CONFIG_PATHS, 'ran2', path)
    moved = WG2 + 'Old/TSGR2_135bis/'
    rows = [row('3GPPRAN2#135-bis', '2026-10-12', '2026-10-16', 'https://ftp.3gpp.org/tsg_ran/WG2_RL2/TSGR2_135bis/')]
    seen = []
    assert resolve('ran2', ['ran2#135bis'], {}, seen, rows=rows, override=[moved]) == {'ran2#135bis': moved}
    assert json.loads(path.read_text())['meeting_specific'] == [WG2 + 'TSGR2_135bis/'] and seen == []
    # Without a Portal URL the folder is found by name, as for any meeting.
    path.write_text(json.dumps({'meeting_specific': []}))
    assert resolve('ran2', ['ran2#136'], {WG2 + 'TSGR2_136/': ['Agenda/']}, seen,
                   override=[WG2 + 'Old/TSGR2_136/']) == {'ran2#136': WG2 + 'Old/TSGR2_136/'}
    assert json.loads(path.read_text())['meeting_specific'] == [WG2 + 'TSGR2_136/']
    # Not found yet: the override is used and nothing is recorded.
    path.write_text(json.dumps({'meeting_specific': []}))
    assert resolve('ran2', ['ran2#137'], {}, seen, override=[WG2 + 'Old/TSGR2_137/']) == {
        'ran2#137': WG2 + 'Old/TSGR2_137/'}
    assert json.loads(path.read_text())['meeting_specific'] == []
