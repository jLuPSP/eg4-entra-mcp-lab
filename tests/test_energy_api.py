from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from eg4_entra_mcp.energy_api import create_app
from eg4_entra_mcp.settings import EnergySettings


@pytest.mark.asyncio
async def test_energy_api_mock_read_and_write_gate(tmp_path: Path) -> None:
    settings = EnergySettings(
        data_dir=tmp_path / "data",
        audit_log=tmp_path / "audit.jsonl",
        auth_disabled=True,
        eg4_mode="mock",
        inverter_allowlist=["12000XP-DEMO"],
        control_enabled=False,
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer local-energy-test-token"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/inverters/12000XP-DEMO/state", headers=headers)
            assert response.status_code == 200
            assert response.json()["battery_soc_percent"] == 67.0
            plan = await client.post(
                "/v1/quick-charge/plans",
                headers=headers,
                json={"inverter_serial": "12000XP-DEMO", "duration_minutes": 15},
            )
            assert plan.status_code == 201
            body = plan.json()
            commit = await client.post(
                f"/v1/operations/{body['operation_id']}/commit",
                headers=headers,
                json={"expected_state_hash": body["expected_state_hash"]},
            )
            assert commit.status_code == 403
            assert commit.json()["error"] == "writes_not_armed"

@pytest.mark.asyncio
async def test_energy_api_commit_verifies_readback(tmp_path: Path) -> None:
    settings = EnergySettings(
        data_dir=tmp_path / "data",
        audit_log=tmp_path / "audit.jsonl",
        auth_disabled=True,
        eg4_mode="mock",
        inverter_allowlist=["12000XP-DEMO"],
        control_enabled=True,
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer local-energy-test-token"}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            plan = await client.post(
                "/v1/quick-charge/plans",
                headers=headers,
                json={"inverter_serial": "12000XP-DEMO", "duration_minutes": 15},
            )
            body = plan.json()
            commit = await client.post(
                f"/v1/operations/{body['operation_id']}/commit",
                headers=headers,
                json={"expected_state_hash": body["expected_state_hash"]},
            )
            assert commit.status_code == 200
            assert commit.json()["accepted"] is True
            assert commit.json()["state"]["quick_charge_active"] is True
