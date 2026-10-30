"""Things a build wants a person to look at, though nothing failed.

A WG adds a notice during the build (for now: its schedule's layout was not
recognised and the whole document was read by the LLM). ``ci.py notify``
turns the build's notices into a GitHub issue comment, which the repository
owner gets by email. Each notice has a ``key``; the same key is reported once.
"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path('.ci/notices.json')


def read() -> list[dict]:
    try:
        value = json.loads(PATH.read_text(encoding='utf-8'))
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def add(wg_id: str, kind: str, key: str, title: str, lines: list[str]) -> None:
    """Record one notice; a second notice with the same key is ignored."""
    notices = read()
    if any(n.get('key') == key for n in notices):
        return
    notices.append({'wg': wg_id, 'kind': kind, 'key': key, 'title': title, 'lines': lines})
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(notices, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _body(notice: dict, run_url: str | None, owner: str | None) -> str:
    lines = [f'- {line}' for line in notice['lines']]
    if run_url:
        lines.append(f'- Build: {run_url}')
    if owner:
        lines.append(f'\ncc @{owner}')
    return '\n'.join(lines) + f'\n\n<!-- notice:{notice["key"]} -->'


def report(run_url: str | None = None, owner: str | None = None, gh=None) -> int:
    """Post this build's notices as GitHub issue comments; return how many were posted.

    One open issue per notice title (per WG and kind) collects its notices as
    comments, so the owner gets an email for each and the history stays in one
    place. A notice whose key the issue already has is not posted again (a
    cached LLM answer is reused by every later build). Errors are printed, not
    raised: a notice must never fail a build that succeeded.
    """
    import subprocess
    gh = gh or (lambda args: subprocess.run(['gh', *args], check=True, capture_output=True, text=True).stdout)
    posted = 0
    for notice in read():
        try:
            found = json.loads(gh(['issue', 'list', '--state', 'open', '--search', f'"{notice["title"]}" in:title',
                                   '--json', 'number,title', '--limit', '20']) or '[]')
            issue = next((i['number'] for i in found if i['title'] == notice['title']), None)
            body = _body(notice, run_url, owner)
            if issue is None:
                gh(['issue', 'create', '--title', notice['title'], '--body', body])
            else:
                seen = json.loads(gh(['issue', 'view', str(issue), '--json', 'body,comments']) or '{}')
                text = (seen.get('body') or '') + ''.join(c.get('body') or '' for c in seen.get('comments') or [])
                if f'notice:{notice["key"]}' in text:
                    continue
                gh(['issue', 'comment', str(issue), '--body', body])
            posted += 1
        except Exception as exc:
            print(f'Notice not posted ({notice.get("title")}): {exc}')
    return posted
