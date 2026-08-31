from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pylxpweb.models import SuccessResponse

from eg4_entra_mcp.eg4_adapter import Eg4Error, PylxpwebEg4Adapter


def adapter(tmp_path: Path, *, timeout: float = 20.0) -> PylxpwebEg4Adapter:
    username = tmp_path / "username"
    password = tmp_path / "password"
    username.write_text("placeholder-user", encoding="utf-8")
    password.write_text("placeholder-password", encoding="utf-8")
    return PylxpwebEg4Adapter(
        username_file=username,
        password_file=password,
        base_url="https://monitor.eg4electronics.com",
        inverter_allowlist=["ALLOWED-SERIAL"],
        plant_allowlist=[],
        timeout_seconds=timeout,
    )


@pytest.mark.parametrize("timeout", [0.0, -1.0, 0.5, float("nan"), float("inf"), True])
def test_invalid_timeout_is_rejected(tmp_path: Path, timeout: float) -> None:
    with pytest.raises(ValueError, match="positive whole number"):
        adapter(tmp_path, timeout=timeout)


@pytest.mark.asyncio
async def test_login_failure_closes_client_and_leaves_adapter_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = SimpleNamespace(login=AsyncMock(side_effect=RuntimeError("nope")), close=AsyncMock())
    monkeypatch.setattr("eg4_entra_mcp.eg4_adapter.LuxpowerClient", lambda *args, **kwargs: fake)
    subject = adapter(tmp_path)
    with pytest.raises(Eg4Error, match="login failed"):
        await subject.start()
    fake.close.assert_awaited_once()
    with pytest.raises(Eg4Error, match="not started"):
        await subject.current_state("ALLOWED-SERIAL")


@pytest.mark.asyncio
async def test_close_clears_client_and_rejects_double_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = SimpleNamespace(login=AsyncMock(), close=AsyncMock())
    monkeypatch.setattr("eg4_entra_mcp.eg4_adapter.LuxpowerClient", lambda *args, **kwargs: fake)
    subject = adapter(tmp_path)
    await subject.start()
    with pytest.raises(Eg4Error, match="already started"):
        await subject.start()
    await subject.close()
    fake.close.assert_awaited_once()
    with pytest.raises(Eg4Error, match="not started"):
        await subject.current_state("ALLOWED-SERIAL")


@pytest.mark.asyncio
async def test_offline_runtime_does_not_report_stale_live_values(tmp_path: Path) -> None:
    runtime = SimpleNamespace(
        lost=True,
        hasRuntimeData=False,
        ppv=999,
        pLoad170=888,
        pToUser=777,
        pToGrid=666,
        batPower=555,
        soc=44,
        vBat=523,
        hasUnclosedQuickChargeTask=False,
        statusText="offline",
        fwCode="placeholder",
        deviceTime="placeholder",
        modelText="12000XP",
    )
    fake = SimpleNamespace(
        api=SimpleNamespace(devices=SimpleNamespace(get_inverter_runtime=AsyncMock(return_value=runtime)))
    )
    subject = adapter(tmp_path)
    subject._client = fake  # type: ignore[assignment]
    state = await subject.current_state("ALLOWED-SERIAL")
    assert state.online is False
    assert state.pv_power_w is None
    assert state.load_power_w is None
    assert state.grid_power_w is None
    assert state.battery_power_w is None
    assert state.battery_soc_percent is None
    assert state.battery_voltage_v is None


@pytest.mark.asyncio
async def test_control_rejection_raises_and_duration_is_validated(tmp_path: Path) -> None:
    control = SimpleNamespace(
        start_quick_charge=AsyncMock(return_value=SuccessResponse(success=False, message="rejected")),
        stop_quick_charge=AsyncMock(return_value=SuccessResponse(success=False, message="rejected")),
    )
    subject = adapter(tmp_path)
    subject._client = SimpleNamespace(api=SimpleNamespace(control=control))  # type: ignore[assignment]
    with pytest.raises(Eg4Error, match="duration_minutes"):
        await subject.start_quick_charge("ALLOWED-SERIAL", True)  # type: ignore[arg-type]
    with pytest.raises(Eg4Error, match="rejected"):
        await subject.start_quick_charge("ALLOWED-SERIAL", 15)
    control.start_quick_charge.assert_awaited_once_with("ALLOWED-SERIAL", minute=15)
    with pytest.raises(Eg4Error, match="rejected"):
        await subject.stop_quick_charge("ALLOWED-SERIAL")
