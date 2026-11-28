param([switch]$RestartBackend)

$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path -Parent $PSScriptRoot
$demoLogs = Join-Path $demoRoot 'tmp'
New-Item -ItemType Directory -Force -Path $demoLogs | Out-Null

$backendListener = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
if ($backendListener -and $RestartBackend) {
    $backendProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($backendListener[0].OwningProcess)"
    if ($backendProcess.CommandLine -notmatch 'uvicorn.+backend.api.main:app') {
        throw 'Port 8000 is occupied by another application; it was not stopped.'
    }
    Stop-Process -Id $backendProcess.ProcessId -Force
    $backendListener = $null
}

$previousSalesFlag = $env:DEMO_SALES_ENABLED
$previousConsultationFlag = $env:CUSTOMER_CONSULTATION_ENABLED
$previousReviewFlag = $env:CUSTOMER_CONSULTATION_REVIEW_ENABLED
$previousLocalFallbackFlag = $env:CUSTOMER_CONSULTATION_LOCAL_FALLBACK_ENABLED
$previousAppEnvironment = $env:APP_ENV
$previousVietnameseModelDir = $env:VIETNAMESE_ASR_MODEL_DIR
$previousVietnameseVerification = $env:VIETNAMESE_ASR_VERIFY_UNCERTAIN
try {
    $env:APP_ENV = 'development'
    $env:DEMO_SALES_ENABLED = 'true'
    $env:CUSTOMER_CONSULTATION_ENABLED = 'true'
    $env:CUSTOMER_CONSULTATION_REVIEW_ENABLED = 'true'
    $env:CUSTOMER_CONSULTATION_LOCAL_FALLBACK_ENABLED = 'true'
    $cachedVietnameseModel = Join-Path $demoRoot '.tmp\vi_asr_20261105\sherpa-onnx-zipformer-vi-30M-int8-2026-02-09'
    $localAsrArguments = @()
    if (Test-Path -LiteralPath (Join-Path $cachedVietnameseModel 'encoder.int8.onnx')) {
        $env:VIETNAMESE_ASR_MODEL_DIR = $cachedVietnameseModel
        $env:VIETNAMESE_ASR_VERIFY_UNCERTAIN = 'true'
        $localAsrArguments = @('--extra', 'local-asr')
    }
    Push-Location $demoRoot
    try {
        & uv run python -m scripts.seed_demo_sales
        if ($LASTEXITCODE -ne 0) { throw 'Sales seed failed.' }
    } finally { Pop-Location }
    if (-not $backendListener) {
        $backendArguments = @('run') + $localAsrArguments + @('uvicorn','backend.api.main:app','--host','127.0.0.1','--port','8000')
        Start-Process -FilePath 'uv' -ArgumentList $backendArguments -WorkingDirectory $demoRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $demoLogs 'demo-backend.log') -RedirectStandardError (Join-Path $demoLogs 'demo-backend.err.log')
    }
    if (-not (Get-NetTCPConnection -State Listen -LocalPort 5173 -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath 'npm.cmd' -ArgumentList @('--prefix','apps/kiosk-airi','run','dev','--','--host','127.0.0.1','--port','5173','--strictPort') -WorkingDirectory $demoRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $demoLogs 'demo-frontend.log') -RedirectStandardError (Join-Path $demoLogs 'demo-frontend.err.log')
    }
} finally {
    $env:APP_ENV = $previousAppEnvironment
    $env:DEMO_SALES_ENABLED = $previousSalesFlag
    $env:CUSTOMER_CONSULTATION_ENABLED = $previousConsultationFlag
    $env:CUSTOMER_CONSULTATION_REVIEW_ENABLED = $previousReviewFlag
    $env:CUSTOMER_CONSULTATION_LOCAL_FALLBACK_ENABLED = $previousLocalFallbackFlag
    $env:VIETNAMESE_ASR_MODEL_DIR = $previousVietnameseModelDir
    $env:VIETNAMESE_ASR_VERIFY_UNCERTAIN = $previousVietnameseVerification
}
foreach ($demoEndpoint in @('http://127.0.0.1:8000/health', 'http://127.0.0.1:5173/')) {
    $demoReady = $false
    for ($demoAttempt = 0; $demoAttempt -lt 12; $demoAttempt++) {
        try {
            $demoResponse = Invoke-WebRequest -Uri $demoEndpoint -UseBasicParsing -TimeoutSec 2
            if ($demoResponse.StatusCode -eq 200) {
                $demoReady = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $demoReady) {
        throw "Kiosk service did not become ready at $demoEndpoint. Check logs in $demoLogs."
    }
}
Write-Output 'Kiosk: http://127.0.0.1:5173/ | API: http://127.0.0.1:8000/health'
