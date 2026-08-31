from __future__ import annotations

import httpx
import pytest

from eg4_entra_mcp.gateway import create_app
from eg4_entra_mcp.settings import GatewaySettings


@pytest.mark.asyncio
async def test_gateway_metadata_and_bearer_challenge(tmp_path) -> None:
    settings = GatewaySettings(
        public_base_url="http://testserver:8930",
        allowed_hosts=["testserver:8930"],
        auth_disabled=True,
        data_dir=tmp_path,
        audit_log=tmp_path / "audit.jsonl",
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver:8930") as client:
            metadata = await client.get("/.well-known/oauth-protected-resource")
            assert metadata.status_code == 200
            assert metadata.json()["resource"] == "http://testserver:8930/mcp"
            assert metadata.json()["scopes_supported"] == [
                f"api://{settings.client_id}/Mcp.Read"
            ]
            path_metadata = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert path_metadata.status_code == 200
            assert path_metadata.json() == metadata.json()
            denied = await client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            )
            assert denied.status_code == 401
            challenge = denied.headers["www-authenticate"]
            assert "resource_metadata" in challenge
            assert "/.well-known/oauth-protected-resource/mcp" in challenge

@pytest.mark.asyncio
async def test_gateway_metadata_advertises_control_only_when_enabled(tmp_path) -> None:
    settings = GatewaySettings(
        public_base_url="http://testserver:8930",
        allowed_hosts=["testserver:8930"],
        auth_disabled=True,
        control_scope_enabled=True,
        data_dir=tmp_path,
        audit_log=tmp_path / "audit.jsonl",
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver:8930") as client:
            metadata = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
            assert metadata["scopes_supported"] == [
                f"api://{settings.client_id}/Mcp.Read",
                f"api://{settings.client_id}/Mcp.Control",
            ]
