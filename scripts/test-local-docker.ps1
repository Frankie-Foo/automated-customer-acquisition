param(
    [switch]$SkipUnitTests
)

$ErrorActionPreference = "Stop"
$ProjectName = "salesbot-ci"
$ComposeFile = "deployment/docker-compose.ci.yml"

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker compose -p $ProjectName -f $ComposeFile @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed: $($Arguments -join ' ')"
    }
}

function Show-FailureContext {
    & docker compose -p $ProjectName -f $ComposeFile ps
    & docker compose -p $ProjectName -f $ComposeFile logs --no-color --tail 200
}

function Remove-TestStack {
    $PreviousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & docker compose -p $ProjectName -f $ComposeFile down --volumes --remove-orphans 2>&1 | Out-Null
    } finally {
        $ErrorActionPreference = $PreviousErrorPreference
    }
}

if (-not $SkipUnitTests) {
    & .\.venv\Scripts\python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }

    & npm --prefix frontend run check
    if ($LASTEXITCODE -ne 0) { throw "frontend build check failed" }
}

try {
    Remove-TestStack
    Invoke-Compose -Arguments @("config", "--quiet")
    Invoke-Compose -Arguments @("build")
    Invoke-Compose -Arguments @("up", "-d")

    $Ready = $false
    for ($Attempt = 1; $Attempt -le 60; $Attempt++) {
        try {
            $Response = Invoke-RestMethod -Uri "http://127.0.0.1:18765/api/live" -TimeoutSec 3
            if ($Response) {
                $Ready = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }

    if (-not $Ready) {
        Show-FailureContext
        throw "salesbot Docker smoke test timed out"
    }

    Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:18765/" -TimeoutSec 5 | Out-Null
    Write-Host "salesbot Docker smoke test passed" -ForegroundColor Green
} finally {
    Remove-TestStack
}
