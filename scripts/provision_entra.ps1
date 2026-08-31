#requires -Version 7.0
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)] [ValidatePattern('^[0-9a-fA-F-]{36}$')] [string] $TenantId,
    [string] $StatePath = (Join-Path $PSScriptRoot '..\entra.generated.json'),
    [string[]] $ReaderPrincipalId = @(),
    [string[]] $OperatorPrincipalId = @(),
    [string] $GatewayCertificatePath = (Join-Path $PSScriptRoot '..\secrets\gateway.crt.pem'),
    [switch] $GrantReadAdminConsent,
    [switch] $EnableControlPermissions,
    [switch] $CreateAutomationAssignment,
    [switch] $UploadGatewayCertificate
)

$ErrorActionPreference = 'Stop'
$graph = 'https://graph.microsoft.com/v1.0'

function Invoke-Graph {
    param([Parameter(Mandatory)][ValidateSet('GET','POST','PATCH')][string]$Method,
          [Parameter(Mandatory)][string]$Path,
          [object]$Body)
    $arguments = @('rest','--method',$Method,'--url',($graph + $Path),'--output','json')
    $bodyFile = $null
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 20 -Compress
        $bodyFile = New-TemporaryFile
        Set-Content -LiteralPath $bodyFile.FullName -Value $json -Encoding utf8NoBOM -NoNewline
        $arguments += @('--headers','Content-Type=application/json','--body',("@$($bodyFile.FullName)"))
    }
    try {
        $raw = & az @arguments
    } finally {
        if ($bodyFile) { Remove-Item -LiteralPath $bodyFile.FullName -Force -ErrorAction SilentlyContinue }
    }
    if ($LASTEXITCODE -ne 0) { throw "Microsoft Graph request failed: $Method $Path" }
    if ([string]::IsNullOrWhiteSpace(($raw -join ''))) { return $null }
    return (($raw -join [Environment]::NewLine) | ConvertFrom-Json -Depth 30)
}

function Ensure-Application {
    param([string]$DisplayName,[hashtable]$Desired,[hashtable]$State,[string]$StateKey)
    $record = $State[$StateKey]
    $app = $null
    if ($record -and $record.objectId) {
        try { $app = Invoke-Graph GET "/applications/$($record.objectId)" $null } catch { $app = $null }
    }
    if (-not $app) {
        $escaped = $DisplayName.Replace("'","''")
        $found = Invoke-Graph GET "/applications?%24filter=displayName%20eq%20'$escaped'" $null
        if ($found.value.Count -gt 1) { throw "Ambiguous application display name: $DisplayName" }
        if ($found.value.Count -eq 1) { $app = $found.value[0] }
    }
    if (-not $app) {
        if (-not $PSCmdlet.ShouldProcess($DisplayName,'Create application')) { throw "Cannot continue without application $DisplayName" }
        $app = Invoke-Graph POST '/applications' @{ displayName=$DisplayName; signInAudience='AzureADMyOrg'; tags=@('eg4-entra-mcp-lab') }
    }
    $Desired.displayName = $DisplayName
    $Desired.signInAudience = 'AzureADMyOrg'
    $Desired.tags = @('eg4-entra-mcp-lab')
    if ($PSCmdlet.ShouldProcess($DisplayName,'Reconcile application manifest')) {
        Invoke-Graph PATCH "/applications/$($app.id)" $Desired | Out-Null
        $app = Invoke-Graph GET "/applications/$($app.id)" $null
    }
    $existingState = $State[$StateKey]
    if (-not $existingState) { $existingState = @{} }
    $existingState.objectId = $app.id
    $existingState.clientId = $app.appId
    $State[$StateKey] = $existingState
    return $app
}

function Ensure-ServicePrincipal {
    param([string]$AppId,[hashtable]$State,[string]$StateKey)
    $found = Invoke-Graph GET "/servicePrincipals?%24filter=appId%20eq%20'$AppId'" $null
    if ($found.value.Count -gt 1) { throw "Multiple service principals for appId $AppId" }
    $sp = if ($found.value.Count -eq 1) { $found.value[0] } else { Invoke-Graph POST '/servicePrincipals' @{appId=$AppId} }
    $State[$StateKey].servicePrincipalId = $sp.id
    return $sp
}

function Ensure-AppRoleAssignment {
    param([string]$PrincipalId,[string]$ResourceSpId,[string]$AppRoleId)
    $found = Invoke-Graph GET "/servicePrincipals/$ResourceSpId/appRoleAssignedTo" $null
    $exists = @($found.value | Where-Object {
        $_.principalId -eq $PrincipalId -and $_.appRoleId -eq $AppRoleId
    }).Count -gt 0
    if (-not $exists) {
        Invoke-Graph POST "/servicePrincipals/$ResourceSpId/appRoleAssignedTo" @{
            principalId=$PrincipalId;resourceId=$ResourceSpId;appRoleId=$AppRoleId
        } | Out-Null
    }
}

