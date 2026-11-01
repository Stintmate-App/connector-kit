<#
.SYNOPSIS
  Fetches the stintmate-ipc wheel of one release into vendor\ (next to this script).

.DESCRIPTION
  The Python binding of Stintmate IPC ships as a release asset of the stintmate-ipc-python
  repository: the wheel (the binding with its native DLL and the stintmate-ipc-check conformance
  tool) and SHA256SUMS. The skeleton's pyproject.toml installs the wheel from vendor\ in the
  connector kit. The wheel is verified against SHA256SUMS; on a mismatch nothing is kept and the
  exit code is 1. When vendor\ already holds the verified wheel nothing is downloaded.

  STINTMATE_IPC_RELEASE, when set, replaces the source: an http(s) URL used as the base of the
  two files, or the path of a local folder that holds them.
#>
$ErrorActionPreference = 'Stop'

$IpcVersion = "0.3.3"
$wheel = "stintmate_ipc-$IpcVersion-py3-none-win_amd64.whl"
$sums = 'SHA256SUMS'
$vendor = Join-Path $PSScriptRoot 'vendor'
$repo = 'Stintmate-App/stintmate-ipc-python'

$base = $env:STINTMATE_IPC_RELEASE
if ([string]::IsNullOrWhiteSpace($base)) {
    $base = "https://github.com/$repo/releases/download/v$IpcVersion/"
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
    $path = Join-Path $dir $wheel
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath $sumsFile -PathType Leaf)) { return $false }
    $want = Get-ExpectedHash $sumsFile $wheel
    if (-not $want -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $want) {
        return $false
    }
    return $true
}

if (Test-Verified $vendor) {
    Write-Host "stintmate-ipc ${IpcVersion}: vendor\ is up to date"
    return
}

New-Item -ItemType Directory -Force -Path $vendor | Out-Null
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    foreach ($name in @($wheel, $sums)) {
        $dest = Join-Path $vendor $name
        if ($isUrl) {
            Invoke-WebRequest -UseBasicParsing -Uri ($base.TrimEnd('/') + '/' + $name) -OutFile $dest
        } else {
            $src = Join-Path $base $name
            if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { throw "not found: $src" }
            Copy-Item -LiteralPath $src -Destination $dest -Force
        }
    }
    $want = Get-ExpectedHash (Join-Path $vendor $sums) $wheel
    if (-not $want) { throw "$sums has no entry for $wheel" }
    $got = (Get-FileHash -LiteralPath (Join-Path $vendor $wheel) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($got -ne $want) { throw "SHA-256 mismatch for ${wheel}: expected $want, got $got" }
} catch {
    foreach ($name in @($wheel, $sums)) {
        Remove-Item -LiteralPath (Join-Path $vendor $name) -Force -ErrorAction SilentlyContinue
    }
    [Console]::Error.WriteLine("fetch-ipc: stintmate-ipc $IpcVersion could not be fetched from ${base}: $($_.Exception.Message)")
    exit 1
}
Write-Host "stintmate-ipc ${IpcVersion}: fetched the wheel into vendor\ from $base"
