from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.authentication import AuthCredentials, AuthenticationBackend, AuthenticationError, SimpleUser
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .audit import AuditWriter
from .auth import EntraJwtConfig, EntraJwtValidator
from .eg4_adapter import Eg4Adapter, MockEg4Adapter, PylxpwebEg4Adapter, state_hash
from .models import (
    AuditEvent,
    CommitRequest,
    OperationKind,
    Principal,
    QuickChargePlanRequest,
    StopChargePlanRequest,
)
from .plans import PlanError, PlanStore
from .settings import EnergySettings, energy_settings

logger = logging.getLogger(__name__)


class EnergyAuthBackend(AuthenticationBackend):
    def __init__(self, settings: EnergySettings) -> None:
        self.settings = settings
        self.validator = EntraJwtValidator(
            EntraJwtConfig(
                tenant_id=settings.tenant_id,
                audience=settings.client_id,
                required_scopes=frozenset(
                    {settings.required_read_scope, settings.required_control_scope}
                ),
                allow_app_roles=frozenset({settings.app_read_role}),
            )
        )

    async def authenticate(self, conn: Any) -> tuple[AuthCredentials, SimpleUser] | None:
        header = conn.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        token = header[7:]
        if self.settings.auth_disabled and token == "local-energy-test-token":
            principal = Principal(
                tenant_id="local-test-tenant",
                subject="local-test-user",
                object_id="local-test-user",
                client_id="local-gateway",
                name="Local Test User",
                scopes=frozenset(
                    {self.settings.required_read_scope, self.settings.required_control_scope}
                ),
                roles=frozenset({self.settings.operator_role}),
            )
        else:
            try:
                principal = await self.validator.principal(token)
            except Exception as exc:
                raise AuthenticationError("invalid bearer token") from exc
        conn.scope["energy.principal"] = principal
        return AuthCredentials(["authenticated"]), SimpleUser(principal.subject)


def _principal(request: Request) -> Principal:
    principal = request.scope.get("energy.principal")
    if not isinstance(principal, Principal):
        raise PermissionError("authentication required")
    return principal


def _require_read(principal: Principal, settings: EnergySettings) -> None:
    if not principal.can_read(settings.required_read_scope, settings.app_read_role):
        raise PermissionError("read permission required")


def _require_control(principal: Principal, settings: EnergySettings) -> None:
    if not principal.can_control(settings.required_control_scope, settings.operator_role):
        raise PermissionError("delegated operator permission required")


