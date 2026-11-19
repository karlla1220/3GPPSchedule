"""Download and verify the pinned optional WMF renderer used by CI and previews."""

from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
import hashlib
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.wmf2svg import DOWNLOAD_URL, JAR_PATH, SHA256

# Maven Central answers 429 when CI runners share an IP; back off instead of failing the deploy.
RETRY_CODES = {429, 500, 502, 503, 504}
ATTEMPTS = 4


def download():
    for attempt in range(ATTEMPTS):
        try:
            with urlopen(DOWNLOAD_URL, timeout=30) as response:
                return response.read()
        except HTTPError as error:
            if error.code not in RETRY_CODES or attempt == ATTEMPTS - 1:
                raise
            time.sleep(15 * 2**attempt)


def main():
    data = JAR_PATH.read_bytes() if JAR_PATH.exists() else b""
    if hashlib.sha256(data).hexdigest() != SHA256:
        data = download()
        if hashlib.sha256(data).hexdigest() != SHA256:
            raise ValueError("Downloaded wmf2svg checksum mismatch")
        JAR_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = JAR_PATH.with_suffix(".tmp")
        temp.write_bytes(data)
        temp.replace(JAR_PATH)
    print(f"Verified {JAR_PATH} (SHA-256 {SHA256})")


if __name__ == "__main__":
    main()
