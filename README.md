# EG4 Entra MCP Lab

A security-focused Model Context Protocol lab for an EG4 inverter. It replaces copied API keys at the MCP boundary with Microsoft Entra authentication and demonstrates three distinct authorization patterns:

1. Direct delegated access from a signed-in MCP client to Gateway API A.
2. True OAuth On-Behalf-Of (OBO) from Gateway A to Energy API B.
3. Application-only read access for a bounded automation client.

The final Energy API to EG4 Monitor hop uses a server-held EG4 portal credential because EG4 does not publish user-delegated OAuth. That hop is deliberately documented as service-credential access, not OBO.

## Safety status

- EG4 mode defaults to deterministic mock data.
- Real cloud mode requires an explicit inverter serial allowlist.
- All writes default to disabled: ENERGY_CONTROL_ENABLED=false.
- Quick charge uses a short-lived plan/commit operation with state-drift detection.
- No generic register-write, arbitrary HTTP, or shell tool is exposed.
- EG4 and Entra credentials must never be put in Git, prompts, MCP arguments, logs, or browser storage.

## Architecture

~~~text
VS Code/public client -- delegated token (aud=A) --> MCP Gateway A :8930
                                                     |
                                                     | Entra OBO
                                                     v
                                           Energy API B :8931
                                                     |
                                                     | EG4 portal session
                                                     v
                                           EG4 Monitor private API
~~~

Only Gateway A has a host port. API B is reachable only on the Compose network and still validates its own Entra audience and permissions.

## Entra applications

Provisioning creates four single-tenant applications:

| App | Type | Permission |
|---|---|---|
| EG4 Lab Interactive Client | Public native client | delegated Mcp.Read, optional Mcp.Control to A |
| EG4 Lab MCP Gateway | Confidential API A | exposes MCP scopes; OBO client of B |
| EG4 Lab Energy API | Resource API B | delegated Energy.Read/Energy.Control; app role Energy.Read.All |
| EG4 Lab Automation Reader | Confidential daemon | B application role Energy.Read.All only |

See docs/architecture.md and docs/entra-setup.md.

For the verified security properties, live validation evidence, failure modes,
and operational lessons, see [docs/security-learnings.md](docs/security-learnings.md).

## MCP tools

- whoami: inspect sanitized Entra authorization claims
- list_inverters: read allow-listed plants/inverters
- get_current_state: power flow, SOC, battery, online and quick-charge state
- plan_quick_charge: no mutation
- plan_stop_quick_charge: no mutation
- commit_operation: mutation only when every server-side gate passes

## Local mock development

~~~powershell
Copy-Item .env.example .env
# In .env only for isolated local testing:
# GATEWAY_AUTH_DISABLED=true
# ENERGY_AUTH_DISABLED=true
# ENERGY_EG4_MODE=mock
# Create an empty ignored secrets/mock_gateway.pem only for mock Compose startup.
uv sync --extra dev
uv run pytest
~~~

A local test MCP bearer is intentionally fixed as local-test-token; it is accepted only with mock auth explicitly enabled.

## Container deployment

1. Copy `.env.example` to a server-only `.env` and fill in only the documented identifiers and policy values.
2. Run `scripts/new_gateway_certificate.ps1`, upload only `gateway.crt.pem` with the provisioning script, and mount only the ignored private `gateway.pem` into Gateway A. See `docs/entra-setup.md`.
3. Install EG4 credentials interactively with `scripts/install_eg4_credentials.sh`; the secret directory is an optional first argument.
4. Generate ignored `certs/eg4-gateway.crt` and `certs/eg4-gateway.key` files with SANs for the published hostname. Trust only the public certificate on the client.
5. Keep `ENERGY_EG4_MODE=mock` and `ENERGY_CONTROL_ENABLED=false` for the first deployment, then run `docker compose up -d --build`.
6. Verify `https://127.0.0.1:8930/healthz` and the RFC 9728 metadata route. Set `GATEWAY_BIND_ADDRESS`, `GATEWAY_PUBLIC_BASE_URL`, and `GATEWAY_ALLOWED_HOSTS` explicitly before publishing to another trusted host.

## MCP clients

Copy `.vscode/mcp.example.json` to `.vscode/mcp.json` for an OAuth-capable client and substitute the generated public client ID. Do not put a client secret or bearer token in that file.

For clients that support stdio but not interactive remote MCP OAuth, this repo provides a local bridge. `eg4-client-login` performs Entra device-code or browser sign-in and stores the serialized MSAL cache in the OS credential store. `eg4-client-bridge` silently refreshes the session and proxies the authenticated remote MCP over stdio. Both commands take `--tenant-id`, `--client-id`, `--scope api://GATEWAY-CLIENT-ID/Mcp.Read`, and optionally `--mcp-url`.

## EG4 caveat

EG4 Monitor has no public supported developer API found during research. This project calls a community reverse-engineered portal API through pylxpweb, pinned to a tested version. Read and control behavior can vary by role, model, and firmware.

## License

MIT. This project is unaffiliated with EG4 Electronics or Microsoft.
