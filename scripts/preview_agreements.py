"""Create a reproducible offline input preview; serve via HTTP for module/fetch support."""

from pathlib import Path
import argparse
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build import render_site
from shared.schedule import (
    Schedule,
    DaySchedule,
    RoomInfo,
    Session,
    save_schedule,
    load_schedule,
)
from shared.agreement_assets import package_agreements
from working_groups.ran1.agreements import parse_agreements, track_changes

# A new agreement as a chair adds one: label, text, a TDoc row, more text.
NEW_AGREEMENT = (
    "Agreement",
    "Preview revision: this agreement was added in v10.",
    "R1-2601234",
    "FFS: details of the added agreement.",
)


def insert_agreement(document, paragraphs=NEW_AGREEMENT):
    """Insert a new agreement before the first existing one (AI 10.1)."""
    from copy import deepcopy
    from docx.text.paragraph import Paragraph

    label = next(p for p in document.paragraphs if p.text.strip() == "Agreement")
    for text in paragraphs:
        copy = deepcopy(label._p)
        label._p.addprevious(copy)
        Paragraph(copy, label._parent).text = text


def revised_note(note, directory):
    """The same note with a new agreement in AI 10.1, as a v10 would."""
    from docx import Document

    document = Document(note)
    insert_agreement(document)
    path = directory / note.name.replace("v09", "v10")
    document.save(path)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("test_runs/agreements"))
    args = parser.parse_args()
    note = Path("tests/fixtures/ran1/Chair notes RAN1#124 - v09.docx")
    # v09 is the baseline; v10 adds one paragraph to 10.1, so the panel shows
    # both an unchanged section and a dated change with a highlighted addition.
    baseline, _ = package_agreements(track_changes(
        parse_agreements(note, "ran1#124"), None,
        changed_at="2026-10-05T07:00:00+00:00", document=note.name,
    ))
    with tempfile.TemporaryDirectory() as scratch:
        v10 = revised_note(note, Path(scratch))
        data = track_changes(
            parse_agreements(v10, "ran1#124"), baseline,
            changed_at="2026-10-06T12:30:00+00:00", document=v10.name,
        )
    stored, fragments = package_agreements(data, baseline)
    samples = [
        ("Evaluation assumptions", "10.1"),
        ("Energy efficiency", "10.4"),
        ("Multiple agendas", "10.5.1.1, 10.5.2.2"),
        ("Section without Agreement label", "9.2.1"),
        ("Exact number missing", "10.10"),
        ("No agenda", None),
        ("Nested lists", "10.5.2.1"),
        ("TDoc entries only", "9.7.1"),
        ("Heading over subsections", "10.6.1"),
    ]
    days = []
    for day in ("Monday", "Tuesday"):
        sessions = [
            Session(
                name,
                60,
                f"{9 + i:02}:00",
                f"{10 + i:02}:00",
                day,
                agenda_item=ai,
                group_header="Agreement verification",
                room_ids=["main"],
            )
            for i, (name, ai) in enumerate(samples)
        ]
        days.append(
            DaySchedule(day, [RoomInfo("Verification room", id="main")], sessions)
        )
    schedule = Schedule(
        "RAN1#124 · Agreement verification",
        days,
        note.name,
        "2026-09-29",
        wg_id="ran1",
        meeting_id="ran1#124",
        timezone="Europe/Malta",
        is_demo=True,
        chairman_agreements=stored,
        agreement_fragments=fragments,
    )
    other = load_schedule(Path("docs/ran-plenary/schedule.json"))
    config = {
        "default_wg": "ran1",
        "working_groups": [{"id": "ran1"}, {"id": "ran-plenary"}],
        "presentation": {
            "notice": "Actual chairman note; demonstration session timings for UI verification."
        },
    }
    root = args.output_dir
    for s in [schedule, other]:
        save_schedule(s, root / s.wg_id / "schedule.json")
    render_site(config, root, {"ran1": schedule, "ran-plenary": other})
    # A separate real current schedule verifies that #124 notes cannot appear in #126.
    current = load_schedule(Path("docs/ran1/schedule.json"))
    from shared.renderer import save_html

    save_html(current, root / "current-ran1/index.html")
    print(f"Preview: python3 -m http.server 8874 --directory {root}")


if __name__ == "__main__":
    main()
