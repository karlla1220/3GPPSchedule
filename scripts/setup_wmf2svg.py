"""Download and verify the pinned optional WMF renderer used by CI and previews."""

from pathlib import Path
import hashlib
import sys
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.wmf2svg import DOWNLOAD_URL, JAR_PATH, SHA256


def main():
    data = JAR_PATH.read_bytes() if JAR_PATH.exists() else b""
    if hashlib.sha256(data).hexdigest() != SHA256:
        with urlopen(DOWNLOAD_URL, timeout=30) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != SHA256:
            raise ValueError("Downloaded wmf2svg checksum mismatch")
        JAR_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = JAR_PATH.with_suffix(".tmp")
        temp.write_bytes(data)
        temp.replace(JAR_PATH)
    print(f"Verified {JAR_PATH} (SHA-256 {SHA256})")


if __name__ == "__main__":
    main()
