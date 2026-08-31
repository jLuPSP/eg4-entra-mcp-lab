from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CommonSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    log_level: str = "INFO"
    data_dir: Path = Path("/app/data")
    audit_log: Path = Path("/app/logs/audit.jsonl")


class GatewaySettings(CommonSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix="GATEWAY_"
    )

    host: str = "0.0.0.0"
    port: int = 8930
    public_base_url: AnyHttpUrl = AnyHttpUrl("https://127.0.0.1:8930")
    tenant_id: str = "00000000-0000-0000-0000-000000000000"
    client_id: str = "00000000-0000-0000-0000-000000000000"
    downstream_scope: str = "api://00000000-0000-0000-0000-000000000000/.default"
    energy_api_url: AnyHttpUrl = AnyHttpUrl("http://energy-api:8931")
    certificate_path: Path = Path("/run/secrets/gateway.pem")
    certificate_thumbprint: str = ""
    certificate_private_key_password_file: Path | None = None
    required_read_scope: str = "Mcp.Read"
    required_control_scope: str = "Mcp.Control"
    control_scope_enabled: bool = False
    operator_role: str = "Operator"
    reader_role: str = "Reader"
    allowed_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1:8930", "localhost:8930"])
    allowed_origins: list[str] = Field(default_factory=list)
    auth_disabled: bool = False
    mock_principal_role: str = "Reader"

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def mcp_url(self) -> str:
        return f"{str(self.public_base_url).rstrip('/')}/mcp"

    @field_validator("auth_disabled")
    @classmethod
    def reject_truthy_non_boolean(cls, value: bool) -> bool:
        return value


class EnergySettings(CommonSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", env_prefix="ENERGY_"
    )

    host: str = "0.0.0.0"
    port: int = 8931
    tenant_id: str = "00000000-0000-0000-0000-000000000000"
    client_id: str = "00000000-0000-0000-0000-000000000000"
    required_read_scope: str = "Energy.Read"
    required_control_scope: str = "Energy.Control"
    app_read_role: str = "Energy.Read.All"
    operator_role: str = "Operator"
    eg4_base_url: AnyHttpUrl = AnyHttpUrl("https://monitor.eg4electronics.com")
    eg4_username_file: Path = Path("/run/secrets/eg4_username")
    eg4_password_file: Path = Path("/run/secrets/eg4_password")
    inverter_allowlist: list[str] = Field(default_factory=list)
    plant_allowlist: list[str] = Field(default_factory=list)
    eg4_mode: str = "mock"
    control_enabled: bool = False
    max_quick_charge_minutes: int = 30
    plan_ttl_seconds: int = 120
    request_timeout_seconds: float = 20.0
    min_write_interval_seconds: int = 30
    auth_disabled: bool = False

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"


@lru_cache
def gateway_settings() -> GatewaySettings:
    return GatewaySettings()


@lru_cache
def energy_settings() -> EnergySettings:
    return EnergySettings()
