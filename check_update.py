"""Lightweight FTP change-detection script for GitHub Actions.

Checks if the latest schedule file on the 3GPP FTP has changed since
the last run.  Outputs `changed=true/false` to $GITHUB_OUTPUT.

State is persisted in docs/.schedule_state.json (committed to the repo
by the build-and-deploy job).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from downloader import get_all_remote_schedule_info

STATE_FILE = Path("docs/.schedule_state.json")


def _normalize_for_compare(entries: list[dict]) -> set[tuple]:
    """Convert list of dicts to a set of tuples for order-independent comparison.

    Only compares the (folder, name, uploaded_at) triple — ignoring list order
    so that different FTP listing orderings don't trigger false positives.
    """
    return {
        (e.get("folder", ""), e.get("name", ""), e.get("uploaded_at", ""))
        for e in entries
    }


def main() -> None:
    # 1. Fetch current remote state (lightweight — directory listing only)
    print("Checking FTP for schedule updates (all folders)…")
    try:
        remote_all = get_all_remote_schedule_info()
    except Exception as e:
        print(f"FTP check failed: {e}")
        # On failure, assume NOT changed — avoids unnecessary rebuilds
        # caused by transient FTP/network errors.
        _set_output("changed", "false")
        sys.exit(0)

    if not remote_all:
        print("No schedule files found on FTP.")
        _set_output("changed", "false")
        return

    for info in remote_all:
        folder = info.get("folder", "?")
        print(f"  Remote [{folder}]: {info['name']}  uploaded_at={info.get('uploaded_at')}")

    # 2. Compare with cached state (stored in repo as docs/.schedule_state.json)
    cached = _load_state()

    # Normalize for comparison: both should be lists of dicts
    # Handle migration from old single-dict format
    if isinstance(cached, dict) and "name" in cached:
        # Old format: single dict → convert to list for comparison
        cached = [cached]

    if cached is None:
        print("No cached state found — treating as changed.")
        changed = True
    else:
        # Order-independent, content-based comparison.
        cached_set = _normalize_for_compare(cached)
        remote_set = _normalize_for_compare(remote_all)

        if cached_set == remote_set:
            changed = False
        elif len(remote_all) < len(cached) and remote_set.issubset(cached_set):
            # Remote is a strict subset of cached — likely a transient FTP
            # failure where some folders didn't respond.  Treat as unchanged.
            print(
                f"Remote returned fewer entries ({len(remote_all)}) than "
                f"cached ({len(cached)}) and all remote entries exist in "
                f"cache — likely transient FTP failure, treating as unchanged."
            )
            changed = False
        else:
            changed = True
            # Log details about what changed
            added = remote_set - cached_set
            removed = cached_set - remote_set
            if added:
                print(f"  New/updated entries: {added}")
            if removed:
                print(f"  Removed entries: {removed}")

    print(f"Cached: {cached}")
    print(f"Changed: {changed}")

    # State is saved by the build-and-deploy job (committed to repo),
    # not here — so a failed build will retry on the next check.
    _set_output("changed", str(changed).lower())


def _load_state() -> dict | None:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))


def _set_output(name: str, value: str) -> None:
    """Write to $GITHUB_OUTPUT (or print for local testing)."""
    ghout = os.environ.get("GITHUB_OUTPUT")
    if ghout:
        with open(ghout, "a") as f:
            f.write(f"{name}={value}\n")
    print(f"::set-output {name}={value}")


if __name__ == "__main__":
    main()
