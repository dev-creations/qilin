# Qilin installer for Windows.
#
# Usage (in PowerShell):
#     irm https://raw.githubusercontent.com/dev-creations/qilin/main/scripts/install.ps1 | iex
#
# Optional environment variables (PowerShell $env:):
#     QILIN_VERSION   - version to install (default: latest GitHub release).
#     QILIN_PREFIX    - install directory (default: $env:LOCALAPPDATA\Programs\qilin).
#     QILIN_REPO      - GitHub repo (default: dev-creations/qilin).
#
# The script downloads the matching release zip, verifies its SHA-256 against
# the published checksums.txt, and installs qilin.exe into $QILIN_PREFIX.

$ErrorActionPreference = 'Stop'

function Write-Info($msg)  { Write-Host ("qilin-install: " + $msg) }
function Write-WarnMsg($msg) { Write-Warning ("qilin-install: " + $msg) }
function Die($msg) {
    Write-Error ("qilin-install: error: " + $msg)
    exit 1
}

function Get-Arch {
    switch ($env:PROCESSOR_ARCHITECTURE) {
        'AMD64' { return 'x86_64' }
        'X86'   { Die "32-bit Windows is not supported" }
        'ARM64' { Die "Windows on ARM is not currently published; build from source." }
        default { Die "unsupported architecture: $env:PROCESSOR_ARCHITECTURE" }
    }
}

function Resolve-Version {
    $explicit = $env:QILIN_VERSION
    if ($explicit) { return $explicit }

    $repo = if ($env:QILIN_REPO) { $env:QILIN_REPO } else { 'dev-creations/qilin' }
    $url  = "https://api.github.com/repos/$repo/releases/latest"
    try {
        $rel = Invoke-RestMethod -Headers @{ 'User-Agent' = 'qilin-installer' } -Uri $url
    } catch {
        Die "failed to query GitHub for the latest release: $_"
    }
    if (-not $rel.tag_name) { Die "no tag_name in the GitHub release response" }
    return [string]$rel.tag_name
}

function Sha256-Of($path) {
    $hash = Get-FileHash -Path $path -Algorithm SHA256
    return $hash.Hash.ToLower()
}

function Add-ToUserPath($dir) {
    $currentPath = [Environment]::GetEnvironmentVariable('Path', [EnvironmentVariableTarget]::User)
    $entries = @()
    if ($currentPath) { $entries = $currentPath -split ';' }
    if ($entries -notcontains $dir) {
        $newPath = ($entries + $dir) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $newPath, [EnvironmentVariableTarget]::User)
        Write-Info "added $dir to user PATH (restart your shell to pick it up)"
    }
}

function Main {
    $repo    = if ($env:QILIN_REPO) { $env:QILIN_REPO } else { 'dev-creations/qilin' }
    $version = Resolve-Version
    $vNum    = $version.TrimStart('v')
    $arch    = Get-Arch
    $prefix  = if ($env:QILIN_PREFIX) { $env:QILIN_PREFIX } else { Join-Path $env:LOCALAPPDATA 'Programs\qilin' }

    Write-Info "OS=windows ARCH=$arch VERSION=$version PREFIX=$prefix"

    $archive       = "qilin_${vNum}_windows_${arch}.zip"
    $archiveUrl    = "https://github.com/$repo/releases/download/$version/$archive"
    $checksumsUrl  = "https://github.com/$repo/releases/download/$version/checksums.txt"

    $tmp = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("qilin-install-" + [System.IO.Path]::GetRandomFileName())) -Force
    try {
        $archivePath   = Join-Path $tmp.FullName $archive
        $checksumsPath = Join-Path $tmp.FullName 'checksums.txt'

        Write-Info "downloading $archiveUrl"
        Invoke-WebRequest -UseBasicParsing -Uri $archiveUrl -OutFile $archivePath

        Write-Info "downloading checksums"
        Invoke-WebRequest -UseBasicParsing -Uri $checksumsUrl -OutFile $checksumsPath

        $expected = $null
        foreach ($line in Get-Content $checksumsPath) {
            if ($line -match "^([0-9a-f]{64})\s+$([regex]::Escape($archive))$") {
                $expected = $matches[1].ToLower()
                break
            }
        }
        if (-not $expected) { Die "no checksum entry for $archive in checksums.txt" }

        $actual = Sha256-Of $archivePath
        if ($actual -ne $expected) { Die "SHA-256 mismatch: expected $expected, got $actual" }
        Write-Info "checksum verified ($actual)"

        $extractDir = Join-Path $tmp.FullName 'extract'
        Expand-Archive -Path $archivePath -DestinationPath $extractDir -Force

        $binSrc = Join-Path $extractDir 'qilin.exe'
        if (-not (Test-Path $binSrc)) { Die "extracted archive does not contain qilin.exe" }

        if (-not (Test-Path $prefix)) {
            New-Item -ItemType Directory -Path $prefix -Force | Out-Null
        }
        $binDst = Join-Path $prefix 'qilin.exe'
        Copy-Item -Path $binSrc -Destination $binDst -Force

        Write-Info "installed to $binDst"
        Add-ToUserPath $prefix
        Write-Info "run 'qilin init' to get started"
    } finally {
        Remove-Item -Recurse -Force $tmp.FullName -ErrorAction SilentlyContinue
    }
}

Main
