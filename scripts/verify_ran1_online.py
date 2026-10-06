"""Run the real download/Gemini build, recording failures without exposing API keys.

Run from the repository root. Output is a new directory, never a saved snapshot.
Local fallback is explicitly classified as an incomplete online verification.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    root = args.output_dir or Path('test_runs') / datetime.now().strftime('ran1-online-%Y%m%d-%H%M%S')
    if root.exists():
        parser.error('Use a new output directory to avoid validating an old result')
    env = os.environ.copy()
    key = env.get('GEMINI_API_KEY') or dotenv_values(args.env_file).get('GEMINI_API_KEY')
    if not key:
        parser.error('GEMINI_API_KEY is unavailable')
    env.update(GEMINI_API_KEY=key, PYTHONUNBUFFERED='1')
    root.mkdir(parents=True)
    command = [sys.executable, 'main.py', '--wg', 'ran1', '--rebuild-slots',
               '--output-dir', str(root / 'site')]
    report = {'started_at': datetime.now(timezone.utc).isoformat(),
              'command': command, 'api_key_available': True}
    lines = []
    with (root / 'pipeline.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        for line in process.stdout:
            safe = line.replace(key, '[REDACTED]')
            lines.append(safe)
            log.write(safe)
            log.flush()
            print(safe, end='', flush=True)
        report['exit_code'] = process.wait()
    text = ''.join(lines)
    calls = re.search(r'LLM calls: (\d+)', text)
    report['gemini_slot_calls'] = int(calls[1]) if calls else 0
    report['local_schedule_fallback'] = 'Using local file:' in text
    report['discovery_warnings'] = [line.strip() for line in lines if
                                   'Failed to list' in line or 'Discovery failed:' in line]
    snapshot = root / 'site/ran1/schedule.json'
    if report['exit_code'] == 0 and snapshot.exists():
        data = json.loads(snapshot.read_text())
        note = data.get('chairman_agreements', {})
        report.update(meeting_id=data['meeting_id'], source_files=data.get('source_files'),
                      sessions=sum(len(day['sessions']) for day in data['days']),
                      chairman_source={name: note.get(name) for name in
                                       ('meeting_id', 'source_url', 'document_file', 'sha256', 'status')})
        report['online_verified'] = (
            data['meeting_id'].lower() == 'ran1#126'
            and report['gemini_slot_calls'] > 0
            and not report['local_schedule_fallback']
            and not report['discovery_warnings']
            and note.get('meeting_id', '').lower() == 'ran1#126'
            and bool(note.get('source_url'))
            and bool(note.get('sections'))
        )
    else:
        report['online_verified'] = False
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    (root / 'attempt.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(f"Online verified: {report['online_verified']}; report: {root / 'attempt.json'}")
    return 0 if report['online_verified'] else 1


if __name__ == '__main__':
    sys.exit(main())
