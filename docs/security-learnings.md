# Building a private-ready Entra/OBO MCP gateway for EG4 Monitor

## What this project proves

This lab puts an MCP interface in front of an EG4 inverter without giving MCP
clients the inverter account password. A signed-in user receives a delegated
token for Gateway API A. Gateway A validates that token and performs a genuine
Microsoft Entra On-Behalf-Of exchange for a second token addressed to Energy API
B. Energy B independently validates its token before it can reach the
server-held EG4 Monitor session.

The important boundary is:

```text
OAuth-capable MCP client
  -> delegated token, aud = Gateway A, scp = Mcp.Read
  -> Gateway A validates user, scope, role, issuer, tenant, audience, expiry
  -> Entra OBO using the original A assertion
  -> delegated token, aud = Energy B, scp = Energy.Read, azp = Gateway A
  -> Energy B validates again
  -> allow-listed, read-only EG4 adapter
  -> EG4 Monitor using credentials stored only on the server
```

The last hop is deliberately not described as OBO. EG4 Monitor does not expose
Microsoft Entra delegated authorization. It uses a service credential held in
two mode-600 files on the server host.

## Authorization model

Four single-tenant Entra applications separate client and resource identities:

| Application | Purpose | Permission used in the read-only deployment |
|---|---|---|
| Interactive Client | Native public client | Delegated `Mcp.Read` on Gateway A |
| MCP Gateway | Confidential API A and OBO client | Delegated `Energy.Read` on Energy B |
| Energy API | Resource API B | `Energy.Read`; app-only `Energy.Read.All` |
| Automation Reader | Confidential daemon identity | Application role `Energy.Read.All` only |

Reader and Operator are resource-specific app roles. Assigning Operator on A
would not cause it to appear in a token for B, so a future control deployment
must assign and validate it independently on both resources.

The verified read-only state intentionally has no `Mcp.Control` or
`Energy.Control` scopes enabled, no Operator assignment, and
`ENERGY_CONTROL_ENABLED=false`.

## Evidence from the live validation

The following checks were performed without printing raw JWTs or secrets:

- Gateway A returned an RFC 9728 protected-resource challenge for an
  unauthenticated MCP request.
- The protected-resource metadata advertised only the fully qualified
  `api://<gateway-client-id>/Mcp.Read` scope.
- A sanitized incoming token had `aud=<Gateway A>`, `scp=Mcp.Read`, the expected
  tenant and user object IDs, and the Reader role.
- The OBO token had `aud=<Energy B>`, `scp=Energy.Read`, and `azp=<Gateway A>`.
- Energy B accepted the B token with HTTP 200 and rejected the original A token
  with HTTP 401.
- The automation client received an app-only token with `aud=<Energy B>` and
  `roles=Energy.Read.All`, with no delegated `scp`. It could read but received
  HTTP 403 when attempting to create a control plan.
- The local OAuth bridge refreshed its cached Entra session, discovered all six MCP tools, and
  completed a real EG4 read through the stdio bridge without a static bearer
  header.
- A live target was discovered only after authentication, then pinned with
  explicit plant and inverter allowlists. It returned
  online telemetry through A -> OBO -> B -> EG4.
- A Reader token was denied when it attempted `plan_quick_charge`; no plan and no
  inverter write occurred.

## Security controls that mattered

### Fail closed at every audience boundary

Gateway A and Energy B validate distinct audiences. Accepting an A token at B
would collapse the OBO architecture into token forwarding and make the second
API boundary cosmetic. The explicit negative test against B is as important as
the successful OBO test.

### Discovery scope and runtime scope are different strings

OAuth discovery advertises fully qualified Entra scopes such as
`api://<client-id>/Mcp.Read`. Entra places the short value `Mcp.Read` in the
token's `scp` claim. Configuration and authorization checks must account for
that difference without weakening audience validation.

### EG4 reads are constrained after authentication

Authentication alone is not the EG4 safety boundary. The adapter also requires
an explicit inverter allowlist, optionally restricts plant IDs, paginates plant
inventory, fails closed on truncated device results, and suppresses stale live
values for offline devices.

### Planning is not writing

Quick-charge operations use plan/commit with TTL, owner binding, atomic claim,
state hashes, drift checks, duration limits, post-write readback, and an audit
trail. Even with those controls, the final write gate remains disabled until a
separate supervised control phase.

### Secrets stay out of clients and prompts

- The Gateway private key is mode 600 on the server and mounted only into Gateway A.
- Entra receives only the public certificate.
- EG4 username and password are installed interactively on the server,
  never passed as command-line arguments or chat content.
- The local bridge stores its serialized MSAL cache in the OS credential store and forces
  a refresh for each bridge process.
- Client configuration contains public client IDs and scopes, never
  bearer tokens or client secrets.

## Failure modes and what they taught us

### Device-code success is identity-topology dependent

An external member identity could complete normal browser authorization while
device-code authorization was rejected. Repeating device codes did not help.
Adding an interactive browser mode to the local bridge was the correct fix;
weakening tenant or issuer validation was not.

### A cached CLI account is not proof of a usable Graph session

`az account show` can succeed while the Microsoft Graph token cache is stale.
Provisioning now probes a Graph access token before deciding that login is
usable.

### Windows command wrappers can corrupt inline JSON

Passing compressed JSON directly through PowerShell to `az.cmd rest --body`
produced invalid Graph payloads. Writing the non-secret request body to a
short-lived UTF-8 file and passing `@<path>` removed the quoting ambiguity.

### Entra permission manifests need staged reconciliation

Graph rejected pre-authorized permission IDs when the corresponding exposed
scope did not already exist. Provisioning now creates API scopes first and adds
pre-authorized clients in a second idempotent stage.

An interrupted run can create enabled scopes before the public state file is
written. Enabled entitlement IDs cannot simply be replaced. Recovery therefore
adopts existing scope and role IDs by their semantic values before reconciling
the manifest.

### Graph relationship queries are not uniformly filterable

Filtering `appRoleAssignedTo` by `principalId` failed for this relationship.
The robust approach is to read the resource service principal's assignment
collection and compare both principal and role IDs locally before creating an
assignment.

### Mode 600 also requires the correct owner

The Gateway initially restarted because its mode-600 key was owned by root while
the container ran as UID 10001. Secret deployment must set both restrictive mode
and the least-privileged runtime owner.

### Credential Manager has a per-entry size ceiling

The serialized MSAL cache exceeded Windows Credential Manager's single-blob
limit. The bridge now splits it into bounded credential entries and stores a
count marker, while retaining backward-compatible reads of the original entry.

### Tool-hosted upgrades need lifecycle awareness

An MCP client could not replace its own files while it hosted the coding
session. A detached one-shot helper closed, upgraded, and reopened the
workspace. Operational automation must account for the process hosting it.

### Windows broker authentication needs its own redirect URI

One desktop client used Windows Web Account Manager and sent an
`ms-appx-web://Microsoft.AAD.BrokerPlugin/<client-id>` callback. Loopback
redirects alone therefore produced `AADSTS50011`. Native-client provisioning now
registers the WAM callback alongside the loopback and `vscode.dev` redirects;
the callback is public application metadata, not a secret.

## Remaining boundary

Real read-only EG4 access is verified. The later control phase enables only the
bounded quick-charge start/stop plan-and-commit workflow for one explicitly
assigned Operator. It adds both Entra control scopes, assigns Operator on A and
B, enables the Gateway control scope, and arms the Energy API write gate. The
automation client remains read-only. Enabling these gates is not itself an inverter operation; a
real commit still requires a separate reviewed plan and explicit confirmation.
