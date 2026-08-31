from __future__ import annotations

import keyring.backend
import msal

from eg4_entra_mcp import client_bridge


class MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


def test_msal_cache_round_trips_through_keyring(monkeypatch) -> None:
    backend = MemoryKeyring()
    monkeypatch.setattr(client_bridge.keyring, "get_password", backend.get_password)
    monkeypatch.setattr(client_bridge.keyring, "set_password", backend.set_password)
    cache = msal.SerializableTokenCache()
    cache.has_state_changed = True
    client_bridge._save_cache(cache)
    count = backend.get_password(client_bridge.SERVICE, client_bridge.CACHE_COUNT_ACCOUNT)
    assert count is not None
    assert int(count) >= 1
    assert backend.get_password(client_bridge.SERVICE, f"{client_bridge.CACHE_ACCOUNT}-0") is not None
    loaded = client_bridge._load_cache()
    assert isinstance(loaded, msal.SerializableTokenCache)


def test_bridge_forces_token_refresh(monkeypatch) -> None:
    config = client_bridge.BridgeConfig(
        tenant_id="tenant",
        client_id="client",
        scope="api://gateway/Mcp.Read",
        mcp_url="http://example.invalid/mcp",
    )
    cache = msal.SerializableTokenCache()
    monkeypatch.setattr(client_bridge, "_load_cache", lambda: cache)
    monkeypatch.setattr(client_bridge, "_save_cache", lambda _: None)

    class FakeApp:
        def get_accounts(self):
            return [{"home_account_id": "account"}]

        def acquire_token_silent(self, scopes, *, account, force_refresh):
            assert scopes == config.scopes
            assert force_refresh is True
            return {"access_token": "opaque-access-token"}

    monkeypatch.setattr(client_bridge, "_app", lambda _config, _cache: FakeApp())
    assert client_bridge._acquire_silent(config) == "opaque-access-token"
