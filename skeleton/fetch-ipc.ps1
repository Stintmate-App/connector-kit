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

  STINTMATE_IPC_READ_TOKEN, when set, is a GitHub token that can read the release (needed only
  while the repository is private). The assets are then downloaded through the GitHub API; the
  token is sent to api.github.com only and never printed.
#>
$ErrorActionPreference = 'Stop'

$IpcVersion = "0.3.1"
$wheel = "stintmate_ipc-$IpcVersion-py3-none-win_amd64.whl"
$sums = 'SHA256SUMS'
$vendor = Join-Path $PSScriptRoot 'vendor'
$repo = 'Stintmate-App/stintmate-ipc-python'

$base = $env:STINTMATE_IPC_RELEASE
$token = $env:STINTMATE_IPC_READ_TOKEN
$useApi = ([string]::IsNullOrWhiteSpace($base)) -and (-not [string]::IsNullOrWhiteSpace($token))
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
    $assets = $null
    if ($useApi) {
        $headers = @{ Authorization = "Bearer $token"; Accept = 'application/vnd.github+json'; 'X-GitHub-Api-Version' = '2022-11-28' }
        $release = Invoke-RestMethod -UseBasicParsing -Headers $headers `
            -Uri "https://api.github.com/repos/$repo/releases/tags/v$IpcVersion"
        $assets = @{}
        foreach ($a in $release.assets) { $assets[$a.name] = $a.url }
    }
    foreach ($name in @($wheel, $sums)) {
        $dest = Join-Path $vendor $name
        if ($assets) {
            if (-not $assets.ContainsKey($name)) { throw "release v$IpcVersion has no asset $name" }
            $h = @{ Authorization = "Bearer $token"; Accept = 'application/octet-stream' }
            Invoke-WebRequest -UseBasicParsing -Headers $h -Uri $assets[$name] -OutFile $dest
        } elseif ($isUrl) {
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
