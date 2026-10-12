
# Uploads model API keys and GitHub token from the local .env
# to AWS SSM Parameter Store as SecureString parameters.
#
# Usage:
# powershell -File scripts\put_ssm_secrets.ps1 -Env dev
#
# Run locally. Never commit .env or share secrets in chat.

param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('dev', 'prod')]
    [string]$Env,

    [string]$Region = 'us-east-1',

    [string]$EnvFile = ''
)

$ErrorActionPreference = 'Stop'

# Resolve .env relative to the repository root.
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $repoRoot = Split-Path -Parent $scriptDir
    $EnvFile = Join-Path $repoRoot '.env'
}

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Environment file not found: $EnvFile"
}

$EnvFile = (Resolve-Path -LiteralPath $EnvFile).Path

# Verify AWS CLI availability.
if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    throw "AWS CLI is not installed or not available in PATH."
}

# Verify AWS account before writing secrets.
$identityJson = aws sts get-caller-identity `
    --region $Region `
    --output json

if ($LASTEXITCODE -ne 0) {
    throw "AWS authentication failed."
}

$identity = $identityJson | ConvertFrom-Json

$expectedAccount = '822491881190'

if ($identity.Account -ne $expectedAccount) {
    throw "Wrong AWS account. Expected $expectedAccount, got $($identity.Account)."
}

Write-Host "AWS account verified: $($identity.Account)"
Write-Host "AWS region: $Region"
Write-Host "Environment: $Env"

$prefix = "/unfold/$Env"

# Read environment variables.
$values = @{}

foreach ($line in Get-Content -LiteralPath $EnvFile) {
    $trimmed = $line.Trim()

    if (
        $trimmed.Length -eq 0 -or
        $trimmed.StartsWith('#')
    ) {
        continue
    }

    if ($trimmed -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $key = $Matches[1]
        $value = $Matches[2].Trim()

        if ($value.Length -ge 2) {
            if (
                ($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))
            ) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        $values[$key] = $value
    }
}

# Map SSM parameter names to .env variable names.
$map = [ordered]@{
    github_token = 'GITHUB_TOKEN'
}

1..4 | ForEach-Object {
    $map["gemini_api_key_$_"] = "GEMINI_API_KEY_$_"
    $map["groq_api_key_$_"] = "GROQ_API_KEY_$_"
}

# Upload available secrets.
foreach ($name in $map.Keys) {
    $envKey = $map[$name]
    $value = $values[$envKey]

    if ([string]::IsNullOrWhiteSpace($value)) {
        Write-Host "skip   $name (missing $envKey)"
        continue
    }

    $parameterName = "$prefix/$name"

    # Pass secret through a temporary file rather than
    # directly as a command-line argument.
    $tempFile = Join-Path `
        ([System.IO.Path]::GetTempPath()) `
        ([System.IO.Path]::GetRandomFileName())

    try {
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText(
            $tempFile,
            $value,
            $utf8
        )

        aws ssm put-parameter `
            --region $Region `
            --name $parameterName `
            --type SecureString `
            --value "file://$tempFile" `
            --overwrite `
            --output json | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "Failed to store $parameterName"
        }

        Write-Host "stored $parameterName"
    }
    finally {
        Remove-Item -LiteralPath $tempFile `
            -Force -ErrorAction SilentlyContinue
    }
}

# Check whether the HMAC secret already exists.
$hmacName = "$prefix/proxy_hmac_secret"

aws ssm get-parameter `
    --region $Region `
    --name $hmacName `
    --output json 2>$null | Out-Null

$hmacExists = ($LASTEXITCODE -eq 0)

if (-not $hmacExists) {
    # Generate a cryptographically secure 48-byte secret.
    $bytes = New-Object byte[] 48
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()

    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }

    $secret = [Convert]::ToBase64String($bytes)

    # Create the secret without overwriting an existing value.
    $tempFile = Join-Path `
        ([System.IO.Path]::GetTempPath()) `
        ([System.IO.Path]::GetRandomFileName())

    try {
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText(
            $tempFile,
            $secret,
            $utf8
        )

        aws ssm put-parameter `
            --region $Region `
            --name $hmacName `
            --type SecureString `
            --value "file://$tempFile" `
            --output json | Out-Null

        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create $hmacName"
        }

        Write-Host ""
        Write-Host "Created $hmacName"
        Write-Host "Set this value in Vercel as PROXY_HMAC_SECRET:"
        Write-Host $secret
    }
    finally {
        Remove-Item -LiteralPath $tempFile `
            -Force -ErrorAction SilentlyContinue
    }
}
else {
    Write-Host "kept   $hmacName (already exists)"
}

Write-Host ""
Write-Host "SSM secret configuration completed."
