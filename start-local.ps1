<#
.SYNOPSIS
    ReportAnchor 로컬 웹앱 원클릭 실행 (Windows PowerShell 5.1 / PowerShell 7 호환)

.DESCRIPTION
    1. frontend/dist가 없거나 -RebuildFrontend면 프론트엔드를 빌드한다(필요 시 npm install).
    2. backend/.venv가 없으면 uv sync로 설치한다.
    3. 이미 같은 포트에서 실행 중이면 브라우저만 열고 종료한다.
    4. 아니면 단일 Python 데몬을 포그라운드로 실행한다(종료: Ctrl+C).

.EXAMPLE
    .\start-local.ps1
    .\start-local.ps1 -Port 8800 -NoBrowser
    .\start-local.ps1 -RebuildFrontend
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$NoBrowser,
    [switch]$RebuildFrontend
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Frontend = Join-Path $Root 'frontend'
$Backend = Join-Path $Root 'backend'
$Python = Join-Path $Backend '.venv\Scripts\python.exe'
$Url = "http://127.0.0.1:$Port"
$HealthUrl = "$Url/api/health"

function Test-ReportAnchorHealth {
    param([string]$Target)
    try {
        $health = Invoke-RestMethod -Uri $Target -TimeoutSec 2 -ErrorAction Stop
        return ($health.ok -eq $true)
    } catch {
        return $false
    }
}

function Invoke-Checked {
    param([string]$Step, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Step 단계가 실패했습니다 (종료 코드 $LASTEXITCODE)."
    }
}

# 0. 이미 실행 중이면 브라우저만 연다
if (Test-ReportAnchorHealth $HealthUrl) {
    Write-Host "ReportAnchor가 이미 실행 중입니다: $Url"
    if (-not $NoBrowser) { Start-Process $Url }
    exit 0
}

# 1. 프론트엔드 빌드
$IndexHtml = Join-Path $Frontend 'dist\index.html'
if ($RebuildFrontend -or -not (Test-Path $IndexHtml)) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw 'npm을 찾을 수 없습니다. Node.js를 설치한 뒤 다시 실행하세요.'
    }
    Push-Location $Frontend
    try {
        if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) {
            Write-Host '프론트엔드 의존성을 설치합니다 (npm install)...'
            Invoke-Checked 'npm install' { npm install }
        }
        Write-Host '프론트엔드를 빌드합니다 (npm run build)...'
        Invoke-Checked 'npm run build' { npm run build }
    } finally {
        Pop-Location
    }
}

# 2. 백엔드 가상환경
if (-not (Test-Path $Python)) {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'uv를 찾을 수 없습니다. uv를 설치한 뒤 다시 실행하세요.'
    }
    Push-Location $Backend
    try {
        Write-Host '백엔드 의존성을 설치합니다 (uv sync)...'
        Invoke-Checked 'uv sync' { uv sync }
    } finally {
        Pop-Location
    }
}

# 3. 준비되면 브라우저를 여는 가벼운 백그라운드 헬퍼
$Opener = $null
if (-not $NoBrowser) {
    $Opener = Start-Job -ArgumentList $HealthUrl, $Url -ScriptBlock {
        param($Health, $Target)
        for ($i = 0; $i -lt 120; $i++) {
            try {
                $h = Invoke-RestMethod -Uri $Health -TimeoutSec 2 -ErrorAction Stop
                if ($h.ok -eq $true) { Start-Process $Target; return }
            } catch { }
            Start-Sleep -Milliseconds 500
        }
    }
}

# 4. 데몬 포그라운드 실행
Write-Host "ReportAnchor 로컬 웹앱: $Url (종료: Ctrl+C)"
$PreviousPort = $env:REPORTANCHOR_PORT
$env:REPORTANCHOR_PORT = [string]$Port
Push-Location $Backend
try {
    & $Python -m reportanchor
} finally {
    Pop-Location
    $env:REPORTANCHOR_PORT = $PreviousPort
    if ($Opener) { Remove-Job -Job $Opener -Force -ErrorAction SilentlyContinue }
}