def create_app(settings: EnergySettings | None = None, adapter: Eg4Adapter | None = None) -> Starlette:
    settings = settings or energy_settings()
    audit = AuditWriter(settings.audit_log)
    plan_store = PlanStore(settings.data_dir / "plans.sqlite3", settings.plan_ttl_seconds)
    if adapter is None:
        if settings.eg4_mode == "mock":
            adapter = MockEg4Adapter(settings.inverter_allowlist or None)
        elif settings.eg4_mode == "cloud":
            adapter = PylxpwebEg4Adapter(
                username_file=settings.eg4_username_file,
                password_file=settings.eg4_password_file,
                base_url=str(settings.eg4_base_url),
                inverter_allowlist=settings.inverter_allowlist,
                plant_allowlist=settings.plant_allowlist,
                timeout_seconds=settings.request_timeout_seconds,
            )
        else:
            raise ValueError("ENERGY_EG4_MODE must be mock or cloud")

    @asynccontextmanager
    async def lifespan(app: Starlette) -> Any:
        await plan_store.initialize()
        if isinstance(adapter, PylxpwebEg4Adapter):
            await adapter.start()
        app.state.adapter = adapter
        app.state.plan_store = plan_store
        try:
            yield
        finally:
            if isinstance(adapter, PylxpwebEg4Adapter):
                await adapter.close()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "eg4_mode": settings.eg4_mode, "writes": settings.control_enabled})

    async def list_inverters(request: Request) -> JSONResponse:
        principal = _principal(request)
        _require_read(principal, settings)
        rows = await adapter.list_inverters()
        await audit.write(AuditEvent(service="energy-api", action="list_inverters", outcome="success", tenant_id=principal.tenant_id, object_id=principal.object_id, client_id=principal.client_id))
        return JSONResponse([row.model_dump(mode="json") for row in rows])

    async def current_state(request: Request) -> JSONResponse:
        principal = _principal(request)
        _require_read(principal, settings)
        serial = request.path_params["serial"]
        state = await adapter.current_state(serial)
        await audit.write(AuditEvent(service="energy-api", action="get_current_state", outcome="success", tenant_id=principal.tenant_id, object_id=principal.object_id, client_id=principal.client_id, target=serial))
        return JSONResponse(state.model_dump(mode="json"))

    async def plan_start(request: Request) -> JSONResponse:
        principal = _principal(request)
        _require_control(principal, settings)
        payload = QuickChargePlanRequest.model_validate(await request.json())
        if payload.duration_minutes > settings.max_quick_charge_minutes:
            return JSONResponse({"error": "duration exceeds configured maximum"}, status_code=422)
        before = await adapter.current_state(payload.inverter_serial)
        plan = await plan_store.create(kind=OperationKind.QUICK_CHARGE_START, serial=payload.inverter_serial, principal=principal, expected_state_hash=state_hash(before), parameters={"duration_minutes": payload.duration_minutes})
        await audit.write(AuditEvent(service="energy-api", action="plan_quick_charge", outcome="planned", tenant_id=principal.tenant_id, object_id=principal.object_id, client_id=principal.client_id, target=payload.inverter_serial, details={"duration_minutes": payload.duration_minutes, "operation_id": plan.operation_id}))
        return JSONResponse(plan.model_dump(mode="json"), status_code=201)

    async def plan_stop(request: Request) -> JSONResponse:
        principal = _principal(request)
        _require_control(principal, settings)
        payload = StopChargePlanRequest.model_validate(await request.json())
        before = await adapter.current_state(payload.inverter_serial)
        plan = await plan_store.create(kind=OperationKind.QUICK_CHARGE_STOP, serial=payload.inverter_serial, principal=principal, expected_state_hash=state_hash(before), parameters={})
        return JSONResponse(plan.model_dump(mode="json"), status_code=201)

    async def commit(request: Request) -> JSONResponse:
        principal = _principal(request)
        _require_control(principal, settings)
        if not settings.control_enabled:
            await audit.write(AuditEvent(service="energy-api", action="commit_operation", outcome="writes_not_armed", tenant_id=principal.tenant_id, object_id=principal.object_id, client_id=principal.client_id))
            return JSONResponse({"error": "writes_not_armed"}, status_code=403)
        payload = CommitRequest.model_validate(await request.json())
        operation_id = request.path_params["operation_id"]
        try:
            plan = await plan_store.begin_commit(operation_id, principal)
        except PlanError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        current = await adapter.current_state(plan.inverter_serial)
        actual_hash = state_hash(current)
        if payload.expected_state_hash != plan.expected_state_hash or actual_hash != plan.expected_state_hash:
            await plan_store.finish(operation_id, "rejected_state_drift", {"error": "state drift"})
            return JSONResponse({"error": "state_drift"}, status_code=409)
        try:
            if plan.kind is OperationKind.QUICK_CHARGE_START:
                result = await adapter.start_quick_charge(
                    plan.inverter_serial, int(plan.parameters["duration_minutes"])
                )
            else:
                result = await adapter.stop_quick_charge(plan.inverter_serial)
            # Do not call this operation successful unless EG4 reports the expected state.
            after = await adapter.current_state(plan.inverter_serial)
            expected_active = plan.kind is OperationKind.QUICK_CHARGE_START
            if after.quick_charge_active is not expected_active:
                response = {
                    "operation_id": operation_id,
                    "accepted": False,
                    "upstream": result,
                    "state": after.model_dump(mode="json"),
                    "error": "readback_mismatch",
                }
                await plan_store.finish(operation_id, "readback_mismatch", response)
                await audit.write(
                    AuditEvent(
                        service="energy-api",
                        action=plan.kind.value,
                        outcome="readback_mismatch",
                        tenant_id=principal.tenant_id,
                        object_id=principal.object_id,
                        client_id=principal.client_id,
                        target=plan.inverter_serial,
                        details={"operation_id": operation_id},
                    )
                )
                return JSONResponse(response, status_code=502)
            response = {"operation_id": operation_id, "accepted": True, "upstream": result, "state": after.model_dump(mode="json")}
            await plan_store.finish(operation_id, "succeeded", response)
            await audit.write(AuditEvent(service="energy-api", action=plan.kind.value, outcome="success", tenant_id=principal.tenant_id, object_id=principal.object_id, client_id=principal.client_id, target=plan.inverter_serial, details={"operation_id": operation_id}))
            return JSONResponse(response)
        except Exception:
            await plan_store.finish(operation_id, "outcome_unknown", {"error": "upstream outcome unknown"})
            raise

    def auth_error(_: Any, exc: AuthenticationError) -> JSONResponse:
        return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})

    async def permission_error(_: Request, exc: Exception) -> JSONResponse:
        status = 401 if str(exc) == "authentication required" else 403
        return JSONResponse({"error": str(exc)}, status_code=status)

    return Starlette(
        routes=[
            Route("/healthz", health),
            Route("/v1/inverters", list_inverters),
            Route("/v1/inverters/{serial:str}/state", current_state),
            Route("/v1/quick-charge/plans", plan_start, methods=["POST"]),
            Route("/v1/quick-charge/stop-plans", plan_stop, methods=["POST"]),
            Route("/v1/operations/{operation_id:str}/commit", commit, methods=["POST"]),
        ],
        middleware=[Middleware(AuthenticationMiddleware, backend=EnergyAuthBackend(settings), on_error=auth_error)],
        exception_handlers={PermissionError: permission_error},
        lifespan=lifespan,
    )


def main() -> None:
    settings = energy_settings()
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
