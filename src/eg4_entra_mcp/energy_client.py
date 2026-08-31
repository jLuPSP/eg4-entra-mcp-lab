from __future__ import annotations

from typing import Any

import httpx

from .models import CommitRequest, QuickChargePlanRequest, StopChargePlanRequest


class EnergyApiError(RuntimeError):
    pass


class EnergyApiClient:
    def __init__(self, base_url: str, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def request(
        self,
        method: str,
        path: str,
        access_token: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(method, self.base_url + path, headers=headers, json=json)
        if response.status_code >= 400:
            raise EnergyApiError(f"energy API returned HTTP {response.status_code}")
        return response.json()

    async def list_inverters(self, token: str) -> Any:
        return await self.request("GET", "/v1/inverters", token)

    async def current_state(self, token: str, serial: str) -> Any:
        return await self.request("GET", f"/v1/inverters/{serial}/state", token)

    async def plan_quick_charge(self, token: str, request: QuickChargePlanRequest) -> Any:
        return await self.request("POST", "/v1/quick-charge/plans", token, json=request.model_dump())

    async def plan_stop_charge(self, token: str, request: StopChargePlanRequest) -> Any:
        return await self.request("POST", "/v1/quick-charge/stop-plans", token, json=request.model_dump())

    async def commit(self, token: str, operation_id: str, request: CommitRequest) -> Any:
        return await self.request(
            "POST",
            f"/v1/operations/{operation_id}/commit",
            token,
            json=request.model_dump(),
        )
