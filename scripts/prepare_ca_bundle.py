"""Supplement certifi with the missing 3GPP intermediate, without disabling TLS."""
import argparse
from pathlib import Path
import subprocess

import certifi

INTERMEDIATE = Path(__file__).resolve().parents[1] / 'certificates/sectigo-public-server-authentication-ca-ov-r36.pem'


def prepare(destination: Path):
    # Verify against existing trusted roots; do not introduce a new trust anchor.
    subprocess.run(['openssl', 'verify', '-CAfile', certifi.where(), str(INTERMEDIATE)], check=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(Path(certifi.where()).read_bytes() + b'\n' + INTERMEDIATE.read_bytes())
    return destination.resolve()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(prepare(args.destination))
