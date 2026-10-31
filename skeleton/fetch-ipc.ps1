<#
.SYNOPSIS
  Fetches the stintmate-ipc wheel and C header of one release into vendor\ (next to this script).

.DESCRIPTION
  The transport (Stintmate IPC) ships as release assets of the stintmate-ipc repository:
  the wheel (the Python binding with its native DLL), stintmate_ipc.h and SHA256SUMS. The
  skeleton's pyproject.toml installs the wheel from vendor\ in the connector kit. Both files are
  verified against SHA256SUMS; on a mismatch nothing is kept and the exit code is 1. When vendor\
  already holds verified files nothing is downloaded.

  STINTMATE_IPC_RELEASE, when set, replaces the source: an http(s) URL used as the base of the
  three files, or the path of a local folder that holds them.
#>
$ErrorActionPreference = 'Stop'

$IpcVersion = "0.3.1"
$wheel = "stintmate_ipc-$IpcVersion-py3-none-win_amd64.whl"
$header = 'stintmate_ipc.h'
$sums = 'SHA256SUMS'
$vendor = Join-Path $PSScriptRoot 'vendor'

$base = $env:STINTMATE_IPC_RELEASE
if ([string]::IsNullOrWhiteSpace($base)) {
    $base = "https://github.com/Stintmate-App/stintmate-ipc/releases/download/v$IpcVersion/"
}
$isUrl = $base -match '^https?://'

function Get-ExpectedHash([string]$sumsFile, [string]$name) {
    foreach ($line in Get-Content -LiteralPath $sumsFile) {
        if ($line -match '^([0-9a-fA-F]{64}) [ *](.+)$' -and $Matches[2].Trim() -eq $name) {
            return $Matches[1].ToLowerInvariant()
        }
    }
    return $null
}

function Test-Verified([string]$dir) {
    $sumsFile = Join-Path $dir $sums
    foreach ($name in @($wheel, $header)) {
        $path = Join-Path $dir $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
        if (-not (Test-Path -LiteralPath $sumsFile -PathType Leaf)) { return $false }
        $want = Get-ExpectedHash $sumsFile $name
        if (-not $want -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $want) {
            return $false
        }
    }
    return $true
}

if (Test-Verified $vendor) {
    Write-Host "stintmate-ipc ${IpcVersion}: vendor\ is up to date"
    return
}

New-Item -ItemType Directory -Force -Path $vendor | Out-Null
try {
    foreach ($name in @($wheel, $header, $sums)) {
        $dest = Join-Path $vendor $name
        if ($isUrl) {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -UseBasicParsing -Uri ($base.TrimEnd('/') + '/' + $name) -OutFile $dest
        } else {
            $src = Join-Path $base $name
            if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { throw "not found: $src" }
            Copy-Item -LiteralPath $src -Destination $dest -Force
        }
    }
    foreach ($name in @($wheel, $header)) {
        $want = Get-ExpectedHash (Join-Path $vendor $sums) $name
        if (-not $want) { throw "$sums has no entry for $name" }
        $got = (Get-FileHash -LiteralPath (Join-Path $vendor $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($got -ne $want) { throw "SHA-256 mismatch for ${name}: expected $want, got $got" }
    }
} catch {
    foreach ($name in @($wheel, $header, $sums)) {
        Remove-Item -LiteralPath (Join-Path $vendor $name) -Force -ErrorAction SilentlyContinue
    }
    [Console]::Error.WriteLine("fetch-ipc: stintmate-ipc $IpcVersion could not be fetched from ${base}: $($_.Exception.Message)")
    exit 1
}
Write-Host "stintmate-ipc ${IpcVersion}: fetched the wheel and header into vendor\ from $base"
