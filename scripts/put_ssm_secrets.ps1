# Copies the model keys and the GitHub token from the git-ignored .env into SSM Parameter Store (SecureString) for
# one environment, and creates proxy_hmac_secret when it does not exist yet. Run it yourself: it reads secrets, so
# an agent should not.
#
#   powershell -File scripts\put_ssm_secrets.ps1 -Env dev
#
# The generated proxy_hmac_secret is printed once; paste the same value into the Vercel project as
# PROXY_HMAC_SECRET (Production and Preview). Re-running keeps an existing proxy_hmac_secret.
param(
    [Parameter(Mandatory = $true)][ValidateSet('dev', 'prod')][string]$Env,
    [string]$Region = 'us-east-1',
    [string]$EnvFile = (Join-Path $PSScriptRoot '..\.env')
)
$ErrorActionPreference = 'Stop'
$prefix = "/unfold/$Env"
$values = @{}
foreach ($line in Get-Content $EnvFile) {
    if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.+?)\s*$') { $values[$Matches[1]] = $Matches[2].Trim('"').Trim("'") }
}
$map = [ordered]@{ github_token = 'GITHUB_TOKEN' }
1..4 | ForEach-Object { $map["gemini_api_key_$_"] = "GEMINI_API_KEY_$_"; $map["groq_api_key_$_"] = "GROQ_API_KEY_$_" }
foreach ($name in $map.Keys) {
    $v = $values[$map[$name]]
    if (-not $v) { Write-Host "skip   $name (no $($map[$name]) in .env)"; continue }
    aws ssm put-parameter --region $Region --name "$prefix/$name" --type SecureString --value $v --overwrite | Out-Null
    Write-Host "stored $prefix/$name"
}
aws ssm get-parameter --region $Region --name "$prefix/proxy_hmac_secret" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $secret = [Convert]::ToBase64String($bytes)
    aws ssm put-parameter --region $Region --name "$prefix/proxy_hmac_secret" --type SecureString --value $secret | Out-Null
    Write-Host "stored $prefix/proxy_hmac_secret (new). Put this value in Vercel as PROXY_HMAC_SECRET:"
    Write-Host $secret
} else {
    Write-Host "kept   $prefix/proxy_hmac_secret (already exists)"
}
