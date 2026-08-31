from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from eg4_entra_mcp.eg4_adapter import MockEg4Adapter, state_hash
from eg4_entra_mcp.models import OperationKind, Principal
from eg4_entra_mcp.plans import PlanError, PlanStore


@pytest.mark.asyncio
async def test_plan_is_owner_bound_and_single_use(tmp_path: Path) -> None:
    store = PlanStore(tmp_path / "plans.db", ttl_seconds=120)
    await store.initialize()
    principal = Principal(
        tenant_id="tenant",
        subject="subject",
        object_id="owner-a",
        client_id="client",
        scopes=frozenset({"Energy.Control"}),
        roles=frozenset({"Operator"}),
    )
    other = principal.model_copy(update={"object_id": "owner-b"})
    plan = await store.create(
        kind=OperationKind.QUICK_CHARGE_START,
        serial="12000XP-DEMO",
        principal=principal,
        expected_state_hash="a" * 64,
        parameters={"duration_minutes": 15},
    )
    with pytest.raises(PlanError, match="another principal"):
        await store.begin_commit(plan.operation_id, other)
    claimed = await store.begin_commit(plan.operation_id, principal)
    assert claimed.committed
    with pytest.raises(PlanError, match="executing"):
        await store.begin_commit(plan.operation_id, principal)


@pytest.mark.asyncio
async def test_mock_adapter_state_hash_ignores_observation_time() -> None:
    adapter = MockEg4Adapter(["12000XP-DEMO"])
    first = await adapter.current_state("12000XP-DEMO")
    second = first.model_copy(update={"observed_at": first.observed_at + timedelta(seconds=5)})
    assert state_hash(first) == state_hash(second)
