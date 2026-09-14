"""RAN1 cache persistence on a fresh CI runner."""
from working_groups.ran1 import session_parser


def test_result_cache_roundtrip_without_parent_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not session_parser.CACHE_DIR.parent.exists()
    result = [{"name": "6GR", "duration_minutes": 120}]

    session_parser._save_cache("first-result", result)

    assert session_parser._load_cache("first-result") == result
