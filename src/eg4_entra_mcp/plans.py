from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .models import OperationKind, OperationPlan, Principal


class PlanError(RuntimeError):
    pass


class PlanStore:
    def __init__(self, path: Path, ttl_seconds: int) -> None:
        self.path = path
        self.ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS plans (
                    operation_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    inverter_serial TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    expected_state_hash TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'planned',
                    result_json TEXT
                )"""
            )

    async def create(
        self,
        *,
        kind: OperationKind,
        serial: str,
        principal: Principal,
        expected_state_hash: str,
        parameters: dict[str, object],
    ) -> OperationPlan:
        owner = principal.object_id or principal.subject
        now = datetime.now(UTC)
        plan = OperationPlan(
            operation_id=str(uuid4()),
            kind=kind,
            inverter_serial=serial,
            requested_by=owner,
            created_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
            expected_state_hash=expected_state_hash,
            parameters=parameters,
        )
        await asyncio.to_thread(self._insert_sync, plan, principal.tenant_id)
        return plan

    def _insert_sync(self, plan: OperationPlan, tenant_id: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', NULL)",
                (
                    plan.operation_id,
                    plan.kind.value,
                    plan.inverter_serial,
                    tenant_id,
                    plan.requested_by,
                    plan.created_at.isoformat(),
                    plan.expires_at.isoformat(),
                    plan.expected_state_hash,
                    json.dumps(plan.parameters, sort_keys=True),
                ),
            )

    async def begin_commit(self, operation_id: str, principal: Principal) -> OperationPlan:
        async with self._lock:
            return await asyncio.to_thread(self._begin_commit_sync, operation_id, principal)

    def _begin_commit_sync(self, operation_id: str, principal: Principal) -> OperationPlan:
        owner = principal.object_id or principal.subject
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM plans WHERE operation_id = ?", (operation_id,)).fetchone()
            if row is None:
                raise PlanError("plan not found")
            if row["tenant_id"] != principal.tenant_id or row["owner_id"] != owner:
                raise PlanError("plan belongs to another principal")
            if row["status"] != "planned":
                raise PlanError(f"plan status is {row['status']}")
            if datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
                db.execute("UPDATE plans SET status='expired' WHERE operation_id=?", (operation_id,))
                raise PlanError("plan expired")
            changed = db.execute(
                "UPDATE plans SET status='executing' WHERE operation_id=? AND status='planned'",
                (operation_id,),
            ).rowcount
            if changed != 1:
                raise PlanError("plan could not be claimed")
            return self._row_to_plan(row, committed=True)

    async def finish(self, operation_id: str, status: str, result: dict[str, object]) -> None:
        await asyncio.to_thread(self._finish_sync, operation_id, status, result)

    def _finish_sync(self, operation_id: str, status: str, result: dict[str, object]) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE plans SET status=?, result_json=? WHERE operation_id=?",
                (status, json.dumps(result, sort_keys=True), operation_id),
            )

    @staticmethod
    def _row_to_plan(row: sqlite3.Row, *, committed: bool) -> OperationPlan:
        return OperationPlan(
            operation_id=row["operation_id"],
            kind=OperationKind(row["kind"]),
            inverter_serial=row["inverter_serial"],
            requested_by=row["owner_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            expected_state_hash=row["expected_state_hash"],
            parameters=json.loads(row["parameters_json"]),
            committed=committed,
        )
