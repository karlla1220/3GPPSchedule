"""Lightweight FTP change-detection script for GitHub Actions.

Checks if the selected schedule files on the 3GPP FTP (or the
manually-provided chairman documents in ``ref_in_manual/``) have changed
since the last run.  Outputs `changed=true/false` to $GITHUB_OUTPUT.
When an external-file cache miss is downloaded, also outputs
`extra_files_artifact=true` and stages the file for the build job.

State is persisted in docs/.schedule_state.json (committed to the repo
by the build-and-deploy job). The cached ``meeting_id`` is fed back into
remote selection as a stability hint: older meetings do not displace the
current state, but a later regular meeting (e.g., ``124bis`` → ``125``)
does advance automatically. When ``ref_in_manual/`` contains a regular
meeting, that local meeting is authoritative and remote selection is locked
to it so check and build cannot disagree. Irregular meetings continue to
rely on upload timestamps.
"""

from __future__ import annotations

import os
import shutil
import sys

from config import load_config
from downloader import (
    check_external_files,
    EXTRA_FILES_TRANSFER_DIR,
    get_all_remote_schedule_info,
    get_latest_agenda_info,
    get_latest_chair_notes_info,
    _extract_version_parts_from_name,
    local_reference_hashes,
    local_reference_meeting_id,
    load_external_files_state,
    load_schedule_state,
    save_external_files_state,
)


def _timezone_reference(ref_type: str, info: dict | None) -> dict | None:
    """Return the stable remote identity of a timezone source document."""
    if info is None:
        return None
    uploaded_at = info.get("uploaded_at")
    if hasattr(uploaded_at, "isoformat"):
        uploaded_at = uploaded_at.isoformat()
    return {
        key: value
        for key, value in {
            "type": ref_type,
            "name": info.get("name"),
            "uploaded_at": uploaded_at,
            "url": info.get("url"),
            "source_url": info.get("source_url"),
        }.items()
        if value is not None
    }


def _is_chair_notes_downgrade(
    current_ref: dict | None,
    cached_ref: object,
) -> bool:
    if not isinstance(current_ref, dict) or not isinstance(cached_ref, dict):
        return False
    if current_ref.get("type") != "chair_notes" or cached_ref.get("type") != "chair_notes":
        return False
    return _extract_version_parts_from_name(
        str(current_ref.get("name", ""))
    ) < _extract_version_parts_from_name(str(cached_ref.get("name", "")))


def _normalize_for_compare(entries: list[dict]) -> set[tuple]:
    """Convert list of dicts to a set of tuples for order-independent comparison.

    Only compares the (folder, name, uploaded_at) triple — ignoring list order
    so that different FTP listing orderings don't trigger false positives.
    """
    return {
        (e.get("folder", ""), e.get("name", ""), e.get("uploaded_at", ""))
        for e in entries
        if isinstance(e, dict)
    }


