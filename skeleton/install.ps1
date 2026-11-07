<#
.SYNOPSIS
  Installs, updates or uninstalls the skeleton connector on this machine.

.DESCRIPTION
  1. Creates the connector's own Python environment inside its install folder,
     <Destination>\.venv, and installs this project into it (the hub never provides a Python
     for a connector).
  2. Copies this script to <Destination>\install.ps1: the descriptor's `uninstall` command runs
     that copy. Copies icon.png to <Destination>\icon.png (the descriptor's `icon`; it goes
     with the folder on -Uninstall).
  3. Writes %LOCALAPPDATA%\Stintmate\connectors\skeleton.json with the ABSOLUTE launcher and the
     connector's product key. If that file already holds ANOTHER product key, the id belongs to
     another connector: the script says so, names it and changes nothing. That file is all the
     hub needs; how it gets there is the connector's business. This script is
     this connector's way. It needs no Stintmate workspace: the connector's logs and data go
     to <workspace>\connectors\skeleton\ when the hub runs it.

  While Stintmate runs, -Update and -Uninstall first ask the hub to stop the connector and keep
  it stopped (docs/connector-protocol.md section 1.2), through skeleton_connector.setup_hold run
  by the installed environment. With no hub they ask the connector to quit by itself. While a
  session is being recorded they exit with code 1 and change nothing.

  Needs: uv (https://docs.astral.sh/uv/) only (it installs Python 3.12 itself; no git, no Rust).
  First it runs fetch-ipc.ps1, which puts the stintmate-ipc wheel in vendor (STINTMATE_IPC_RELEASE
  points it at a local folder). To update, run it again with -Update (while the connector
  runs, only with -Update is it stopped first); the environment is rebuilt in place and the
  descriptor replaced. The update is not atomic: if the install fails, the old build is gone,
  the script says so and exits with a non-zero code. The hold then ends when the script
  releases it, or at its time limit (300 s) when it cannot.

  STINTMATE_CONNECTORS_DIR, when set, replaces the descriptors folder (tests).

.PARAMETER Destination
  The install folder (default: %LOCALAPPDATA%\Programs\Stintmate Skeleton Connector).

.PARAMETER SkipInstall
  Write only the descriptor (no environment). Used by the tests.

.PARAMETER Update
  Install over an existing install: hold the connector first, install, then release it so the
  hub starts the new build.

.PARAMETER Uninstall
  Hold the connector, remove skeleton.json only if its launcher is under -Destination and its
  product key is this connector's, release the connector without starting it, then remove the
  destination folder.
#>
param(
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'Programs\Stintmate Skeleton Connector'),
    [switch]$SkipInstall,
    [switch]$Update,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'

$id = 'skeleton'
# The product key: generated once with [guid]::NewGuid(), never changed, never another
# connector's. Keep it equal to PRODUCT in src\skeleton_connector\__init__.py (a test checks).
$product = '6f1c2a84-3b7e-4d59-9a0c-5e8d7b2f1a43'
$source = $PSScriptRoot
$connectors = $env:STINTMATE_CONNECTORS_DIR
if ([string]::IsNullOrWhiteSpace($connectors)) {
    $connectors = Join-Path $env:LOCALAPPDATA 'Stintmate\connectors'
}
$target = Join-Path $connectors "$id.json"
$destFull = [System.IO.Path]::GetFullPath($Destination).TrimEnd('\')
$venv = Join-Path $destFull '.venv'
$py = Join-Path $venv 'Scripts\python.exe'
$launcher = Join-Path $venv 'Scripts\skeleton-connector.exe'

# Runs skeleton_connector.setup_hold from the installed environment and returns its exit code
# (0 done; 3 no usable hub; 4 a session is being recorded).
function Invoke-Setup([string[]]$Arguments) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'      # a native command's stderr is not an error here
    try {
        & $py -m skeleton_connector.setup_hold @Arguments
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $saved }
}

# Holds the connector through the hub, or stops it when there is no hub. Returns $false when
# the setup must stop with nothing changed. $script:holdTried says a release is due.
$script:holdTried = $false
$script:rebuilding = $false
function Enter-Hold([string]$Purpose) {
    $code = Invoke-Setup @('hold', '--purpose', $Purpose, '--ttl', '300')
    if ($code -eq 4) {
        [Console]::Error.WriteLine('A driving session is being recorded with this connector. Finish it and try again; nothing was changed.')
        return $false
    }
    $script:holdTried = $true
    if ($code -ne 0) {
        if ((Invoke-Setup @('stop')) -ne 0) {
            [Console]::Error.WriteLine('The connector is still running. Close it and try again; nothing was changed.')
            return $false
        }
    }
    return $true
}

# The descriptor of this id as it stands, or $null (missing or not readable JSON).
function Read-Descriptor() {
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { return $null }
    try { return (Get-Content -LiteralPath $target -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}

if ($Uninstall) {
    if (Test-Path -LiteralPath $py -PathType Leaf) {
        if (-not (Enter-Hold 'uninstall')) {
            if ($script:holdTried) { Invoke-Setup @('release', '--start') | Out-Null }
            exit 1
        }
    }
    $removed = $false
    try {
        # Remove the descriptor only when it names THIS install (another build may have replaced it).
        if (Test-Path -LiteralPath $target -PathType Leaf) {
            $current = Read-Descriptor
            $launcherOf = $null
            if ($current) { $launcherOf = $current.launcher }
            $ours = $current -and ($current.product -ceq $product)
            $under = $ours -and $launcherOf -and ([System.IO.Path]::GetFullPath(($launcherOf -replace '/', '\')) + '\').StartsWith(
                $destFull + '\', [System.StringComparison]::OrdinalIgnoreCase)
            if ($under) {
                Remove-Item -LiteralPath $target -Force
                Write-Host "Removed: $target"
                $removed = $true
            } else {
                Write-Host "Left as it is (it names another install or another product): $target"
            }
        }
    } finally {
        # A descriptor removed while held keeps the connector "updating" until it is released:
        # release now, without starting, and before the environment that runs this goes. When
        # the descriptor is still there, the connector starts again.
        if ($script:holdTried) {
            Invoke-Setup @('release', $(if ($removed) { '--no-start' } else { '--start' })) | Out-Null
        }
    }
    Set-Location -LiteralPath $env:TEMP      # this script may run from the folder it removes
    if (Test-Path -LiteralPath $destFull) { Remove-Item -LiteralPath $destFull -Recurse -Force }
    Write-Host "Uninstalled: $destFull"
    return
}

# The id is another connector's when the file holds a different product key: never overwrite it.
# (A file without a product key is an older build of this connector, or broken: it is replaced.)
$existing = Read-Descriptor
if ($existing -and $existing.product -and ($existing.product -cne $product)) {
    [Console]::Error.WriteLine("The connector id '$id' is taken: $target belongs to another connector " +
        "('$($existing.name)', product $($existing.product)). Nothing was changed. " +
        "Uninstall that connector first, or give this one another id and product key.")
    exit 1
}

if ($Update -and (Test-Path -LiteralPath $py -PathType Leaf)) {
    if (-not (Enter-Hold 'update')) {
        if ($script:holdTried) { Invoke-Setup @('release', '--start') | Out-Null }
        exit 1
    }
}

try {
    New-Item -ItemType Directory -Force -Path $destFull | Out-Null

    if (-not $SkipInstall) {
        & (Join-Path $source 'fetch-ipc.ps1')
        if (-not $?) { throw 'fetch-ipc.ps1 failed' }
        $script:rebuilding = $true     # from here the old environment may be gone
        uv venv --python 3.12 --clear $venv
        if ($LASTEXITCODE -ne 0) { throw 'uv venv failed' }
        # A normal (not editable) install: the environment holds a copy, so the connector keeps
        # working if this source folder goes away.
        uv pip install --python $py $source
        if ($LASTEXITCODE -ne 0) { throw 'uv pip install failed' }
        if (-not (Test-Path -LiteralPath $launcher)) { throw "The launcher was not created: $launcher" }
    }

    # The descriptor's `uninstall` command runs this copy, so it must outlive the source folder.
    $installed = Join-Path $destFull 'install.ps1'
    if ([System.IO.Path]::GetFullPath($PSCommandPath) -ne [System.IO.Path]::GetFullPath($installed)) {
        Copy-Item -LiteralPath $PSCommandPath -Destination $installed -Force
    }

    $version = (Select-String -Path (Join-Path $source 'pyproject.toml') `
        -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
    # The connector's icon: installed beside the script, named by the descriptor's `icon`.
    $icon = Join-Path $destFull 'icon.png'
    Copy-Item -LiteralPath (Join-Path $source 'icon.png') -Destination $icon -Force
    # Windows PowerShell, by absolute path: no dependency on the shell that ran this script.
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $descriptor = [ordered]@{
        schema           = 1
        id               = $id
        name             = 'Skeleton connector'
        product          = $product
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
        uninstall        = @($ps, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                             '-File', $installed, '-Uninstall', '-Destination', $destFull)
        window           = $false               # no window of its own to show
        features         = @()                  # what it offers beyond the minimum (levels.md)
        icon             = $icon                # absolute path of the installed PNG
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
} catch {
    [Console]::Error.WriteLine("The install failed: $($_.Exception.Message)")
    if ($script:rebuilding) {
        [Console]::Error.WriteLine('The environment was being rebuilt: the earlier build may be gone. Run the install again.')
    }
    exit 1
} finally {
    # With the new environment or the old one: the hub starts the connector again.
    if ($script:holdTried -and (Test-Path -LiteralPath $py -PathType Leaf)) {
        Invoke-Setup @('release', '--start') | Out-Null
    }
}
