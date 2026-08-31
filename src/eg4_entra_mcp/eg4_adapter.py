from __future__ import annotations

import asyncio
import hashlib
import json
import math
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pylxpweb import LuxpowerClient

from .models import CurrentState, PlantSummary


class Eg4Error(RuntimeError):
    pass


class Eg4Adapter(ABC):
    @abstractmethod
    async def list_inverters(self) -> list[PlantSummary]: ...

    @abstractmethod
    async def current_state(self, serial: str) -> CurrentState: ...

    @abstractmethod
    async def start_quick_charge(self, serial: str, duration_minutes: int) -> dict[str, Any]: ...

    @abstractmethod
    async def stop_quick_charge(self, serial: str) -> dict[str, Any]: ...


class MockEg4Adapter(Eg4Adapter):
    def __init__(self, serials: list[str] | None = None) -> None:
        self.serials = serials or ["12000XP-DEMO"]
        self.quick_charge: dict[str, bool] = {serial: False for serial in self.serials}

    async def list_inverters(self) -> list[PlantSummary]:
        return [PlantSummary(plant_id="demo", name="12000XP Demo", inverter_serials=self.serials)]

    async def current_state(self, serial: str) -> CurrentState:
        self._require(serial)
        return CurrentState(
            inverter_serial=serial,
            online=True,
            pv_power_w=4200,
            load_power_w=1850,
            grid_power_w=-450,
            battery_power_w=1900,
            battery_soc_percent=67,
            battery_voltage_v=52.4,
            quick_charge_active=self.quick_charge[serial],
            raw={"mode": "mock"},
        )

    async def start_quick_charge(self, serial: str, duration_minutes: int) -> dict[str, Any]:
        self._require(serial)
        self.quick_charge[serial] = True
        return {"accepted": True, "duration_minutes": duration_minutes}

    async def stop_quick_charge(self, serial: str) -> dict[str, Any]:
        self._require(serial)
        self.quick_charge[serial] = False
        return {"accepted": True}

    def _require(self, serial: str) -> None:
        if serial not in self.serials:
            raise Eg4Error("inverter is not allow-listed")


class PylxpwebEg4Adapter(Eg4Adapter):
    def __init__(
        self,
        *,
        username_file: Path,
        password_file: Path,
        base_url: str,
        inverter_allowlist: list[str],
        plant_allowlist: list[str],
        timeout_seconds: float,
    ) -> None:
        self.username_file = username_file
        self.password_file = password_file
        self.base_url = base_url
        self.inverter_allowlist = set(inverter_allowlist)
        self.plant_allowlist = set(plant_allowlist)
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or not timeout_seconds.is_integer()
        ):
            raise ValueError("timeout_seconds must be a positive whole number")
        self.timeout_seconds = int(timeout_seconds)
        self._client: LuxpowerClient | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._client is not None:
                raise Eg4Error("EG4 adapter is already started")
            username = self.username_file.read_text(encoding="utf-8").strip()
            password = self.password_file.read_text(encoding="utf-8").strip()
            if not username or not password:
                raise Eg4Error("EG4 credentials are empty")
            client = LuxpowerClient(
                username,
                password,
                base_url=self.base_url,
                verify_ssl=True,
                timeout=self.timeout_seconds,
            )
            try:
                await client.login()
            except Exception as exc:
                await client.close()
                raise Eg4Error("EG4 login failed") from exc
            self._client = client

    async def close(self) -> None:
        async with self._lifecycle_lock:
            client, self._client = self._client, None
        if client is not None:
            await client.close()

    def _get_client(self) -> LuxpowerClient:
        if self._client is None:
            raise Eg4Error("EG4 adapter is not started")
        return self._client

    async def list_inverters(self) -> list[PlantSummary]:
        client = self._get_client()
        page = 1
        plants_seen = 0
        summaries: list[PlantSummary] = []
        while True:
            plants = await client.api.plants.get_plants(page=page, rows=100)
            if not plants.rows:
                break
            for plant in plants.rows:
                plant_id = str(plant.plantId)
                if self.plant_allowlist and plant_id not in self.plant_allowlist:
                    continue
                devices = await client.api.devices.get_devices(plant.plantId)
                if devices.total > len(devices.rows):
                    raise Eg4Error(
                        f"device inventory for plant {plant_id} is truncated: "
                        f"received {len(devices.rows)} of {devices.total}"
                    )
                serials = [
                    device.serialNum
                    for device in devices.rows
                    if not self.inverter_allowlist or device.serialNum in self.inverter_allowlist
                ]
                if serials:
                    summaries.append(
                        PlantSummary(plant_id=plant_id, name=plant.name, inverter_serials=serials)
                    )
            plants_seen += len(plants.rows)
            if plants_seen >= plants.total:
                break
            page += 1
        return summaries

    async def current_state(self, serial: str) -> CurrentState:
        self._require_serial(serial)
        client = self._get_client()
        runtime = await client.api.devices.get_inverter_runtime(serial)
        has_live_data = not runtime.lost and runtime.hasRuntimeData
        return CurrentState(
            inverter_serial=serial,
            online=not runtime.lost,
            pv_power_w=runtime.ppv if has_live_data else None,
            load_power_w=(
                runtime.pLoad170 if runtime.pLoad170 is not None else runtime.pToUser
            )
            if has_live_data
            else None,
            grid_power_w=(runtime.pToGrid - runtime.pToUser) if has_live_data else None,
            battery_power_w=runtime.batPower if has_live_data else None,
            battery_soc_percent=runtime.soc if has_live_data else None,
            battery_voltage_v=(runtime.vBat / 10)
            if has_live_data and runtime.vBat is not None
            else None,
            quick_charge_active=runtime.hasUnclosedQuickChargeTask,
            raw={
                "status": runtime.statusText,
                "firmware": runtime.fwCode,
                "device_time": runtime.deviceTime,
                "model": runtime.modelText,
            },
        )

    async def start_quick_charge(self, serial: str, duration_minutes: int) -> dict[str, Any]:
        self._require_serial(serial)
        if (
            isinstance(duration_minutes, bool)
            or not isinstance(duration_minutes, int)
            or not 1 <= duration_minutes <= 1440
        ):
            raise Eg4Error("duration_minutes must be an integer from 1 through 1440")
        async with self._lock:
            result = await self._get_client().api.control.start_quick_charge(
                serial, minute=duration_minutes
            )
            if not result.success:
                raise Eg4Error(result.message or "EG4 rejected quick-charge start")
            return result.model_dump(mode="json")

    async def stop_quick_charge(self, serial: str) -> dict[str, Any]:
        self._require_serial(serial)
        async with self._lock:
            result = await self._get_client().api.control.stop_quick_charge(serial)
            if not result.success:
                raise Eg4Error(result.message or "EG4 rejected quick-charge stop")
            return result.model_dump(mode="json")

    def _require_serial(self, serial: str) -> None:
        if not self.inverter_allowlist:
            raise Eg4Error("real EG4 mode requires an explicit inverter allowlist")
        if serial not in self.inverter_allowlist:
            raise Eg4Error("inverter is not allow-listed")


def state_hash(state: CurrentState) -> str:
    stable = state.model_dump(mode="json", exclude={"observed_at"})
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()
