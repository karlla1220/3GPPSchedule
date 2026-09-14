"""Fixed demonstration schedule. No parser, network access or LLM dependency."""
from shared.schedule import DaySchedule, RoomInfo, Schedule, Session, Timeline, time_to_minutes


def build_schedule(options=None) -> Schedule:
    rooms = [RoomInfo(name="Plenary hall", id="plenary"), RoomInfo(name="Discussion room", id="discussion")]
    days = []
    entries = {
        "Monday": [
            ("09:00", "09:45", "Opening and agenda approval", "plenary", "Plenary"),
            ("09:45", "10:30", "Working group reports", "plenary", "Reports"),
            ("10:45", "12:00", "RAN1 and RAN2 reports", "plenary", "Reports"),
            ("13:00", "14:30", "Release planning", "plenary", "Planning"),
            ("13:00", "14:00", "Rapporteur discussion", "discussion", "Discussion"),
            ("14:45", "16:00", "Study and work item proposals", "plenary", "Planning"),
            ("16:00", "17:00", "Decisions and next steps", "plenary", "Plenary"),
        ],
        "Tuesday": [
            ("09:30", "10:30", "RAN3 and RAN4 reports", "plenary", "Reports"),
            ("10:45", "12:00", "Liaison statements", "plenary", "Plenary"),
            ("13:00", "14:15", "Work plan review", "plenary", "Planning"),
            ("14:15", "15:00", "Conclusions and closure", "plenary", "Plenary"),
        ],
    }
    for index, (day, rows) in enumerate(entries.items()):
        timeline = Timeline(
            start="09:00" if index == 0 else "09:30",
            end="17:00" if index == 0 else "15:00",
            slot_minutes=15, label_minutes=60 if index == 0 else 30,
            breaks=[
                {"name": "Coffee break", "start": "10:30", "end": "10:45"},
                {"name": "Lunch", "start": "12:00", "end": "13:00"},
            ],
        )
        sessions = [Session(
            name=name, duration_minutes=time_to_minutes(end)-time_to_minutes(start),
            start_time=start, end_time=end, day=day, room_ids=[room], group_header=group,
        ) for start, end, name, room, group in rows]
        days.append(DaySchedule(day_name=day, rooms=rooms if index == 0 else rooms[:1],
                                sessions=sessions, timeline=timeline))
    return Schedule(
        wg_id="ran-plenary", meeting_id="ran-plenary-demo", meeting_name="RAN Plenary",
        days=days, source_file="Fixed demonstration schedule", generated_at="2026-09-15 00:00",
        contact_name="", contact_email="", timezone="UTC", is_demo=True,
    )
