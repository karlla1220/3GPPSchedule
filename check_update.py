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

from downloader import get_remote_schedule_info

STATE_FILE = Path("docs/.schedule_state.json")


def main() -> None:
    # 1. Fetch current remote state (lightweight — directory listing only)
    print("Checking FTP for schedule updates…")
    try:
        remote = get_remote_schedule_info()
    except Exception as e:
        print(f"FTP check failed: {e}")
        # On failure, assume changed to be safe
        _set_output("changed", "true")
        sys.exit(0)

    if remote is None:
        print("No schedule file found on FTP.")
        _set_output("changed", "false")
        return

    print(f"Remote: {remote['name']}  uploaded_at={remote['uploaded_at']}")

    # 2. Compare with cached state (stored in repo as docs/.schedule_state.json)
    cached = _load_state()
    changed = cached != remote
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
