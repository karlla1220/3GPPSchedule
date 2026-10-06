"""Create a reproducible offline input preview; serve via HTTP for module/fetch support."""

from pathlib import Path
import argparse
import sys

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
from working_groups.ran1.agreements import parse_agreements


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("test_runs/agreements"))
    args = parser.parse_args()
    note = Path("tests/fixtures/ran1/Chair notes RAN1#124 - v09.docx")
    data = parse_agreements(note, "ran1#124")
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
        is_demo=True,
        chairman_agreements=data,
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
