"""Verify OBO audiences without printing either bearer token."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from typing import Any

import httpx

from eg4_entra_mcp.obo import OboClient
from eg4_entra_mcp.settings import gateway_settings


def _claims(token: str) -> dict[str, Any]:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
    value = json.loads(decoded)
    if not isinstance(value, dict):
        raise TypeError("JWT payload must be an object")
    return value


async def main() -> None:
    incoming_token = sys.stdin.read().strip()
    if not incoming_token:
        raise ValueError("incoming token is required on stdin")
    settings = gateway_settings()
    obo = OboClient(
        tenant_id=settings.tenant_id,
        client_id=settings.client_id,
        downstream_scope=settings.downstream_scope,
        certificate_path=settings.certificate_path,
        certificate_thumbprint=settings.certificate_thumbprint,
        private_key_password_file=settings.certificate_private_key_password_file,
    )
    downstream_token = await obo.exchange(incoming_token)
    downstream_claims = _claims(downstream_token)
    expected_audience = settings.downstream_scope.removeprefix("api://").removesuffix("/.default")
    scopes = set(str(downstream_claims.get("scp", "")).split())
    print(
        json.dumps(
            {
                "audience_matches_energy_api": downstream_claims.get("aud") == expected_audience,
                "tenant_matches": downstream_claims.get("tid") == settings.tenant_id,
                "has_delegated_scope": bool(scopes),
                "authorized_party_is_gateway": downstream_claims.get("azp") == settings.client_id,
                "has_user_object_id": bool(downstream_claims.get("oid")),
            },
            sort_keys=True,
        )
    )
    url = f"{str(settings.energy_api_url).rstrip('/')}/v1/inverters"
    async with httpx.AsyncClient(timeout=10) as client:
        accepted = await client.get(url, headers={"Authorization": f"Bearer {downstream_token}"})
        rejected = await client.get(url, headers={"Authorization": f"Bearer {incoming_token}"})
    print(json.dumps({"downstream_token_status": accepted.status_code, "incoming_a_token_status": rejected.status_code}))


if __name__ == "__main__":
    asyncio.run(main())
