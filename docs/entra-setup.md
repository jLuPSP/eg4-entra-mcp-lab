# Entra setup

The idempotent script at scripts/provision_entra.ps1 uses an interactive Azure CLI device-code session and Microsoft Graph. It never asks you to paste a token into chat or a file.

## Bootstrap prerequisites

- A tenant account able to create app registrations and grant admin consent.
- Prefer Application Administrator or Cloud Application Administrator; avoid Global Administrator.
- Temporary delegated Microsoft Graph access for registration, delegated grants, and app-role assignment.
- Azure CLI and PowerShell 7 installed locally.

The runtime applications need no Microsoft Graph permission.

## 1. Sign in without sharing a token

Run this locally and finish authentication only in Microsoft's browser/device page:

~~~powershell
az login --use-device-code --tenant TENANT-ID --allow-no-subscriptions
~~~

Do not paste the device code, access token, refresh token, or CLI cache into chat.

## 2. Generate the Gateway OBO certificate

~~~powershell
pwsh -File scripts/new_gateway_certificate.ps1
~~~

This creates ignored files under secrets/:

- gateway.pem: private RSA key; mount only into Gateway A and keep mode 600.
- gateway.crt.pem: public certificate; safe to upload to the Gateway app registration.

The private key is never sent to Entra. For rotation, generate a new pair under temporary names, upload it with --append, deploy the new key/thumbprint, verify OBO, and only then remove the old Entra keyCredential.

## 3. Provision applications and least privilege

Use Entra object IDs for a user or security group; these are not email addresses. Reader assignments allow MCP reads. Operator assignments are created on both A and B because OBO tokens for B do not inherit app roles assigned on A.

~~~powershell
pwsh -File scripts/provision_entra.ps1   -TenantId TENANT-ID   -ReaderPrincipalId USER-OR-GROUP-OBJECT-ID   -GatewayCertificatePath secrets/gateway.crt.pem   -UploadGatewayCertificate   -GrantReadAdminConsent   -CreateAutomationAssignment
~~~

Control is deliberately omitted by default. Add both switches/assignments only for the bounded control experiment:

~~~powershell
pwsh -File scripts/provision_entra.ps1   -TenantId TENANT-ID   -OperatorPrincipalId OPERATOR-OBJECT-ID   -EnableControlPermissions   -GrantReadAdminConsent
~~~

The ignored entra.generated.json contains public IDs, role/scope UUIDs, and the public certificate thumbprint/expiry. Copy those non-secret values into the server .env; never copy the private key into a prompt.

## Direct MCP compatibility tests

The protected-resource metadata advertises fully qualified Entra scopes such as api://GATEWAY-CLIENT-ID/Mcp.Read. Runtime authorization separately checks the short Mcp.Read value from the scp claim. The MCP resource itself remains the canonical HTTP URL.

Entra commonly chooses token audience from scope namespaces rather than arbitrary RFC 8707 resource values. Test the exact authorization request produced by the target MCP client. If it sends an incompatible resource parameter, use the local OAuth bridge pattern rather than weakening audience validation.

VS Code OAuth prefers http://127.0.0.1:33418/ in the targeted implementation and can use the registered native http://localhost loopback redirect. Confirm the callback emitted by the installed build.
