from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier

from .models import Principal


class AuthenticationError(ValueError):
    """A bearer token is invalid for this resource."""


@dataclass(frozen=True)
class EntraJwtConfig:
    tenant_id: str
    audience: str
    required_scopes: frozenset[str] = frozenset()
    allow_app_roles: frozenset[str] = frozenset()

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_uri(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"


class EntraJwtValidator:
    def __init__(self, config: EntraJwtConfig) -> None:
        self.config = config
        self._jwk_client = PyJWKClient(config.jwks_uri, cache_keys=True, lifespan=3600)

    async def decode(self, token: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._decode_sync, token)

    def _decode_sync(self, token: str) -> dict[str, Any]:
        signing_key = self._jwk_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.config.audience,
            issuer=self.config.issuer,
            options={"require": ["exp", "iat", "iss", "aud", "tid"]},
        )
        if claims.get("tid") != self.config.tenant_id:
            raise AuthenticationError("token tenant is not allowed")
        if claims.get("ver") != "2.0":
            raise AuthenticationError("only Entra v2 access tokens are accepted")
        if not claims.get("azp") and not claims.get("appid"):
            raise AuthenticationError("token has no authorized-party claim")
        raw_roles = claims.get("roles", [])
        if not isinstance(raw_roles, list):
            raise AuthenticationError("token roles claim is malformed")
        scopes = frozenset(str(claims.get("scp", "")).split())
        roles = frozenset(str(role) for role in raw_roles)
        delegated_ok = bool(self.config.required_scopes.intersection(scopes))
        app_ok = bool(self.config.allow_app_roles.intersection(roles))
        if (self.config.required_scopes or self.config.allow_app_roles) and not (
            delegated_ok or app_ok
        ):
            raise AuthenticationError("token has no permitted scope or app role")
        return claims

    async def principal(self, token: str) -> Principal:
        claims = await self.decode(token)
        scopes = frozenset(str(claims.get("scp", "")).split())
        roles = frozenset(str(role) for role in claims.get("roles", []))
        client_id = str(claims.get("azp") or claims.get("appid") or "")
        return Principal(
            tenant_id=str(claims["tid"]),
            subject=str(claims.get("sub") or claims.get("oid") or client_id),
            object_id=str(claims["oid"]) if claims.get("oid") else None,
            client_id=client_id,
            name=str(claims["name"]) if claims.get("name") else None,
            scopes=scopes,
            roles=roles,
            token_kind="delegated" if scopes else "application",
        )


class EntraMcpTokenVerifier(TokenVerifier):
    def __init__(self, validator: EntraJwtValidator, resource: str) -> None:
        self.validator = validator
        self.resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await self.validator.decode(token)
        except (jwt.PyJWTError, AuthenticationError, httpx.HTTPError, ValueError):
            return None
        scopes = str(claims.get("scp", "")).split()
        roles = [str(role) for role in claims.get("roles", [])]
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or claims.get("appid") or ""),
            scopes=[*scopes, *roles],
            expires_at=int(claims["exp"]),
            resource=self.resource,
            subject=str(claims.get("sub") or claims.get("oid") or ""),
            claims=claims,
        )


class MockMcpTokenVerifier(TokenVerifier):
    def __init__(self, resource: str, scopes: list[str], role: str) -> None:
        self.resource = resource
        self.scopes = scopes
        self.role = role

    async def verify_token(self, token: str) -> AccessToken | None:
        if token != "local-test-token":
            return None
        return AccessToken(
            token=token,
            client_id="local-test-client",
            scopes=[*self.scopes, self.role],
            expires_at=int(time.time()) + 3600,
            resource=self.resource,
            subject="local-test-user",
            claims={
                "tid": "local-test-tenant",
                "oid": "local-test-user",
                "name": "Local Test User",
                "roles": [self.role],
                "scp": " ".join(self.scopes),
                "iss": "local-test-issuer",
            },
        )


def principal_from_claims(claims: dict[str, Any]) -> Principal:
    scopes = frozenset(str(claims.get("scp", "")).split())
    roles = frozenset(str(role) for role in claims.get("roles", []))
    client_id = str(claims.get("azp") or claims.get("appid") or "")
    return Principal(
        tenant_id=str(claims.get("tid", "")),
        subject=str(claims.get("sub") or claims.get("oid") or client_id),
        object_id=str(claims["oid"]) if claims.get("oid") else None,
        client_id=client_id,
        name=str(claims["name"]) if claims.get("name") else None,
        scopes=scopes,
        roles=roles,
        token_kind="delegated" if scopes else "application",
    )
