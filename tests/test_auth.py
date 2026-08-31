from __future__ import annotations

from eg4_entra_mcp.auth import principal_from_claims


def test_delegated_and_application_claims_do_not_collapse() -> None:
    delegated = principal_from_claims(
        {"tid": "t", "sub": "u", "oid": "o", "azp": "client", "scp": "Energy.Read", "roles": ["Operator"]}
    )
    application = principal_from_claims(
        {"tid": "t", "sub": "daemon", "azp": "automation-client", "roles": ["Energy.Read.All"]}
    )
    assert delegated.token_kind == "delegated"
    assert delegated.can_control("Energy.Control", "Operator") is False
    assert application.token_kind == "application"
    assert application.can_read("Energy.Read", "Energy.Read.All") is True
    assert application.can_control("Energy.Control", "Operator") is False
