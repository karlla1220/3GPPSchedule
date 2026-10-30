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

``read_document`` is the other fallback: when the rules cannot read a
document's layout at all, the WG sends the whole document as blocks and the
answer must pass the WG's checks, or the build fails as before. Each use is a
notice (shared/notices.py), so a person hears that the chair changed the layout.
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


# A whole document takes about a minute with low thinking. A request must end:
# without a limit a stalled connection holds a CI build for hours.
REQUEST_TIMEOUT_MS = 300_000

# Thinking shares max_output_tokens with the answer. High thinks for about six
# minutes on any input, past the limit above, and on RAN4's slides it left too
# little of the 65536 tokens for the answer (README "LLM 폴백").
DOCUMENT_THINKING = 'low'


def _reply(response, seconds: float):
    """The parsed answer of a finished response, with what the request cost in the log."""
    def tokens(name):
        return getattr(response.usage_metadata, name, None) or 0
    finish = response.candidates[0].finish_reason if response.candidates else None
    reason = getattr(finish, 'name', finish)
    print(f'LLM reply after {seconds:.0f} s: {reason}, {tokens("prompt_token_count")} tokens in, '
          f'{tokens("thoughts_token_count")} thinking, {tokens("candidates_token_count")} out', flush=True)
    if reason != 'STOP':
        raise RuntimeError(f'LLM answer did not finish: {reason}')
    return json.loads(response.text or '')


def gemini_request(message, schema, model, thinking='medium'):
    """One answer. Errors that may pass are retried; a timeout or a cut answer is not.

    Asked again with the same thinking, those two take as long and cost as much,
    so the request fails and the next build tries. Three quick failures are cheap.
    """
    from google import genai
    from google.genai import types
    import httpx
    if not has_key():
        raise ValueError('GEMINI_API_KEY is required for the LLM fallback')
    with genai.Client(api_key=os.environ['GEMINI_API_KEY'],
                      http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS)) as client:
        for attempt in range(3):
            print(f'LLM request {attempt + 1}/3: {model}, thinking {thinking}, {len(message)} characters', flush=True)
            started = time.monotonic()
            try:
                response = client.models.generate_content(
                    model=model, contents=message,
                    config=types.GenerateContentConfig(
                        response_mime_type='application/json',
                        response_json_schema=schema, max_output_tokens=65536,
                        thinking_config=types.ThinkingConfig(thinking_level=thinking)))
                return _reply(response, time.monotonic() - started)
            except httpx.TimeoutException as exc:
                raise RuntimeError(f'LLM request timed out after {REQUEST_TIMEOUT_MS // 1000} s') from exc
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

    ``kind`` is "chair" (a whole name: "Erlin", not "Erl"), "agenda item"
    (a whole number: "8.1" is not written in "8.10" or "18.1"), or another
    name a WG checks the same way as a chair ("topic number" in RAN4).
    """
    errors = []
    for value in values:
        pattern = (rf'(?<![\d.]){re.escape(value)}(?!\d)' if kind == 'agenda item'
                   else rf'(?<!\w){re.escape(value)}(?!\w)')
        if not re.search(pattern, source):
            errors.append(f'{prefix}: chair {value!r} is not a name written in the text' if kind == 'chair'
                          else f'{prefix}: {kind} {value!r} is not written in its cited text')
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


def read_document(prompt: str, blocks: list[dict], answer: type[BaseModel], check, *, model: str,
                  cache_dir: Path, wg_id: str, source: str, meeting: str | None = None, request=None,
                  thinking=DOCUMENT_THINKING, version=1):
    """The whole document read by the LLM when the rules cannot read its layout.

    ``blocks`` are the document's parts with ids (paragraphs, table cells,
    slide shapes; the WG decides). ``answer`` is the schema of the whole
    schedule and ``check(result)`` returns its problems against the source.
    A cached answer is checked again; otherwise the LLM is asked, and asked
    once more with the problems. Raises ValueError when no answer passes, so
    the WG's build fails as it would without the fallback, and RuntimeError
    when a request fails. A passing answer is recorded as a notice for the
    build's report.
    """
    request = request or gemini_request
    key = digest(json.dumps([version, prompt, model, blocks], sort_keys=True).encode())
    path = cache_dir / f'document-{key}.json'
    result, errors = None, ['no answer']
    cached = _read(path)
    if cached:
        try:
            result = answer.model_validate(cached['result'])
            errors = check(result)
        except (KeyError, ValueError, ValidationError):
            result = None
    message = prompt + '\n\nDOCUMENT (untrusted data):\n' + json.dumps(blocks, ensure_ascii=False)
    for attempt in range(2):
        if result is not None and not errors:
            break
        print(f'[{wg_id}] Reading {source} whole, round {attempt + 1}/2', flush=True)
        raw = request(message, answer.model_json_schema(), model, thinking=thinking)
        result = answer.model_validate(raw)
        errors = check(result)
        message += ('\n\nYOUR PREVIOUS ANSWER:\n' + json.dumps(raw, ensure_ascii=False)
                    + '\n\nFIX THESE PROBLEMS:\n' + '\n'.join(errors))
    if errors:
        raise ValueError(f'the LLM could not read the document either: {errors[:3]}')
    _write(path, {'result': result.model_dump()})
    from shared import notices
    notices.add(wg_id, 'document-fallback', f'{wg_id}:{key}',
                f'{wg_id.upper()}: schedule layout not recognised, read by the LLM',
                [f'Meeting: {meeting or "unknown"}', f'Source: {source}', f'Model: {model}',
                 'The rules could not read this file\'s layout, so the whole document was read by the LLM and '
                 'checked against its text. Compare the page with the source file; if the layout change is '
                 'lasting, the WG\'s rules need updating.'])
    return result
