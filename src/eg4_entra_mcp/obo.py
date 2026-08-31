from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import msal


class OboExchangeError(RuntimeError):
    """Entra rejected an OBO exchange; details are intentionally sanitized."""


class OboClient:
    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        downstream_scope: str,
        certificate_path: Path,
        certificate_thumbprint: str,
        private_key_password_file: Path | None = None,
    ) -> None:
        self.downstream_scope = downstream_scope
        self._app = msal.ConfidentialClientApplication(
            client_id=client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=self._load_certificate(
                certificate_path, certificate_thumbprint, private_key_password_file
            ),
        )

    @staticmethod
    def _load_certificate(
        certificate_path: Path,
        thumbprint: str,
        password_file: Path | None,
    ) -> dict[str, Any]:
        if not thumbprint:
            raise ValueError("certificate thumbprint is required")
        private_key = certificate_path.read_text(encoding="utf-8")
        credential: dict[str, Any] = {
            "private_key": private_key,
            "thumbprint": thumbprint,
        }
        if password_file is not None:
            credential["passphrase"] = password_file.read_text(encoding="utf-8").strip()
        return credential

    async def exchange(self, incoming_access_token: str) -> str:
        result = await asyncio.to_thread(
            self._app.acquire_token_on_behalf_of,
            user_assertion=incoming_access_token,
            scopes=[self.downstream_scope],
        )
        access_token = result.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            code = str(result.get("error", "obo_failed"))
            raise OboExchangeError(f"OBO exchange failed ({code})")
        return access_token
