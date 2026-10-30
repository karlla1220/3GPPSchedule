"""Gemini fallback shared by the WGs whose schedule is one file's versions.

The rules stay the default (README "LLM 폴백"). A WG sends only the items its
rules flag as uncertain, each with an ``id``, and says what an answer looks
like (a pydantic model) and how to check one against the source (``check``).
An answer is used only when its check passes; a failed item is asked once
more with its errors, then keeps the rule-based reading. A failed request
returns no answers, so the fallback can improve a page but never takes it
down. Answers are cached by the exact prompt, model and items.

What counts as uncertain, the prompt and the answer schema belong to the WG.
The checks below are the common pieces its ``check`` is built from.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Callable

from pydantic import BaseModel, ValidationError, create_model

from shared.schedule import time_to_minutes

HHMM = re.compile(r'(?:[01]\d|2[0-3]):[0-5]\d')
MAX_TITLE = 160


def has_key() -> bool:
    from dotenv import load_dotenv
    load_dotenv()
    return bool(os.environ.get('GEMINI_API_KEY'))


def gemini_request(message, schema, model, thinking='medium'):
    from google import genai
    from google.genai import types
    if not has_key():
        raise ValueError('GEMINI_API_KEY is required for the LLM fallback')
    with genai.Client(api_key=os.environ['GEMINI_API_KEY']) as client:
        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=model, contents=message,
                    config=types.GenerateContentConfig(
                        response_mime_type='application/json',
                        response_json_schema=schema, max_output_tokens=65536,
                        thinking_config=types.ThinkingConfig(thinking_level=thinking)))
                return json.loads(response.text)
            except (genai.errors.APIError, ValueError) as exc:
                if attempt == 2:
                    raise RuntimeError('LLM request failed after three attempts') from exc
                time.sleep(2 * (attempt + 1))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


# ----------------------------------------------------------------- checks

def words(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r'[A-Za-z0-9]+', text)]


def check_times(prefix, start, end, allowed) -> list[str]:
    """HH:MM on the 5-minute grid, in order, and written in the source (``allowed``)."""
    if not (HHMM.fullmatch(start) and HHMM.fullmatch(end)):
        return [f'{prefix}: times must be HH:MM']
    errors = []
    if start >= end:
        errors.append(f'{prefix}: start {start} is not before end {end}')
    if time_to_minutes(start) % 5 or time_to_minutes(end) % 5:
        errors.append(f'{prefix}: times must be on a 5-minute grid')
    for value in (start, end):
        if value not in allowed:
            errors.append(f'{prefix}: {value} is not written in the source')
    return errors


def title_errors(prefix, title, source: str) -> list[str]:
    """A title is the cited lines' own words: nothing paraphrased or added."""
    errors = []
    if not title.strip() or len(title) > MAX_TITLE:
        errors.append(f'{prefix}: empty or overlong title')
    missing = sorted(set(words(title)) - set(words(source)))
    if missing:
        errors.append(f'{prefix}: title words not in its cited text: {missing}')
    return errors


def written_errors(prefix, values, source: str, kind: str) -> list[str]:
    """Chairs and agenda items must be written in source, not inferred.

    ``kind`` is "chair" (a whole name: "Erlin", not "Erl") or "agenda item"
    (a whole number: "8.1" is not written in "8.10" or "18.1").
    """
    errors = []
    for value in values:
        pattern = (rf'(?<![\d.]){re.escape(value)}(?!\d)' if kind == 'agenda item'
                   else rf'(?<!\w){re.escape(value)}(?!\w)')
        if not re.search(pattern, source):
            errors.append(f'{prefix}: {kind} {value!r} is not written in its cited text'
                          if kind == 'agenda item' else f'{prefix}: {kind} {value!r} is not a name written in the text')
    return errors