def main() -> None:
    # Cache misses are staged here for an optional check-to-build artifact.
    # The directory is ignored and recreated for every check invocation so a
    # stale local file can never be mistaken for a fresh transfer.
    shutil.rmtree(EXTRA_FILES_TRANSFER_DIR, ignore_errors=True)

    # 1. Fetch current remote state (lightweight — directory listing only)
    cfg = load_config()
    print(
        "Checking FTP for schedule updates "
        f"({len(cfg['inbox_urls'])} inbox URL(s), "
        f"{len(cfg['extra_folders'])} extra folder(s))…"
    )
    state = load_schedule_state()
    local_meeting_hint = local_reference_meeting_id()
    cached_meeting_id = state.get("meeting_id")
    if not isinstance(cached_meeting_id, str) or not cached_meeting_id:
        cached_meeting_id = None
    preferred_meeting_id = local_meeting_hint or cached_meeting_id
    cached_meeting_source = state.get("meeting_source")
    if (
        cached_meeting_source == "local"
        and cached_meeting_id is not None
    ):
        # Covers local sources whose filename is irregular or comes from a
        # configured extra file rather than ref_in_manual/.
        locked_meeting_id = cached_meeting_id
    else:
        locked_meeting_id = local_meeting_hint
    if local_meeting_hint:
        print(f"Using local reference meeting hint: {local_meeting_hint}")

    try:
        remote_all = get_all_remote_schedule_info(
            urls=cfg["inbox_urls"],
            extra_folders=cfg["extra_folders"],
            preferred_meeting_id=preferred_meeting_id,
            locked_meeting_id=locked_meeting_id,
        )
    except Exception as e:
        print(f"FTP check failed: {e}")
        # Keep checking local refs and extra_files below.  A transient FTP
        # failure must not hide a committed local change.
        remote_all = None

    if remote_all is None:
        print("Skipping FTP state comparison because the listing failed.")
    elif not remote_all:
        print("No schedule files found on FTP; skipping FTP state comparison.")
    else:
        for info in remote_all:
            folder = info.get("folder", "?")
            print(
                f"  Remote [{folder}]: {info['name']} "
                f"uploaded_at={info.get('uploaded_at')}"
            )

    # 2. Compare with cached state (stored in repo as docs/.schedule_state.json)
    cached = state.get("files")

    # Handle migration from old single-dict format
    if isinstance(cached, dict) and "name" in cached:
        cached = [cached]

    changed = False
    if remote_all:
        if cached is None:
            print("No cached state found — treating as changed.")
            changed = True
        elif not isinstance(cached, list) or not all(
            isinstance(entry, dict) for entry in cached
        ):
            print("Cached remote state is invalid — treating as changed.")
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
    elif remote_all is not None and cached is None:
        # No remote files is a valid state for a local-reference-only setup;
        # local hashes below still decide whether a rebuild is required.
        print("No remote files and no cached remote state; skipping FTP comparison.")
    elif remote_all is None:
        print("No cached remote comparison because FTP was unavailable.")

    # Timezone inputs are independent of schedule files. Chair notes can be
    # uploaded after a meeting's first schedule, so compare the best usable
    # remote reference separately. A missing result does not invalidate an
    # existing reference because 3GPP directory listings can fail transiently.
    cached_timezone_ref = state.get("timezone_ref")
    current_timezone_ref = None
    if (
        isinstance(cached_timezone_ref, dict)
        and cached_timezone_ref.get("type") == "agenda"
        and cached_timezone_ref.get("origin") != "local"
    ):
        agenda_info = get_latest_agenda_info(cfg.get("agenda_urls") or [])
        agenda_name = str((agenda_info or {}).get("name", "")).lower()
        if agenda_name.endswith(".docx"):
            current_timezone_ref = _timezone_reference("agenda", agenda_info)
    elif not (
        isinstance(cached_timezone_ref, dict)
        and cached_timezone_ref.get("origin") == "local"
    ):
        agenda_info = get_latest_agenda_info(cfg.get("agenda_urls") or [])
        agenda_name = str((agenda_info or {}).get("name", ""))
        current_timezone_ref = (
            _timezone_reference("agenda", agenda_info)
            if agenda_name.lower().endswith(".docx")
            else None
        )
        if current_timezone_ref is None:
            chair_notes_info = get_latest_chair_notes_info(
                urls=cfg["inbox_urls"],
                extra_folders=cfg["extra_folders"],
                preferred_meeting_id=preferred_meeting_id,
            )
            current_timezone_ref = _timezone_reference(
                "chair_notes",
                chair_notes_info,
            )

    if (
        current_timezone_ref is not None
        and current_timezone_ref != cached_timezone_ref
        and not _is_chair_notes_downgrade(
            current_timezone_ref,
            cached_timezone_ref,
        )
    ):
        changed = True
        print(
            "Timezone reference changed: "
            f"{cached_timezone_ref!r} → {current_timezone_ref!r}"
        )

    # 3. Compare manually-provided local reference files (ref_in_manual/).
    # These are committed to the repo, so content hashes are stable across
    # CI checkouts and detect local-only changes that the FTP scan misses.
    local_refs = local_reference_hashes()
    cached_local_refs = state.get("local_refs")
    if local_refs or cached_local_refs is not None:
        print(f"Local reference files: {sorted(local_refs)}")
        if cached_local_refs is None:
            changed = True
            print(
                "Local reference files present but not tracked in state — "
                "treating as changed (first run)."
            )
        elif not isinstance(cached_local_refs, dict):
            changed = True
            print("Cached local reference state is invalid — treating as changed.")
        elif cached_local_refs != local_refs:
            changed = True
            for name in sorted(set(cached_local_refs) | set(local_refs)):
                old = cached_local_refs.get(name)
                new = local_refs.get(name)
                if old != new:
                    kind = (
                        "modified"
                        if old is not None and new is not None
                        else "removed"
                        if new is None
                        else "added"
                    )
                    print(f"  Local {kind}: {name}")

    # 4. Check external files (config.json ``extra_files``)
    extra_files = cfg.get("extra_files") or []
    if extra_files:
        print(f"Checking extra files ({len(extra_files)} URL(s))…")
        extra_files_artifact_ready = False
        try:
            ext_changed, ext_state = check_external_files(
                extra_files,
                staging_dir=EXTRA_FILES_TRANSFER_DIR,
            )
            if ext_changed:
                changed = True

            staged_files = [
                path
                for path in EXTRA_FILES_TRANSFER_DIR.iterdir()
                if path.is_file()
            ] if EXTRA_FILES_TRANSFER_DIR.exists() else []
            if staged_files:
                save_external_files_state(
                    ext_state,
                    EXTRA_FILES_TRANSFER_DIR / ".extra_files_state.json",
                )
                extra_files_artifact_ready = True
        except Exception as e:
            print(f"Extra files check failed: {e}")
        _set_output(
            "extra_files_artifact",
            str(extra_files_artifact_ready).lower(),
        )
    else:
        # Removing extra_files from config is an intentional input change.
        # Detect stale committed external state so the next build can remove
        # the old source from the generated schedule and clear the state.
        stale_extra_state = load_external_files_state()
        if stale_extra_state.get("files") or stale_extra_state.get("config"):
            changed = True
            print(
                "Configured extra_files is empty but committed external "
                "file state remains — treating as changed."
            )

    print(f"Cached: {cached}")
    print(f"Changed: {changed}")

    # State is saved by the build-and-deploy job (committed to repo),
    # not here — so a failed build will retry on the next check.
    _set_output("changed", str(changed).lower())


def _set_output(name: str, value: str) -> None:
    """Write to $GITHUB_OUTPUT (or print for local testing)."""
    ghout = os.environ.get("GITHUB_OUTPUT")
    if ghout:
        with open(ghout, "a") as f:
            f.write(f"{name}={value}\n")
    print(f"::set-output {name}={value}")


if __name__ == "__main__":
    main()
