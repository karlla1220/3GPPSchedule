"""Persistent per-slot state for incremental merge.

Each (day, time_block) gets a JSON file under ``docs/ran1/slot_state/`` recording
the hashes of every source that contributed to it, the merged session list
produced by the LLM, and the cells that merge was made from (the cell
ledger). On the next run the merger compares current source hashes against
these snapshots to decide which slots can short-circuit, and compares the
current cells against the ledger to find which cells an incremental LLM call
must apply. A slot without a ledger is rebuilt from scratch. Terms are in
GLOSSARY.md.

The directory lives under ``docs/ran1/`` rather than ``.cache/`` so files are
git-tracked and operators can force a single-slot rebuild by deleting one
file via the GitHub web UI.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .agenda_descriptions import strip_derived_description_fields


SLOT_STATE_DIR = Path("docs/ran1/slot_state")
# This version covers not only the JSON shape but also the parser/prompt
# semantics used to produce ``merged_sessions``.  Bump it whenever an old
# merge result is unsafe as an incremental baseline; a mismatch forces a cold
# rebuild instead of carrying poisoned semantic fields forward.
SCHEMA_VERSION = 3


@dataclass
class SlotState:
    """Persisted snapshot of one (day, time_block) merge."""

    day: str
    time_block_index: int
    source_hashes: dict[str, str] = field(default_factory=dict)
    merged_sessions: list[dict] = field(default_factory=list)
    merged_at: str = ""
    meeting_id: str = ""
    schema_version: int = SCHEMA_VERSION
    # Cell ledger: source label → the cells ``merged_sessions`` was made
    # from ({room_label, cell_text, fallback_start_time, cell_id}). None for
    # a state saved before ledgers existed.
    source_entries: dict[str, list[dict]] | None = None
    # MERGE_PROMPT_VERSION the merge was made under. An incremental merge
    # copies the baseline, so a ledger from another version is not used.
    merge_prompt_version: int | None = None
    # The time block the merge placed sessions in ("08:00", "10:30"). The
    # chair moves breaks between meetings and within one, and a merge made
    # for other block times has the wrong session times. "" for a state
    # saved before blocks were read from the document: RAN1#126bis blocks.
    block_start: str = ""
    block_end: str = ""

    def block(self) -> tuple[str, str]:
        if self.block_start and self.block_end:
            return self.block_start, self.block_end
        from .models import TIME_BLOCKS
        if 0 <= self.time_block_index < len(TIME_BLOCKS):
            default = TIME_BLOCKS[self.time_block_index]
            return default["start"], default["end"]
        return "", ""


def hash_source_text(text: str) -> str:
    """Hash a source's serialized text after whitespace normalization."""
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def slot_state_path(day: str, time_block_index: int) -> Path:
    return SLOT_STATE_DIR / f"{day}_{time_block_index:02d}.json"


def load_slot_state(
    day: str, time_block_index: int, meeting_id: str | None = None
) -> SlotState | None:
    """Load a persisted slot state, or None if missing/unreadable.

    If ``meeting_id`` is given and differs from the one recorded in the
    snapshot, the state is ignored (returns None): the schedule changed
    enough that previous merge results cannot be trusted for incremental
    use, and the slot must be rebuilt cold.
    """
    path = slot_state_path(day, time_block_index)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("schema_version") != SCHEMA_VERSION:
        return None
    if meeting_id is not None and data.get("meeting_id", "") != meeting_id:
        return None
    return SlotState(
        day=data.get("day", day),
        time_block_index=data.get("time_block_index", time_block_index),
        source_hashes=data.get("source_hashes", {}),
        merged_sessions=strip_derived_description_fields(
            data.get("merged_sessions", [])
        ),
        merged_at=data.get("merged_at", ""),
        meeting_id=data.get("meeting_id", ""),
        schema_version=data.get("schema_version", SCHEMA_VERSION),
        source_entries=data.get("source_entries"),
        merge_prompt_version=data.get("merge_prompt_version"),
        block_start=data.get("block_start", ""),
        block_end=data.get("block_end", ""),
    )


def save_slot_state(state: SlotState) -> None:
    """Persist a slot state to ``docs/ran1/slot_state/{day}_{tb:02d}.json``.

    A merge that reproduces the saved slot keeps its ``merged_at``: the file
    is committed, and a rebuild that changes nothing must not change it.
    """
    SLOT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = slot_state_path(state.day, state.time_block_index)
    data = json.loads(json.dumps(asdict(state)))
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = None
    if (
        isinstance(previous, dict)
        and previous.get("merged_at")
        and {**previous, "merged_at": None} == {**data, "merged_at": None}
    ):
        data["merged_at"] = previous["merged_at"]
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def clear_all_slot_states() -> int:
    """Delete every ``*.json`` file in ``docs/ran1/slot_state/``.

    Returns the number of files removed. The directory itself is preserved.
    """
    if not SLOT_STATE_DIR.exists():
        return 0
    count = 0
    for p in SLOT_STATE_DIR.glob("*.json"):
        try:
            p.unlink()
            count += 1
        except OSError:
            pass
    return count
