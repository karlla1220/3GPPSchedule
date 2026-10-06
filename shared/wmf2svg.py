"""Optional, pinned headless Java backend; never downloads during document parsing."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from lxml import etree as ET

VERSION = "0.10.6"
SHA256 = "e48852b450304f9b4abd087970eaeb935a0636823a9fd5990ff24b7f66a9287d"
JAR_PATH = Path(".cache/tools") / f"wmf2svg-{VERSION}.jar"
DOWNLOAD_URL = f"https://repo.maven.apache.org/maven2/net/arnx/wmf2svg/{VERSION}/wmf2svg-{VERSION}.jar"


def available() -> bool:
    return JAR_PATH.is_file() and shutil.which("java") is not None


def convert(data: bytes) -> bytes:
    if hashlib.sha256(JAR_PATH.read_bytes()).hexdigest() != SHA256:
        raise ValueError("wmf2svg JAR checksum mismatch")
    with TemporaryDirectory(prefix="chairman-wmf-") as directory:
        root = Path(directory)
        # EMF starts with the 32-bit EMR_HEADER type; placeable/standard WMF differs.
        source = root / (
            "input.emf" if data[:4] == b"\x01\x00\x00\x00" else "input.wmf"
        )
        target = root / "output.svg"
        source.write_bytes(data)
        result = subprocess.run(
            [
                "java",
                "-Djava.awt.headless=true",
                "-Xmx128m",
                "-jar",
                str(JAR_PATH.resolve()),
                "-replace-symbol-font",
                str(source),
                str(target),
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        # Upstream Main catches exceptions without setting a failing exit code.
        if result.stderr.strip() or not target.is_file() or not target.stat().st_size:
            raise ValueError("wmf2svg did not produce a clean SVG")
        output = target.read_bytes()
        root = ET.fromstring(
            output, ET.XMLParser(resolve_entities=False, no_network=True)
        )
        if root.tag != "{http://www.w3.org/2000/svg}svg":
            raise ValueError("wmf2svg output is not SVG")
        return output
