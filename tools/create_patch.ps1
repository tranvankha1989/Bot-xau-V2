# ====================================================================
# Trading AI PRO by Kha Tran Design - Auto Create OTA Patch Script (PowerShell)
# Dung de dong goi nhanh goi cap nhat Pre-built Patch upload len GitHub Release
# ====================================================================

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = if ($PSScriptRoot) { (Resolve-Path "$PSScriptRoot\..").Path } else { (Get-Location).Path }
Set-Location $Root

Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "   DONG GOI BAN CAP NHAT PRE-BUILT PATCH - TRADING AI PRO" -ForegroundColor Cyan
Write-Host "                  by Kha Tran Design" -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Doc thong tin phien ban tu version.json
$versionFile = Join-Path $Root "version.json"
if (-not (Test-Path $versionFile)) {
    throw "Khong tim thay file version.json tai: $versionFile"
}

$versionJson = Get-Content $versionFile -Raw -Encoding UTF8 | ConvertFrom-Json
$version = $versionJson.version
Write-Host "[OK] Phien ban dang dong goi: v$version" -ForegroundColor Green
Write-Host "Mo ta: $($versionJson.description)" -ForegroundColor Gray
Write-Host ""

# 2. Tu dong cap nhat dong co nhi phan ma hoa core_engine.bin
Write-Host "Dang bien dich va ma hoa lai core_engine.bin ..." -ForegroundColor Yellow
$packerScript = Join-Path $Root "tools\binary_packer.py"
python $packerScript
if ($LASTEXITCODE -ne 0) {
    throw "Loi khi tao core_engine.bin!"
}

# 3. Chuan bi thu muc staging
$outputDir = Join-Path $Root "installer_output"
if (-not (Test-Path $outputDir)) { New-Item -ItemType Directory -Path $outputDir -Force | Out-Null }

$patchZip = Join-Path $outputDir "patch_v$version.zip"
if (Test-Path $patchZip) { Remove-Item $patchZip -Force }

$stagingDir = Join-Path $Root "build_staging\patch"
if (Test-Path $stagingDir) { Remove-Item $stagingDir -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null

# 4. Sao chep cac tep tin ma nguon va dong co nhi phan vao staging
$filesToInclude = @(
    "core_engine.bin",
    "mt5_scalping_bot.py",
    "kronos_license.py",
    "kronos_updater.py",
    "version.json",
    "requirements.txt"
)

foreach ($f in $filesToInclude) {
    $srcPath = Join-Path $Root $f
    if (Test-Path $srcPath) {
        Copy-Item -Path $srcPath -Destination (Join-Path $stagingDir $f) -Force
        Write-Host "  + Da them: $f" -ForegroundColor DarkGray
    }
}

# Don sach __pycache__ va cac file rac
Get-ChildItem -Path $stagingDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $stagingDir -Recurse -File -Include @('*.pyc', '*.tmp') | Remove-Item -Force -ErrorAction SilentlyContinue

# 4. Nen thanh tep ZIP
Write-Host ""
Write-Host "Dang nen thanh: $patchZip ..." -ForegroundColor Yellow
Compress-Archive -Path "$stagingDir\*" -DestinationPath $patchZip -CompressionLevel Optimal

# Xoa staging tam
Remove-Item $stagingDir -Recurse -Force -ErrorAction SilentlyContinue

$zipItem = Get-Item $patchZip
$sizeKB = [math]::Round($zipItem.Length / 1KB, 1)

Write-Host ""
Write-Host "====================================================================" -ForegroundColor Green
Write-Host " DONG GOI THANH CONG BAN PATCH v$version ($sizeKB KB)!" -ForegroundColor Green
Write-Host " File xuat tai: $patchZip" -ForegroundColor Cyan
Write-Host " Huong dan: Upload file patch nay len muc Release tren GitHub voi tag 'v$version'" -ForegroundColor Gray
Write-Host "====================================================================" -ForegroundColor Green