function Ensure-DelegatedGrant {
    param([string]$ClientSpId,[string]$ResourceSpId,[string[]]$Scopes)
    $found = Invoke-Graph GET "/oauth2PermissionGrants?%24filter=clientId%20eq%20'$ClientSpId'%20and%20resourceId%20eq%20'$ResourceSpId'" $null
    $scopeText = (($Scopes | Sort-Object -Unique) -join ' ')
    if ($found.value.Count -eq 0) {
        Invoke-Graph POST '/oauth2PermissionGrants' @{clientId=$ClientSpId;consentType='AllPrincipals';resourceId=$ResourceSpId;scope=$scopeText} | Out-Null
    } elseif ($found.value.Count -eq 1 -and $found.value[0].scope -ne $scopeText) {
        Invoke-Graph PATCH "/oauth2PermissionGrants/$($found.value[0].id)" @{scope=$scopeText} | Out-Null
    } elseif ($found.value.Count -gt 1) { throw 'Duplicate delegated permission grants found' }
}

$azCommand = (Get-Command az.cmd -ErrorAction SilentlyContinue).Source
if (-not $azCommand) {
    $candidate = 'C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin\az.cmd'
    if (Test-Path -LiteralPath $candidate) { $azCommand = $candidate }
}
if (-not $azCommand) { throw 'Azure CLI is required. Install Microsoft.AzureCLI first.' }
Set-Alias -Name az -Value $azCommand -Scope Script -WhatIf:$false -Confirm:$false
& az account show --output none 2>$null
$needsLogin = $LASTEXITCODE -ne 0
if (-not $needsLogin) {
    & az account get-access-token --resource-type ms-graph --output none 2>$null
    $needsLogin = $LASTEXITCODE -ne 0
}
if ($needsLogin) {
    Write-Host 'Azure CLI needs a fresh Microsoft Graph session. Complete device-code sign-in in your browser; do not paste the token anywhere.'
    & az login --use-device-code --tenant $TenantId --allow-no-subscriptions --output none
    if ($LASTEXITCODE -ne 0) { throw 'Azure CLI login failed' }
}
$currentTenant = (& az account show --query tenantId --output tsv).Trim()
if ($currentTenant -ne $TenantId) {
    Write-Host "Switching Azure CLI to tenant $TenantId through device code."
    & az login --use-device-code --tenant $TenantId --allow-no-subscriptions --output none
    if ($LASTEXITCODE -ne 0) { throw 'Azure CLI tenant login failed' }
    $currentTenant = (& az account show --query tenantId --output tsv).Trim()
}
if ($currentTenant -ne $TenantId) { throw "Azure CLI is signed into tenant $currentTenant, not $TenantId" }

$state = @{}
if (Test-Path -LiteralPath $StatePath) {
    $loaded = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json -AsHashtable
    foreach ($key in $loaded.Keys) { $state[$key] = $loaded[$key] }
}
$state.tenantId = $TenantId
foreach ($idName in @('gatewayReadScopeId','gatewayControlScopeId','energyReadScopeId','energyControlScopeId','energyReadRoleId','readerRoleId','operatorRoleId','energyOperatorRoleId')) {
    if (-not $state[$idName]) { $state[$idName] = [guid]::NewGuid().ToString() }
}
$controlPermissionsEnabled = [bool]($EnableControlPermissions -or $state.controlPermissionsEnabled)
if ($controlPermissionsEnabled) { $state.controlPermissionsEnabled = $true }

$vscode = Ensure-Application 'EG4 Lab VS Code Client' @{isFallbackPublicClient=$true;publicClient=@{redirectUris=@('http://localhost','http://127.0.0.1:33418/')}} $state vscode
$gateway = Ensure-Application 'EG4 Lab MCP Gateway' @{} $state gateway
$energy = Ensure-Application 'EG4 Lab Energy API' @{} $state energy
$automation = Ensure-Application 'EG4 Lab Automation Reader' @{} $state automation

