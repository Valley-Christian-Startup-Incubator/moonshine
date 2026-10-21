"""Download a pinned HTTPS artifact with Moonshine's current CA bundle."""

import argparse
import hashlib
import os
import ssl
import tempfile
import urllib.request
from pathlib import Path

import certifi


def download(url: str, expected_sha256: str, destination: Path) -> None:
    if not url.startswith("https://"):
        raise ValueError("Artifact URL must use HTTPS")
    context = ssl.create_default_context(cafile=certifi.where())
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            digest = hashlib.sha256()
            with urllib.request.urlopen(url, context=context, timeout=60) as response:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
        if digest.hexdigest() != expected_sha256:
            raise ValueError("Downloaded artifact SHA-256 does not match the pinned value")
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("sha256")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    download(args.url, args.sha256, args.destination)
