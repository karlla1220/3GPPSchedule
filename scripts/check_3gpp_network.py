"""Read-only HTTPS and forced FTP fallback comparison; no build or deployment."""
import hashlib
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import ftp_transport
from working_groups.ran_plenary.sources import DEFAULTS, select_timeplan


def main():
    with httpx.Client(follow_redirects=True, timeout=60) as https, httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(526))
    ) as failed_https:
        listing = https.get(DEFAULTS['chair_url'])
        listing.raise_for_status()
        chosen = select_timeplan(listing.text, DEFAULTS['chair_url'], {})
        fallback = ftp_transport.get(DEFAULTS['chair_url'], listing=True, http=failed_https)
        ftp_chosen = select_timeplan(fallback.text, DEFAULTS['chair_url'], {})
        if chosen['url'] != ftp_chosen['url']:
            raise RuntimeError('HTTPS and FTP selected different timeplans; retry after checking source updates')
        result = []
        for url in (chosen['url'], DEFAULTS['agenda_url']):
            response = https.get(url)
            response.raise_for_status()
            recovered = ftp_transport.get(url, http=failed_https)
            digest = hashlib.sha256(response.content).hexdigest()
            if digest != hashlib.sha256(recovered.content).hexdigest():
                raise RuntimeError(f'HTTPS and FTP contents differ: {url}')
            result.append({'url': url, 'bytes': len(response.content), 'sha256': digest,
                           'https_status': response.status_code, 'fallback': recovered.headers['x-3gpp-transport']})
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
