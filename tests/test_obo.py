from __future__ import annotations

from pathlib import Path

import pytest

from eg4_entra_mcp.obo import OboClient


def test_certificate_credential_loads_private_key_and_passphrase(tmp_path: Path) -> None:
    key = tmp_path / "gateway.pem"
    password = tmp_path / "password"
    key.write_text("placeholder-private-key", encoding="utf-8")
    password.write_text("placeholder-passphrase\n", encoding="utf-8")
    credential = OboClient._load_certificate(key, "AABBCC", password)
    assert credential == {
        "private_key": "placeholder-private-key",
        "thumbprint": "AABBCC",
        "passphrase": "placeholder-passphrase",
    }


def test_certificate_credential_requires_thumbprint(tmp_path: Path) -> None:
    key = tmp_path / "gateway.pem"
    key.write_text("placeholder-private-key", encoding="utf-8")
    with pytest.raises(ValueError, match="thumbprint"):
        OboClient._load_certificate(key, "", None)