# A previous provisioning attempt can be interrupted after Graph creates enabled
# entitlements but before the public state file is written. Reuse those immutable
# IDs on the next run instead of trying to replace enabled scopes or roles.
foreach ($mapping in @(
    @($gateway.api.oauth2PermissionScopes, 'Mcp.Read', 'gatewayReadScopeId'),
    @($gateway.api.oauth2PermissionScopes, 'Mcp.Control', 'gatewayControlScopeId'),
    @($energy.api.oauth2PermissionScopes, 'Energy.Read', 'energyReadScopeId'),
    @($energy.api.oauth2PermissionScopes, 'Energy.Control', 'energyControlScopeId'),
    @($energy.appRoles, 'Energy.Read.All', 'energyReadRoleId'),
    @($gateway.appRoles, 'Reader', 'readerRoleId'),
    @($gateway.appRoles, 'Operator', 'operatorRoleId'),
    @($energy.appRoles, 'Operator', 'energyOperatorRoleId')
)) {
    $existingEntitlement = @($mapping[0] | Where-Object { $_.value -eq $mapping[1] })
    if ($existingEntitlement.Count -gt 1) { throw "Duplicate entitlement value: $($mapping[1])" }
    if ($existingEntitlement.Count -eq 1) { $state[$mapping[2]] = $existingEntitlement[0].id }
}

$gatewayScopes = @(
 @{id=$state.gatewayReadScopeId;value='Mcp.Read';type='Admin';isEnabled=$true;adminConsentDisplayName='Read EG4 MCP data';adminConsentDescription='Read EG4 data through the MCP gateway on behalf of a signed-in user.';userConsentDisplayName='Read EG4 MCP data';userConsentDescription='Read EG4 data through the MCP gateway on your behalf.'}
)
$energyScopes = @(
 @{id=$state.energyReadScopeId;value='Energy.Read';type='Admin';isEnabled=$true;adminConsentDisplayName='Read energy data';adminConsentDescription='Read energy data on behalf of a signed-in user.';userConsentDisplayName='Read energy data';userConsentDescription='Read energy data on your behalf.'}
)
if ($controlPermissionsEnabled) {
 $gatewayScopes += @{id=$state.gatewayControlScopeId;value='Mcp.Control';type='Admin';isEnabled=$true;adminConsentDisplayName='Control EG4 through MCP';adminConsentDescription='Plan and commit bounded EG4 controls through MCP on behalf of a signed-in operator.';userConsentDisplayName='Control EG4 through MCP';userConsentDescription='Plan and commit bounded EG4 controls on your behalf.'}
 $energyScopes += @{id=$state.energyControlScopeId;value='Energy.Control';type='Admin';isEnabled=$true;adminConsentDisplayName='Control energy system';adminConsentDescription='Plan and commit bounded energy controls on behalf of a signed-in operator.';userConsentDisplayName='Control energy system';userConsentDescription='Plan and commit bounded controls on your behalf.'}
}
$userRoles = @(
 @{id=$state.readerRoleId;value='Reader';displayName='Reader';description='May invoke read-only MCP operations.';allowedMemberTypes=@('User');isEnabled=$true},
 @{id=$state.operatorRoleId;value='Operator';displayName='Operator';description='May invoke bounded control operations when delegated scopes and server gates also permit them.';allowedMemberTypes=@('User');isEnabled=$true}
)
$energyAppRoles = @(
 @{id=$state.energyReadRoleId;value='Energy.Read.All';displayName='Read permitted energy data';description='Read permitted energy data without a signed-in user.';allowedMemberTypes=@('Application');isEnabled=$true},
 @{id=$state.energyOperatorRoleId;value='Operator';displayName='Energy operator';description='May invoke bounded delegated energy controls.';allowedMemberTypes=@('User');isEnabled=$true}
)
$gatewayPermissionAccess = @(@{id=$state.gatewayReadScopeId;type='Scope'})
$gatewayPreAuthorizedScopeIds = @($state.gatewayReadScopeId)
$energyPermissionAccess = @(@{id=$state.energyReadScopeId;type='Scope'})
$energyPreAuthorizedScopeIds = @($state.energyReadScopeId)
if ($controlPermissionsEnabled) {
 $gatewayPermissionAccess += @{id=$state.gatewayControlScopeId;type='Scope'}
 $gatewayPreAuthorizedScopeIds += $state.gatewayControlScopeId
 $energyPermissionAccess += @{id=$state.energyControlScopeId;type='Scope'}
 $energyPreAuthorizedScopeIds += $state.energyControlScopeId
}

