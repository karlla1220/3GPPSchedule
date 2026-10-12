import json

from bs4 import BeautifulSoup

from shared.renderer import generate_html
from shared.schedule import DaySchedule, RoomInfo, Schedule, Session
from working_groups.ran1.agenda_labels import (
    apply_inferred_agenda_items,
    label_map_path,
    load_label_map,
    normalize_agenda_items,
)

AGENDA = {
    "8": "Maintenance on Rel-19 NR and E-UTRA",
    "8.1": "Maintenance on AI/ML for NR Air interface, NR MIMO Phase 5, NES and NTN for NR and IoT",
    "9": "Release 20 NR",
    "9.1": "Artificial Intelligence (AI)/Machine Learning (ML) for NR air interface enhancements",
    "9.1.1": "CSI spatial/frequency compression",
    "9.1.2": "Inter-vendor training collaboration for two-sided AI/ML models",
    "9.2": "NR MIMO Phase 6",
    "10": "Rel-20 Study of 6GR",
    "10.5": "Multi-antenna system",
    "10.5.2": "MIMO operation",
}


def _agenda_file(tmp_path, meeting="TSGR1_126b"):
    path = tmp_path / "agenda_item_description.json"
    path.write_text(json.dumps({
        "source_url": f"https://www.3gpp.org/ftp/tsg_ran/WG1_RL1/{meeting}/Agenda/agenda.csv",
        "descriptions": AGENDA,
    }))
    return path


def _session(name, agenda_item=None):
    return Session(name, 30, "09:00", "09:30", "Monday", agenda_item=agenda_item)


class FakeJudge:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, labels, agenda):
        self.calls.append(list(labels))
        return {
            label: {
                "label": label,
                "items": [{"agenda_item": a, "reason": "r"} for a in self.answers[label]],
                "note": "" if self.answers[label] else "names no agenda subject",
            }
            for label in labels if label in self.answers
        }


def _apply(tmp_path, sessions, judge, meeting="ran1#126bis", agenda=None):
    return apply_inferred_agenda_items(
        sessions, meeting,
        agenda_path=agenda or _agenda_file(tmp_path),
        directory=tmp_path / "map", judge=judge,
    )


def test_normalize_keeps_known_numbers_and_the_top_of_each_subtree():
    assert normalize_agenda_items(["9.1.2", "9.1", "8.1", "7.7", "10.5.2."], AGENDA) == ["8.1", "9.1", "10.5.2"]
    assert normalize_agenda_items([], AGENDA) == []


def test_unnumbered_cells_get_the_judged_items_and_every_label_is_recorded(tmp_path):
    judge = FakeJudge({"MIMO": ["9.2"], "AI/ML": ["9.1", "9.1.2"], "TBD": []})
    sessions = [_session("MIMO"), _session("AI/ML"), _session("TBD"), _session("AI 9.2 MIMO", "9.2")]
    _apply(tmp_path, sessions, judge)

    assert [s.inferred_agenda_items for s in sessions] == [["9.2"], ["9.1"], [], []]
    assert judge.calls == [["MIMO", "AI/ML", "TBD"]]
    records = [json.loads(line) for line in label_map_path("ran1#126bis", tmp_path / "map").read_text().splitlines()]
    assert [(r["label"], r["agenda_items"]) for r in records] == [("MIMO", ["9.2"]), ("AI/ML", ["9.1"]), ("TBD", [])]
    assert [r["agenda_item"] for r in records[1]["raw"]] == ["9.1", "9.1.2"]
    assert records[2]["note"] == "names no agenda subject"


def test_a_recorded_label_is_not_asked_again_and_a_new_one_is_appended(tmp_path):
    _apply(tmp_path, [_session("MIMO")], FakeJudge({"MIMO": ["9.2"]}))
    judge = FakeJudge({"MIMO": ["10.5.2"], "NTN": []})
    sessions = [_session("MIMO"), _session("NTN")]
    _apply(tmp_path, sessions, judge)

    assert judge.calls == [["NTN"]]
    assert sessions[0].inferred_agenda_items == ["9.2"]
    assert load_label_map(label_map_path("ran1#126bis", tmp_path / "map")) == {"MIMO": ["9.2"], "NTN": []}


def test_each_meeting_has_its_own_list(tmp_path):
    _apply(tmp_path, [_session("MIMO")], FakeJudge({"MIMO": ["9.2"]}))
    judge = FakeJudge({"MIMO": ["9.2"]})
    _apply(tmp_path, [_session("MIMO")], judge, meeting="ran1#127",
           agenda=_agenda_file(tmp_path, "TSGR1_127"))
    assert judge.calls == [["MIMO"]]
    assert label_map_path("ran1#127", tmp_path / "map").exists()


def test_nothing_is_inferred_without_this_meetings_agenda(tmp_path):
    judge = FakeJudge({"MIMO": ["9.2"]})
    sessions = [_session("MIMO")]
    _apply(tmp_path, sessions, judge, agenda=_agenda_file(tmp_path, "TSGR1_126"))
    _apply(tmp_path, sessions, judge, agenda=tmp_path / "missing.json")
    assert judge.calls == []
    assert sessions[0].inferred_agenda_items == []
    assert not (tmp_path / "map").exists()


def test_a_failed_or_partial_answer_records_nothing_for_the_missing_labels(tmp_path):
    def failing(labels, agenda):
        raise RuntimeError("504")

    sessions = [_session("MIMO")]
    _apply(tmp_path, sessions, failing)
    assert sessions[0].inferred_agenda_items == []
    assert not label_map_path("ran1#126bis", tmp_path / "map").exists()

    judge = FakeJudge({"MIMO": ["9.2"]})
    _apply(tmp_path, [_session("MIMO"), _session("AI/ML")], judge)
    assert load_label_map(label_map_path("ran1#126bis", tmp_path / "map")) == {"MIMO": ["9.2"]}
    judge = FakeJudge({"AI/ML": ["9.1"]})
    _apply(tmp_path, [_session("MIMO"), _session("AI/ML")], judge)
    assert judge.calls == [["AI/ML"]]


def _block(session, agreements=None):
    schedule = Schedule(
        "Test", [DaySchedule("Monday", [RoomInfo("Main")], [session])],
        "schedule.docx", "2026-10-12", wg_id="ran1", meeting_id="ran1#126bis",
        chairman_agreements=agreements or {},
    )
    return BeautifulSoup(generate_html(schedule), "html.parser").select_one(".session-block")


def test_inferred_items_feed_the_filter_scope_and_agreements_only():
    session = _session("MIMO")
    session.inferred_agenda_items = ["8.1", "9.2"]
    agreements = {"status": "ok", "meeting_id": "ran1#126bis",
                  "sections": {"9.2": {"html": "<p>x</p>"}, "9.2.1": {"html": "<p>y</p>"}}}
    block = _block(session, agreements)
    assert block["data-ai"] == ""
    assert block["data-ai-scope"] == "8.1.*|9.2.*"
    assert block["data-agreement-ai"] == "8.1|9.2|9.2.1"
    assert block["aria-label"] == "MIMO"
    assert "AI 9.2" not in block.get_text()
    assert "9.2" not in block["data-popup"]


def test_a_written_agenda_item_wins_over_an_inferred_one():
    session = _session("MIMO", "10.5.2")
    session.inferred_agenda_items = ["9.2"]
    assert _block(session)["data-ai-scope"] == "10.5.2.*"