def citation_errors(prefix, cited: list[list[int]], lines: list[str], meaningful: Callable[[str], bool] = bool) -> list[str]:
    """Each session cites its own lines, and every meaningful line is cited exactly once."""
    errors, seen = [], []
    for n, numbers in enumerate(cited):
        bad = [i for i in numbers if not 0 <= i < len(lines) or not lines[i]]
        if not numbers or bad:
            errors.append(f'{prefix} session {n}: cite one or more non-blank line numbers (bad: {bad})')
            continue
        seen += numbers
    if len(seen) != len(set(seen)):
        errors.append(f'{prefix}: a line is cited by more than one session')
    uncited = sorted(i for i, raw in enumerate(lines) if raw and meaningful(raw) and i not in seen)
    if uncited:
        errors.append(f'{prefix}: lines {uncited} are not cited')
    return errors


# ------------------------------------------------------------------ asking

class Answers(dict):
    """Accepted answers by item id; ``error`` says why there are none when the request failed."""
    error: str | None = None


def ask(prompt: str, items: list[dict], answer: type[BaseModel], check, *, model: str,
        cache_dir: Path, cache_name: str, label: str, request=None, thinking='medium', version=1,
        heading='CELLS') -> Answers:
    """Ask once for all uncertain ``items``; return the answers that pass ``check``.

    ``answer`` describes one item's answer and must have an ``id`` field.
    ``check(answer, item)`` returns the problems with it (empty means accepted).
    A cached reply is used without asking, its answers checked again. Only a
    reply that came back is cached; after an outage the next build asks again.
    """
    request = request or gemini_request
    by_id = {item['id']: item for item in items}
    result = Answers()
    if not by_id:
        return result
    key = digest(json.dumps([version, prompt, model, items], sort_keys=True).encode())
    path = cache_dir / f'{cache_name}-{key}.json'
    cached = _read(path)
    if cached:
        try:
            answers = {k: answer.model_validate(v) for k, v in cached['cells'].items()}
            result.update({k: a for k, a in answers.items() if k in by_id and not check(a, by_id[k])})
            return result
        except (KeyError, ValueError, ValidationError):
            pass
    try:
        result.update(_ask(prompt, items, by_id, answer, check, model, request, thinking, heading, label))
        _write(path, {'cells': {k: v.model_dump() for k, v in result.items()}})
    except Exception as exc:   # the rules' reading stands; the next build may retry
        print(f'{label} LLM fallback failed ({exc}); keeping the rule-based reading')
        result.clear()
        result.error = str(exc)[:300]
    return result


def _ask(prompt, items, by_id, answer, check, model, request, thinking, heading, label):
    schema = create_model('Answers', cells=(list[answer], ...))
    accepted, errors = {}, {}
    message = f'{prompt}\n\n{heading} (untrusted data):\n' + json.dumps(items, ensure_ascii=False)
    for attempt in range(2):
        raw = request(message, schema.model_json_schema(), model, thinking=thinking)
        for reply in schema.model_validate(raw).cells:
            if reply.id not in by_id or reply.id in accepted:
                continue
            problems = check(reply, by_id[reply.id])
            if problems:
                errors[reply.id] = problems
            else:
                accepted[reply.id] = reply
                errors.pop(reply.id, None)
        for item_id in by_id.keys() - accepted.keys() - errors.keys():
            errors[item_id] = [f'{item_id}: no answer']
        if not errors or attempt:
            break
        retry = [item for item in items if item['id'] in errors]
        message = (f'{prompt}\n\n{heading} (untrusted data):\n' + json.dumps(retry, ensure_ascii=False)
                   + '\n\nYOUR PREVIOUS ANSWER:\n' + json.dumps(raw, ensure_ascii=False)
                   + '\n\nFIX THESE PROBLEMS:\n' + '\n'.join(e for v in errors.values() for e in v))
    for item_id, problems in errors.items():
        print(f'{label} LLM answer for {item_id} rejected: {problems[0]}')
    return accepted
