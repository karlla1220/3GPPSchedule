"""Build independent WG schedules, then assemble one GitHub Pages site."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from shared.schedule import load_schedule, save_schedule
from shared.renderer import generate_html
from working_groups.registry import PIPELINES, build_schedule, get_working_group
from shared.lifecycle import preserve_on_failure


from shared.site_config import load_site_config


def build_site(options):
    config = load_site_config(options.site_config)
    paths = [options.output_dir]
    if not options.render_only:
        for group in config["working_groups"]:
            if options.wg in {"all", group["id"]}:
                paths.extend(get_working_group(group["id"]).persistent_paths)
    # A final HTML write failure must also roll back newly advanced checkpoints.
    # Individual WG errors are handled inside, so successful siblings can advance.
    with preserve_on_failure(tuple(dict.fromkeys(paths))):
        failures = _build_site(options, config)
    if failures:
        raise RuntimeError(f"Failed WG builds: {', '.join(failures)}")


def _build_site(options, config):
    if options.wg != "all" and options.wg not in [g["id"] for g in config["working_groups"]]:
        raise ValueError(f"WG {options.wg} is not enabled in the site configuration")
    if getattr(options, "local", None) and options.wg == "all":
        raise ValueError("--local requires selecting one WG")
    root = options.output_dir
    schedules = {}
    failures = []
    for group in config["working_groups"]:
        wg = group["id"]
        snapshot = root / wg / "schedule.json"
        if snapshot.exists():
            try:
                saved = load_schedule(snapshot)
                if saved.wg_id != wg:
                    raise ValueError("Snapshot WG does not match its directory")
                schedules[wg] = saved
            except (ValueError, TypeError, KeyError, OSError) as exc:
                print(f"[{wg}] Could not read saved schedule: {exc}")
                if options.render_only or options.wg not in {"all", wg}:
                    failures.append(wg)
        if not options.render_only and (options.wg == "all" or options.wg == wg):
            try:
                paths = tuple(dict.fromkeys((*get_working_group(wg).persistent_paths, snapshot.parent)))
                with preserve_on_failure(paths):
                    schedule = build_schedule(wg, options)
                    if schedule.wg_id != wg:
                        raise ValueError(f"Pipeline returned {schedule.wg_id} for {wg}")
                    # Validate rendering before replacing the last successful snapshot.
                    generate_html(schedule)
                    save_schedule(schedule, snapshot)
                    schedules[wg] = schedule
            except (Exception, SystemExit) as exc:
                failures.append(wg)
                print(f"[{wg}] Build failed: {exc}; retaining last successful schedule")
    render_site(config, root, schedules)
    return failures


def render_site(config, root, schedules):
    """Assemble the shared site without invoking any WG parser."""
    if not schedules:
        raise ValueError("No schedules available; build a WG first")
    default = config["default_wg"]
    if default not in schedules:
        raise ValueError(f"Default WG {default} has no schedule; build it first")
    for wg, schedule in schedules.items():
        path = root / wg / "index.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generate_html(schedule, schedules=schedules, groups=config["working_groups"], presentation=config.get("presentation")), encoding="utf-8")
        print(f"[{wg}] {path}")
    # Real files back every URL, so direct links, refresh and history work on Pages.
    target = f"./{default}/"
    (root / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta http-equiv="refresh" content="0;url={target}"><title>3GPP Schedule</title>'
        f'<p><a href="{target}">Open {default} schedule</a></p>'
        f'<script>location.replace({json.dumps(target)} + location.search + location.hash);</script></html>',
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wg", choices=["all", *PIPELINES], default="all")
    parser.add_argument("--render-only", action="store_true", help="Render saved schedule JSON without parsing or network access")
    parser.add_argument("--no-download", action="store_true", help="Reuse downloaded source documents (LLM may still be called)")
    parser.add_argument("--local", help="Local DOCX, or RAN P timeplan ZIP (requires a single --wg)")
    parser.add_argument("--rebuild-slots", action="store_true", help="Clear selected WG interpretation state")
    parser.add_argument("--output-dir", type=Path, default=Path("docs"))
    parser.add_argument("--site-config", type=Path, default=Path("site.json"))
    options = parser.parse_args()
    build_site(options)


if __name__ == "__main__":
    main()
