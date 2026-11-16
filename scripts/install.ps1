# Pepite installer for Windows (PowerShell):
#   irm https://raw.githubusercontent.com/aymaneprojects/pepite/main/scripts/install.ps1 | iex
# Env overrides: PEPITE_REPO (owner/name), PEPITE_VERSION (tag, branch or commit).
$ErrorActionPreference = 'Stop'

$Repo = if ($env:PEPITE_REPO) { $env:PEPITE_REPO } else { 'aymaneprojects/pepite' }
$Version = $env:PEPITE_VERSION

# 1. uv
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing uv (https://astral.sh/uv) ...'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw 'uv was installed but is not on PATH; open a new terminal and run this script again.'
    }
}

# 2. Which version?
if (-not $Version) {
    Write-Host "Looking for the latest release of $Repo ..."
    try {
        # Only app releases (tags v1.2.3); the monthly data packs are releases too (packs-YYYY-MM).
        $Version = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases?per_page=30" |
            Where-Object { $_.tag_name -match '^v[0-9]' -and -not $_.draft -and -not $_.prerelease } |
            Select-Object -First 1).tag_name
    } catch {
        $Version = $null
    }
    if (-not $Version) {
        Write-Warning 'No release found, using the main branch.'
        $Version = 'main'
    }
}
Write-Host "Installing Pepite $Version ..."

# 3. Install
uv tool install --force --python 3.13 "pepite @ git+https://github.com/$Repo@$Version"
if ($LASTEXITCODE -ne 0) { throw 'Installation failed.' }

$toolBin = (uv tool dir --bin 2>$null)
if ($toolBin) { $env:Path = "$toolBin;$env:Path" }

# 4. Company data (not fatal)
Write-Host 'Downloading the latest company data pack ...'
try {
    $env:PEPITE_PACKS_REPO = if ($env:PEPITE_PACKS_REPO) { $env:PEPITE_PACKS_REPO } else { $Repo }
    pepite pack download latest
    if ($LASTEXITCODE -ne 0) { throw 'download failed' }
} catch {
    Write-Warning 'Could not download a pack; the bundled sample will be used. Retry later with: pepite pack download latest'
}

Write-Host ''
Write-Host 'Pepite is installed.'
Write-Host 'Start it with:   pepite serve'
Write-Host 'Then open:       http://127.0.0.1:8000'
Write-Host "If 'pepite' is not found, run: uv tool update-shell   (then open a new terminal)"
