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

from downloader import get_remote_schedule_info, get_all_remote_schedule_info

STATE_FILE = Path("docs/.schedule_state.json")


def main() -> None:
    # 1. Fetch current remote state (lightweight — directory listing only)
    print("Checking FTP for schedule updates (all folders)…")
    try:
        remote_all = get_all_remote_schedule_info()
    except Exception as e:
        print(f"FTP check failed: {e}")
        # On failure, assume changed to be safe
        _set_output("changed", "true")
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

    changed = cached != remote_all
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
