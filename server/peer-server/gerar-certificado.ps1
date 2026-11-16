# Gera um certificado TLS AUTOASSINADO para testes em rede local (HTTPS/WSS).
# Em produção use um certificado válido (ex.: Let's Encrypt) para o domínio público.
#
# Uso:  powershell -ExecutionPolicy Bypass -File gerar-certificado.ps1 -Nome 192.168.0.10 -Senha "troque-esta-senha"
# Depois, em config.json:  "tls": { "pfx": "certs/server.pfx", "pfxPassphrase": "troque-esta-senha" }
param(
  [Parameter(Mandatory = $true)][string]$Nome,
  [Parameter(Mandatory = $true)][string]$Senha
)
$ErrorActionPreference = "Stop"
$dir = Join-Path $PSScriptRoot "certs"
New-Item -ItemType Directory -Force $dir | Out-Null
$isIp = [System.Net.IPAddress]::TryParse($Nome, [ref]$null)
$san = if ($isIp) { "IPAddress=$Nome" } else { "DNS=$Nome" }
$cert = New-SelfSignedCertificate -Subject "CN=$Nome" -TextExtension @("2.5.29.17={text}$san&DNS=localhost") `
  -CertStoreLocation "Cert:\CurrentUser\My" -KeyExportPolicy Exportable -NotAfter (Get-Date).AddYears(1) `
  -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256
$pfx = Join-Path $dir "server.pfx"
Export-PfxCertificate -Cert $cert -FilePath $pfx -Password (ConvertTo-SecureString $Senha -AsPlainText -Force) | Out-Null
Remove-Item "Cert:\CurrentUser\My\$($cert.Thumbprint)"
Write-Host "Certificado gerado: $pfx"
Write-Host "Os navegadores vão alertar que o certificado não é confiável (autoassinado): aceite apenas em testes."
