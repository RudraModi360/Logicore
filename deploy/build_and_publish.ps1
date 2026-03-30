<#
.SYNOPSIS
    Build and publish logicore to PyPI (or TestPyPI).

.DESCRIPTION
    One-command script to clean, build, validate, and upload the package.

.PARAMETER TestPyPI
    Upload to TestPyPI instead of production PyPI.

.PARAMETER SkipClean
    Skip cleaning previous build artifacts.

.PARAMETER DryRun
    Build and validate only — do not upload.

.EXAMPLE
    .\deploy\build_and_publish.ps1                    # Build + upload to PyPI
    .\deploy\build_and_publish.ps1 -TestPyPI          # Build + upload to TestPyPI
    .\deploy\build_and_publish.ps1 -DryRun             # Build only, no upload
#>

param(
    [switch]$TestPyPI,
    [switch]$SkipClean,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Logicore Build & Publish Script" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# --- Step 1: Version Check ---
$InitFile = Join-Path $ProjectRoot "logicore\__init__.py"
$PyprojectFile = Join-Path $ProjectRoot "pyproject.toml"

$InitVersion = (Get-Content $InitFile | Select-String '__version__\s*=\s*"([^"]+)"').Matches.Groups[1].Value
$PyprojectVersion = (Get-Content $PyprojectFile | Select-String 'version\s*=\s*"([^"]+)"').Matches.Groups[1].Value

Write-Host "[1/5] Version Check" -ForegroundColor Yellow
Write-Host "  __init__.py:    $InitVersion"
Write-Host "  pyproject.toml: $PyprojectVersion"

if ($InitVersion -ne $PyprojectVersion) {
    Write-Host "  ERROR: Version mismatch! Update both files to the same version." -ForegroundColor Red
    exit 1
}
Write-Host "  Versions match." -ForegroundColor Green
Write-Host ""

# --- Step 2: Clean ---
if (-not $SkipClean) {
    Write-Host "[2/5] Cleaning old build artifacts..." -ForegroundColor Yellow
    $CleanDirs = @("dist", "build", "logicore.egg-info")
    foreach ($dir in $CleanDirs) {
        $fullPath = Join-Path $ProjectRoot $dir
        if (Test-Path $fullPath) {
            Remove-Item -Recurse -Force $fullPath
            Write-Host "  Removed: $dir" -ForegroundColor Gray
        }
    }
    Write-Host "  Clean complete." -ForegroundColor Green
} else {
    Write-Host "[2/5] Skipping clean." -ForegroundColor Gray
}
Write-Host ""

# --- Step 3: Build ---
Write-Host "[3/5] Building package..." -ForegroundColor Yellow
Push-Location $ProjectRoot
try {
    python -m build
    if ($LASTEXITCODE -ne 0) { throw "Build failed." }
    Write-Host "  Build complete." -ForegroundColor Green
} finally {
    Pop-Location
}
Write-Host ""

# --- Step 4: Validate ---
Write-Host "[4/5] Validating with twine..." -ForegroundColor Yellow
$DistPath = Join-Path $ProjectRoot "dist\*"
twine check $DistPath
if ($LASTEXITCODE -ne 0) { throw "Twine validation failed." }
Write-Host "  Validation passed." -ForegroundColor Green
Write-Host ""

# --- Step 5: Upload ---
if ($DryRun) {
    Write-Host "[5/5] DRY RUN — Skipping upload." -ForegroundColor Gray
    Write-Host "  Build artifacts in: dist/" -ForegroundColor Gray
} elseif ($TestPyPI) {
    Write-Host "[5/5] Uploading to TestPyPI..." -ForegroundColor Yellow
    twine upload --repository testpypi $DistPath
    if ($LASTEXITCODE -ne 0) { throw "TestPyPI upload failed." }
    Write-Host ""
    Write-Host "  Published to TestPyPI!" -ForegroundColor Green
    Write-Host "  Install: pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ logicore" -ForegroundColor Cyan
    Write-Host "  View:    https://test.pypi.org/project/logicore/" -ForegroundColor Cyan
} else {
    Write-Host "[5/5] Uploading to PyPI..." -ForegroundColor Yellow
    twine upload $DistPath
    if ($LASTEXITCODE -ne 0) { throw "PyPI upload failed." }
    Write-Host ""
    Write-Host "  Published to PyPI!" -ForegroundColor Green
    Write-Host "  Install: pip install logicore" -ForegroundColor Cyan
    Write-Host "  View:    https://pypi.org/project/logicore/" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Done! Version: $InitVersion" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan


