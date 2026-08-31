"""Verify app-only authorization without printing its bearer token."""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

from eg4_entra_mcp.settings import gateway_settings


async def main() -> None:
    token = sys.stdin.read().strip()
    if not token:
        raise ValueError("app token is required on stdin")
    settings = gateway_settings()
    base_url = str(settings.energy_api_url).rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10, headers=headers) as client:
        read = await client.get(f"{base_url}/v1/inverters")
        plan = await client.post(
            f"{base_url}/v1/quick-charge/plans",
            json={"inverter_serial": "12000XP-DEMO", "duration_minutes": 5},
        )
    print(json.dumps({"read_status": read.status_code, "control_plan_status": plan.status_code}))


if __name__ == "__main__":
    asyncio.run(main())