$energyManifest = @{
 identifierUris=@("api://$($energy.appId)");
 api=@{requestedAccessTokenVersion=2;oauth2PermissionScopes=$energyScopes};
 appRoles=$energyAppRoles
}
$energy = Ensure-Application 'EG4 Lab Energy API' $energyManifest $state energy
$gatewayManifest = @{
 identifierUris=@("api://$($gateway.appId)");
 api=@{requestedAccessTokenVersion=2;oauth2PermissionScopes=$gatewayScopes};
 appRoles=$userRoles;
 requiredResourceAccess=@(@{resourceAppId=$energy.appId;resourceAccess=$energyPermissionAccess})
}
$gateway = Ensure-Application 'EG4 Lab MCP Gateway' $gatewayManifest $state gateway
$vscodeRedirectUris = @(
 'http://localhost',
 'http://127.0.0.1:33418/',
 'https://vscode.dev/redirect',
 "ms-appx-web://Microsoft.AAD.BrokerPlugin/$($vscode.appId)"
)
$vscode = Ensure-Application 'EG4 Lab VS Code Client' @{
 isFallbackPublicClient=$true;publicClient=@{redirectUris=$vscodeRedirectUris};
 requiredResourceAccess=@(@{resourceAppId=$gateway.appId;resourceAccess=$gatewayPermissionAccess})
} $state vscode
$energyManifest.api.preAuthorizedApplications = @(@{appId=$gateway.appId;delegatedPermissionIds=$energyPreAuthorizedScopeIds})
$energy = Ensure-Application 'EG4 Lab Energy API' $energyManifest $state energy
$gatewayManifest.api.preAuthorizedApplications = @(@{appId=$vscode.appId;delegatedPermissionIds=$gatewayPreAuthorizedScopeIds})
$gateway = Ensure-Application 'EG4 Lab MCP Gateway' $gatewayManifest $state gateway
$automation = Ensure-Application 'EG4 Lab Automation Reader' @{
 requiredResourceAccess=@(@{resourceAppId=$energy.appId;resourceAccess=@(@{id=$state.energyReadRoleId;type='Role'})})
} $state automation

$vscodeSp = Ensure-ServicePrincipal $vscode.appId $state vscode
$gatewaySp = Ensure-ServicePrincipal $gateway.appId $state gateway
$energySp = Ensure-ServicePrincipal $energy.appId $state energy
$automationSp = Ensure-ServicePrincipal $automation.appId $state automation

if ($GrantReadAdminConsent) {
    $gatewayGrantScopes = @('Mcp.Read')
    $energyGrantScopes = @('Energy.Read')
    if ($controlPermissionsEnabled) {
        $gatewayGrantScopes += 'Mcp.Control'
        $energyGrantScopes += 'Energy.Control'
    }
    Ensure-DelegatedGrant $vscodeSp.id $gatewaySp.id $gatewayGrantScopes
    Ensure-DelegatedGrant $gatewaySp.id $energySp.id $energyGrantScopes
}
if (($OperatorPrincipalId.Count -gt 0) -and -not $controlPermissionsEnabled) {
    throw 'Operator assignments require control permissions to be enabled first'
}
foreach ($principalId in $ReaderPrincipalId) {
    Ensure-AppRoleAssignment $principalId $gatewaySp.id $state.readerRoleId
}
foreach ($principalId in $OperatorPrincipalId) {
    Ensure-AppRoleAssignment $principalId $gatewaySp.id $state.operatorRoleId
    Ensure-AppRoleAssignment $principalId $energySp.id $state.energyOperatorRoleId
}
if ($CreateAutomationAssignment) {
    Ensure-AppRoleAssignment $automationSp.id $energySp.id $state.energyReadRoleId
}
if ($UploadGatewayCertificate) {
    if (-not (Test-Path -LiteralPath $GatewayCertificatePath -PathType Leaf)) {
        throw "Gateway public certificate was not found at $GatewayCertificatePath"
    }
    $certificate = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($GatewayCertificatePath)
    $thumbprint = $certificate.Thumbprint.ToUpperInvariant()
    $customKeyIdentifier = [Convert]::ToBase64String($certificate.GetCertHash())
    $registered = @($gateway.keyCredentials | Where-Object { $_.customKeyIdentifier -eq $customKeyIdentifier }).Count -gt 0
    if (-not $registered) {
        & az ad app credential reset --id $gateway.appId --cert "@$GatewayCertificatePath" --append --display-name 'eg4-obo' --output none
        if ($LASTEXITCODE -ne 0) { throw 'Gateway certificate upload failed' }
    }
    $state.gatewayCertificateThumbprint = $thumbprint
    $state.gatewayCertificateExpiresAt = $certificate.NotAfter.ToUniversalTime().ToString('o')
}

$state | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $StatePath -Encoding utf8NoBOM
Write-Host "Provisioning state written to $StatePath (public identifiers only)."
Write-Host "Gateway client ID: $($gateway.appId)"
Write-Host "Energy API client ID: $($energy.appId)"
Write-Host "VS Code public client ID: $($vscode.appId)"
