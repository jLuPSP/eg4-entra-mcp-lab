#requires -Version 7.0
[CmdletBinding(SupportsShouldProcess)]
param(
    [string] $SecretsDirectory = (Join-Path $PSScriptRoot '..\secrets'),
    [ValidateRange(30,825)] [int] $Days = 365,
    [string] $CommonName = 'EG4 Lab MCP Gateway OBO'
)

$ErrorActionPreference = 'Stop'
$openssl = (Get-Command openssl.exe -ErrorAction SilentlyContinue).Source
if (-not $openssl) {
    $candidate = 'C:\Program Files\OpenSSL-Win64\bin\openssl.exe'
    if (Test-Path -LiteralPath $candidate) { $openssl = $candidate }
}
if (-not $openssl) { throw 'OpenSSL is required to generate the Gateway certificate.' }

$privateKeyPath = Join-Path $SecretsDirectory 'gateway.pem'
$publicCertificatePath = Join-Path $SecretsDirectory 'gateway.crt.pem'
if ((Test-Path -LiteralPath $privateKeyPath) -or (Test-Path -LiteralPath $publicCertificatePath)) {
    throw 'Gateway certificate files already exist. Move them aside for explicit rotation.'
}
if (-not $PSCmdlet.ShouldProcess($SecretsDirectory, 'Generate Gateway OBO certificate')) { return }
New-Item -ItemType Directory -Path $SecretsDirectory -Force | Out-Null
& $openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days $Days -subj "/CN=$CommonName" -keyout $privateKeyPath -out $publicCertificatePath
if ($LASTEXITCODE -ne 0) { throw 'OpenSSL certificate generation failed' }
& $openssl pkey -in $privateKeyPath -check -noout
if ($LASTEXITCODE -ne 0) { throw 'Generated private key validation failed' }
& $openssl x509 -in $publicCertificatePath -noout -subject -dates -fingerprint -sha1
if ($LASTEXITCODE -ne 0) { throw 'Generated certificate validation failed' }
if ($IsWindows) {
    $grant = $env:USERDOMAIN + '\' + $env:USERNAME + ':(F)'
    & icacls.exe $privateKeyPath '/inheritance:r' '/grant:r' $grant | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to restrict the private key ACL' }
    & icacls.exe $publicCertificatePath '/inheritance:r' '/grant:r' $grant | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to restrict the public certificate ACL' }
}
Write-Host "Private key written to $privateKeyPath (never upload or commit this file)."
Write-Host "Public certificate written to $publicCertificatePath."
