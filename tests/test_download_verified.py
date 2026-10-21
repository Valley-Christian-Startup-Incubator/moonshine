import hashlib
import importlib.util
import io
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("download_verified", Path(__file__).parents[1] / "scripts/download_verified.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_verified_artifact_replaces_destination(monkeypatch, tmp_path):
    payload = b"verified executable"
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(payload))
    target = tmp_path / "artifact"
    module.download("https://example.com/artifact", hashlib.sha256(payload).hexdigest(), target)
    assert target.read_bytes() == payload


def test_wrong_digest_preserves_existing_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(b"wrong executable"))
    target = tmp_path / "artifact"
    target.write_bytes(b"original")
    with pytest.raises(ValueError, match="SHA-256"):
        module.download("https://example.com/artifact", "0" * 64, target)
    assert target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]
