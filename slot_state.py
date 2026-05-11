"""Persistent per-slot state for incremental merge.

Each (day, time_block) gets a JSON file under ``docs/slot_state/`` recording
the hashes of every source that contributed to it and the merged session
list produced by the LLM. On the next run the merger compares current
source hashes against these snapshots to decide which slots can short-
circuit, which need an incremental LLM call, and which must be rebuilt
from scratch.

The directory lives under ``docs/`` rather than ``.cache/`` so files are
git-tracked and operators can force a single-slot rebuild by deleting one
file via the GitHub web UI.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


SLOT_STATE_DIR = Path("docs/slot_state")
SCHEMA_VERSION = 1


@dataclass
class SlotState:
    """Persisted snapshot of one (day, time_block) merge."""

    day: str
    time_block_index: int
    source_hashes: dict[str, str] = field(default_factory=dict)
    merged_sessions: list[dict] = field(default_factory=list)
    merged_at: str = ""
    schema_version: int = SCHEMA_VERSION


def hash_source_text(text: str) -> str:
    """Hash a source's serialized text after whitespace normalization."""
    normalized = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _state_path(day: str, time_block_index: int) -> Path:
    return SLOT_STATE_DIR / f"{day}_{time_block_index:02d}.json"


def load_slot_state(day: str, time_block_index: int) -> SlotState | None:
    """Load a persisted slot state, or None if missing/unreadable."""
    path = _state_path(day, time_block_index)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("schema_version") != SCHEMA_VERSION:
        return None
    return SlotState(
        day=data.get("day", day),
        time_block_index=data.get("time_block_index", time_block_index),
        source_hashes=data.get("source_hashes", {}),
        merged_sessions=data.get("merged_sessions", []),
        merged_at=data.get("merged_at", ""),
        schema_version=data.get("schema_version", SCHEMA_VERSION),
    )


def save_slot_state(state: SlotState) -> None:
    """Persist a slot state to ``docs/slot_state/{day}_{tb:02d}.json``."""
    SLOT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _state_path(state.day, state.time_block_index)
    with open(path, "w") as f:
        json.dump(asdict(state), f, ensure_ascii=False, indent=2)


def clear_all_slot_states() -> int:
    """Delete every ``*.json`` file in ``docs/slot_state/``.

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
