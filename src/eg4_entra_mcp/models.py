from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Principal(BaseModel):
    tenant_id: str
    subject: str
    object_id: str | None = None
    client_id: str
    name: str | None = None
    scopes: frozenset[str] = Field(default_factory=frozenset)
    roles: frozenset[str] = Field(default_factory=frozenset)
    token_kind: str = "delegated"

    def can_read(self, delegated_scope: str, app_role: str | None = None) -> bool:
        return delegated_scope in self.scopes or (app_role is not None and app_role in self.roles)

    def can_control(self, delegated_scope: str, operator_role: str) -> bool:
        return delegated_scope in self.scopes and operator_role in self.roles


class OperationKind(StrEnum):
    QUICK_CHARGE_START = "quick_charge_start"
    QUICK_CHARGE_STOP = "quick_charge_stop"


class QuickChargePlanRequest(BaseModel):
    inverter_serial: str = Field(min_length=4, max_length=64)
    duration_minutes: int = Field(ge=1, le=1440)


class StopChargePlanRequest(BaseModel):
    inverter_serial: str = Field(min_length=4, max_length=64)


class OperationPlan(BaseModel):
    operation_id: str
    kind: OperationKind
    inverter_serial: str
    requested_by: str
    created_at: datetime
    expires_at: datetime
    expected_state_hash: str
    parameters: dict[str, Any]
    committed: bool = False


class CommitRequest(BaseModel):
    expected_state_hash: str = Field(min_length=16, max_length=128)


class CurrentState(BaseModel):
    inverter_serial: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    online: bool
    pv_power_w: float | None = None
    load_power_w: float | None = None
    grid_power_w: float | None = None
    battery_power_w: float | None = None
    battery_soc_percent: float | None = None
    battery_voltage_v: float | None = None
    quick_charge_active: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class PlantSummary(BaseModel):
    plant_id: str
    name: str | None = None
    inverter_serials: list[str] = Field(default_factory=list)


class AuditEvent(BaseModel):
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    service: str
    action: str
    outcome: str
    tenant_id: str | None = None
    object_id: str | None = None
    subject: str | None = None
    client_id: str | None = None
    target: str | None = None
    correlation_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
