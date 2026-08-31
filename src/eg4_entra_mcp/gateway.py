from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .auth import EntraJwtConfig, EntraJwtValidator, EntraMcpTokenVerifier, MockMcpTokenVerifier, principal_from_claims
from .energy_client import EnergyApiClient
from .models import CommitRequest, QuickChargePlanRequest, StopChargePlanRequest
from .obo import OboClient
from .settings import GatewaySettings, gateway_settings


class MockOboClient:
    async def exchange(self, incoming_access_token: str) -> str:
        if incoming_access_token != "local-test-token":
            raise ValueError("invalid local assertion")
        return "local-energy-test-token"


def create_app(
    settings: GatewaySettings | None = None,
    *,
    obo_client: OboClient | MockOboClient | None = None,
    energy_client: EnergyApiClient | None = None,
) -> Starlette | CORSMiddleware:
    settings = settings or gateway_settings()
    verifier = (
        MockMcpTokenVerifier(
            settings.mcp_url,
            [settings.required_read_scope, settings.required_control_scope],
            settings.mock_principal_role,
        )
        if settings.auth_disabled
        else EntraMcpTokenVerifier(
            EntraJwtValidator(
                EntraJwtConfig(
                    tenant_id=settings.tenant_id,
                    audience=settings.client_id,
                    required_scopes=frozenset(
                        {settings.required_read_scope, settings.required_control_scope}
                    ),
                )
            ),
            settings.mcp_url,
        )
    )
    if obo_client is None:
        obo_client = (
            MockOboClient()
            if settings.auth_disabled
            else OboClient(
                tenant_id=settings.tenant_id,
                client_id=settings.client_id,
                downstream_scope=settings.downstream_scope,
                certificate_path=settings.certificate_path,
                certificate_thumbprint=settings.certificate_thumbprint,
                private_key_password_file=settings.certificate_private_key_password_file,
            )
        )
    energy_client = energy_client or EnergyApiClient(str(settings.energy_api_url))

    mcp = MCPServer(
        "EG4 Entra MCP Lab",
        version="0.1.0",
        instructions=(
            "Read tools are safe. Planning tools never mutate the inverter. "
            "Commit tools are separately permission-gated and disabled by default."
        ),
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.issuer),
            resource_server_url=AnyHttpUrl(settings.mcp_url),
            # Middleware checks short values from the Entra scp claim. Discovery below
            # advertises the fully-qualified OAuth scope identifiers clients request.
            required_scopes=[settings.required_read_scope],
        ),
    )

    def _access() -> tuple[AccessToken, Any]:
        token = get_access_token()
        if token is None:
            raise PermissionError("authentication required")
        principal = principal_from_claims(token.claims or {})
        return token, principal

    def _require_read(principal: Any) -> None:
        if not {settings.reader_role, settings.operator_role}.intersection(principal.roles):
            raise PermissionError("Reader or Operator role required")

    def _require_control(token: AccessToken, principal: Any) -> None:
        if settings.required_control_scope not in token.scopes:
            raise PermissionError("Mcp.Control scope required")
        if settings.operator_role not in principal.roles:
            raise PermissionError("Operator role required")

    async def _downstream_token(token: AccessToken) -> str:
        return await obo_client.exchange(token.token)

    @mcp.tool(description="Show the authenticated Entra principal and authorization claims; performs no EG4 call.")
    def whoami() -> dict[str, Any]:
        token, principal = _access()
        return {
            "tenant_id": principal.tenant_id,
            "object_id": principal.object_id,
            "client_id": principal.client_id,
            "name": principal.name,
            "token_kind": principal.token_kind,
            "scopes": sorted(principal.scopes),
            "roles": sorted(principal.roles),
            "token_expires_at": token.expires_at,
        }

    @mcp.tool(description="List allow-listed EG4 plants and inverters. Read-only.")
    async def list_inverters() -> Any:
        token, principal = _access()
        _require_read(principal)
        return await energy_client.list_inverters(await _downstream_token(token))

    @mcp.tool(description="Get current power flow, battery state, online state, and quick-charge status. Read-only.")
    async def get_current_state(inverter_serial: str) -> Any:
        token, principal = _access()
        _require_read(principal)
        return await energy_client.current_state(await _downstream_token(token), inverter_serial)

    @mcp.tool(
        description="Create an expiring quick-charge plan. This does not modify the inverter."
    )
    async def plan_quick_charge(inverter_serial: str, duration_minutes: int) -> Any:
        token, principal = _access()
        _require_control(token, principal)
        request = QuickChargePlanRequest(inverter_serial=inverter_serial, duration_minutes=duration_minutes)
        return await energy_client.plan_quick_charge(await _downstream_token(token), request)

    @mcp.tool(
        description="Create an expiring plan to stop quick charge. This does not modify the inverter."
    )
    async def plan_stop_quick_charge(inverter_serial: str) -> Any:
        token, principal = _access()
        _require_control(token, principal)
        return await energy_client.plan_stop_charge(
            await _downstream_token(token), StopChargePlanRequest(inverter_serial=inverter_serial)
        )

    @mcp.tool(description="Commit one previously reviewed operation plan. Writes are disabled server-side by default.")
    async def commit_operation(operation_id: str, expected_state_hash: str) -> Any:
        token, principal = _access()
        _require_control(token, principal)
        return await energy_client.commit(
            await _downstream_token(token),
            operation_id,
            CommitRequest(expected_state_hash=expected_state_hash),
        )

    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
    )
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=transport_security,
        host=settings.host,
    )

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "auth": "mock" if settings.auth_disabled else "entra", "mcp": settings.mcp_url})

    async def root_metadata(_: Request) -> JSONResponse:
        scope_prefix = f"api://{settings.client_id}"
        advertised_scopes = [f"{scope_prefix}/{settings.required_read_scope}"]
        if settings.control_scope_enabled:
            advertised_scopes.append(f"{scope_prefix}/{settings.required_control_scope}")
        return JSONResponse(
            {
                "resource": settings.mcp_url,
                "authorization_servers": [settings.issuer],
                "scopes_supported": advertised_scopes,
                "bearer_methods_supported": ["header"],
            }
        )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> Any:
        async with mcp.session_manager.run():
            yield

    core_app = Starlette(
        routes=[
            Route("/healthz", health),
            Route("/.well-known/oauth-protected-resource", root_metadata),
            Route("/.well-known/oauth-protected-resource/mcp", root_metadata),
            Mount("/", app=mcp_app),
        ],
        lifespan=lifespan,
    )
    if settings.allowed_origins:
        return CORSMiddleware(
            core_app,
            allow_origins=settings.allowed_origins,
            allow_methods=["GET", "POST", "DELETE"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],
        )
    return core_app


def main() -> None:
    settings = gateway_settings()
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
