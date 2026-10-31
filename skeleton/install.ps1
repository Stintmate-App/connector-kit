<#
.SYNOPSIS
  Installs (or uninstalls) the skeleton connector on this machine.

.DESCRIPTION
  1. Creates the connector's own Python environment inside its install folder,
     <Destination>\.venv, and installs this project into it (the hub never provides a Python
     for a connector).
  2. Writes %LOCALAPPDATA%\Stintmate\connectors\skeleton.json with the ABSOLUTE launcher. That
     file is all the hub needs; how it gets there is the connector's business. This script is
     this connector's way. It needs no Stintmate workspace: the connector's logs and data go
     to <workspace>\connectors\skeleton\ when the hub runs it.

  Needs: uv (https://docs.astral.sh/uv/) only (it installs Python 3.12 itself; no git, no Rust).
  First it runs fetch-ipc.ps1, which puts the stintmate-ipc wheel in vendor (STINTMATE_IPC_RELEASE
  points it at a local folder). Run it again to update; it replaces the environment's copy
  and the descriptor.

  STINTMATE_CONNECTORS_DIR, when set, replaces the descriptors folder (tests).

.PARAMETER Destination
  The install folder (default: %LOCALAPPDATA%\Programs\Stintmate Skeleton Connector).

.PARAMETER SkipInstall
  Write only the descriptor (no environment). Used by the tests.

.PARAMETER Uninstall
  Remove skeleton.json, only if its launcher is under -Destination, then the destination folder.
#>
param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'Programs\Stintmate Skeleton Connector'),
    [switch]$SkipInstall,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'

$id = 'skeleton'
$source = $PSScriptRoot
$connectors = $env:STINTMATE_CONNECTORS_DIR
if ([string]::IsNullOrWhiteSpace($connectors)) {
    $connectors = Join-Path $env:LOCALAPPDATA 'Stintmate\connectors'
}
$target = Join-Path $connectors "$id.json"
$destFull = [System.IO.Path]::GetFullPath($Destination).TrimEnd('\')

if ($Uninstall) {
    # Remove the descriptor only when it names THIS install (another build may have replaced it).
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        $launcherOf = $null
        try { $launcherOf = (Get-Content -LiteralPath $target -Raw -Encoding UTF8 | ConvertFrom-Json).launcher } catch { }
        $under = $launcherOf -and ([System.IO.Path]::GetFullPath(($launcherOf -replace '/', '\')) + '\').StartsWith(
            $destFull + '\', [System.StringComparison]::OrdinalIgnoreCase)
        if ($under) {
            Remove-Item -LiteralPath $target -Force
            Write-Host "Removed: $target"
        } else {
            Write-Host "Left as it is (it names another install): $target"
        }
    }
    if (Test-Path -LiteralPath $destFull) { Remove-Item -LiteralPath $destFull -Recurse -Force }
    Write-Host "Uninstalled: $destFull"
    return
}

New-Item -ItemType Directory -Force -Path $destFull | Out-Null
$venv = Join-Path $destFull '.venv'
$launcher = Join-Path $venv 'Scripts\skeleton-connector.exe'

if (-not $SkipInstall) {
    & (Join-Path $source 'fetch-ipc.ps1')
    if (-not $?) { throw 'fetch-ipc.ps1 failed' }
    uv venv --python 3.12 $venv
    if ($LASTEXITCODE -ne 0) { throw 'uv venv failed' }
    # A normal (not editable) install: the environment holds a copy, so the connector keeps
    # working if this source folder goes away.
    uv pip install --python (Join-Path $venv 'Scripts\python.exe') $source
    if ($LASTEXITCODE -ne 0) { throw 'uv pip install failed' }
    if (-not (Test-Path -LiteralPath $launcher)) { throw "The launcher was not created: $launcher" }
}

$version = (Select-String -Path (Join-Path $source 'pyproject.toml') `
    -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
$descriptor = [ordered]@{
    schema           = 1
    id               = $id
    name             = 'Skeleton connector'
    sim              = 'skeletonsim'
    sim_name         = 'Skeleton Simulator'
    version          = $version
    launcher         = $launcher            # absolute
    args             = @()
    control_protocol = 'stintmate-ipc/2'
    endpoint         = "connector-$id"
    mutex            = 'Local\SkeletonConnector.Control'
    services         = @(@{ id = 'recorder'; name = 'Recorder' })
    logs             = @('control.log')     # in <workspace>\connectors\skeleton\
    written_at       = [DateTimeOffset]::Now.ToString('yyyy-MM-ddTHH:mm:ssK')
    written_by       = "skeleton-connector install.ps1 $version"
}
# UTF-8 without BOM, written to a temporary file and moved into place (atomic).
$json = $descriptor | ConvertTo-Json -Depth 5
New-Item -ItemType Directory -Force -Path $connectors | Out-Null
$tmp = "$target.tmp"
[System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding $false))
Move-Item -LiteralPath $tmp -Destination $target -Force
Write-Host "Installed: $target"
