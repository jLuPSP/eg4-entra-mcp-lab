# Architecture and security boundaries

## Token audiences

- The MCP client obtains a delegated access token with aud set to Gateway A.
- Gateway A validates signature, tenant, issuer, audience, expiry, and scp.
- Gateway A submits that original access token as the assertion in Entra OBO.
- Entra returns a new delegated token with aud set to Energy B.
- Energy B independently validates the B token and enforces scp or, for automation reads only, roles=Energy.Read.All.
- An A token is never accepted by B and is never forwarded directly.

## Permission model

| Action | A scope | B scope/role | User role | Write gate |
|---|---|---|---|---|
| Reads | Mcp.Read | Energy.Read | Reader or Operator | n/a |
| Plan control | Mcp.Control | Energy.Control | Operator | no mutation |
| Commit control | Mcp.Control | Energy.Control | Operator | ENERGY_CONTROL_ENABLED=true |
| Automation reads | n/a | Energy.Read.All role | application | read-only matrix |

Scopes express what a client may do for a user. Reader/Operator assignments express which users may use the app. Operator is assigned on both A and B because app roles are resource-specific and an OBO token for B does not inherit roles assigned on A. API B checks its own Energy.Control scope and Operator role.

## Quick-charge invariant

Planning captures a hash of current state. Commit is owner-bound to (tid, oid), expiring, atomically claimed, re-reads state, rejects drift, checks the write gate, applies one typed operation, and reads back. A timeout after sending a write is recorded as outcome_unknown and is not blindly retried.

## Secret boundary

The model-visible MCP surface never receives a token, cookie, password, private key, or confirmation credential. HTTP authorization is transport metadata. The Energy API owns the EG4 session, and only that container receives EG4 credential files.
