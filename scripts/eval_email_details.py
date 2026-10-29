"""Measure the email detail extraction against the RAN1#126bis test set.

Runs every mail of tests/fixtures/ran1/listserv_126bis through the same
steps as a build: thread and agenda item, newest reply as the collector
reads it (the preview, or the full text when the preview is cut), the grid
sessions still to come, one Gemini call, and the grid checks. Answers are
kept in --out by message id, so a rerun asks only for mails it has not seen
(delete the file to ask again).

    uv run python scripts/eval_email_details.py --out /tmp/eval
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from working_groups.ran1.email_details import (  # noqa: E402
    EXTRACT_INSTRUCTION, Cell, candidates, gemini_extract, request_text, resolve, times_in,
)
from working_groups.ran1.email_sessions import (  # noqa: E402
    agenda_of, needs_full_body, newest_reply, parse_feed, tdoc_numbers, thread_index,
)

FIXTURE = Path(__file__).resolve().parent.parent / "tests/fixtures/ran1/listserv_126bis"
TZ = ZoneInfo("Asia/Seoul")
FIRST_DAY = date(2026, 10, 12)
# Every day of RAN1#126bis: lunch 13:00-14:30, the last break ends at 17:00.
PARTS = {day: (13 * 60, 14 * 60 + 30, 17 * 60) for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]}


def load():
    read = lambda name: json.loads((FIXTURE / name).read_text(encoding="utf-8"))
    cells = [
        Cell(g["id"], g["day"], g["start"], g["end"], g["room"], g["name"], g["agenda_item"],
             tuple(g["inferred_agenda_items"]), online=g["online"], part=g["part"])
        for g in read("grid.json")["sessions"]
    ]
    agenda = {i["agenda_item"]: i["description"] for i in read("agenda_items.json")["agenda_items"]}
    index = thread_index(read("email_threads_chair.json")["threads"])
    feed = {m.id: m for m in parse_feed((FIXTURE / "feed_rss2_limit50.xml").read_text(encoding="utf-8"))}
    return read("mails.json"), read("labels.json"), cells, agenda, index, feed


def run(out: Path) -> dict:
    mails, labels, cells, agenda, index, feed = load()
    cache_path = out / "answers.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    results = {}
    threads = {}  # thread tag -> resolved items of earlier mails, oldest first
    for mail in sorted(mails, key=lambda m: parsedate_to_datetime(m["date"])):
        entry = feed[mail["id"]]
        full = needs_full_body(entry.snippet)
        reply = newest_reply(mail["body"] if full else entry.snippet)
        tdocs = list(dict.fromkeys(entry.tdocs + (tdoc_numbers(reply) if full else [])))
        found = agenda_of(mail["title"], reply, index, set(agenda))
        items = found["agenda_items"] + found["other"]
        sent = parsedate_to_datetime(mail["date"]).astimezone(TZ)
        offered = candidates(cells, items, sent, FIRST_DAY, TZ)
        if not items:
            results[mail["n"]] = []
            continue
        earlier = [i for i in threads.get(found["thread"], []) if found["thread"]]
        text = request_text(mail["title"], sent, items, agenda, offered, reply, earlier)
        key = hashlib.sha256((EXTRACT_INSTRUCTION + text).encode()).hexdigest()[:16]
        if key not in cache:
            cache[key] = {"n": mail["n"], "request": text, "answer": gemini_extract(text)}
            out.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
            print(f"asked #{mail['n']}", flush=True)
        allowed = times_in(reply) | times_in(" ".join(f'{i.get("start", "")} {i.get("end", "")}' for i in earlier))
        results[mail["n"]] = resolve(cache[key]["answer"], offered=offered, cells=cells,
                                     agenda_items=found["agenda_items"], sent=sent, tdocs=tdocs,
                                     allowed_times=allowed, day_parts=PARTS)
        if found["thread"]:
            threads.setdefault(found["thread"], []).extend(results[mail["n"]])
    return {"results": results, "labels": labels["mails"]}


def score(results: dict, labels: dict) -> dict:
    """Items matched by kind and session (informal by kind alone), per mail."""
    total = {"expected": 0, "found": 0, "matched": 0}
    rows = []
    for n in sorted(set(results) | {int(k) for k in labels}):
        want = [(i["kind"], i.get("session")) for i in labels.get(str(n), [])]
        got = [(i["kind"], i.get("session")) for i in results.get(n, [])]
        left = list(got)
        hit = 0
        missed = []
        for item in want:
            if item in left:
                left.remove(item)
                hit += 1
            else:
                missed.append(item)
        total["expected"] += len(want)
        total["found"] += len(got)
        total["matched"] += hit
        if hit != len(want) or left:
            rows.append((n, missed, left))
    total["recall"] = round(total["matched"] / total["expected"], 3) if total["expected"] else None
    total["precision"] = round(total["matched"] / total["found"], 3) if total["found"] else None
    return {"total": total, "misses": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    data = run(args.out)
    report = score(data["results"], data["labels"])
    (args.out / "results.json").write_text(json.dumps(data["results"], ensure_ascii=False, indent=1))
    print(json.dumps(report["total"]))
    for n, missed, extra in report["misses"]:
        print(f"#{n}: missed {missed} extra {extra}")


if __name__ == "__main__":
    main()
