# ====================================================================
# Trading AI PRO by Kha Tran Design - One-Click Commercial Installer Builder
# Dong goi tu dong: Ma hoa nhi phan Hex (01010101) + Inno Setup Ultra
# ====================================================================

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = if ($PSScriptRoot) { (Resolve-Path "$PSScriptRoot\..").Path } else { (Get-Location).Path }
Set-Location $Root

Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "   DONG GOI BO CAI DAT THUONG MAI - TRADING AI PRO" -ForegroundColor Cyan
Write-Host "                  by Kha Tran Design" -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Kiem tra moi truong
$versionFile = Join-Path $Root "version.json"
if (-not (Test-Path $versionFile)) { throw "Khong tim thay version.json tai $versionFile" }
$versionJson = Get-Content $versionFile -Raw -Encoding UTF8 | ConvertFrom-Json
$version = $versionJson.version
Write-Host "[1/5] Thong tin phien ban: v$version" -ForegroundColor Green

$isccPath = "C:\Program Files\Inno Setup 7\ISCC.exe"
if (-not (Test-Path $isccPath)) {
    $found = Get-ChildItem -Path "C:\Program Files", "C:\Program Files (x86)" -Filter "ISCC.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { $isccPath = $found.FullName }
    else { throw "Khong tim thay Inno Setup compiler (ISCC.exe). Vui long cai dat Inno Setup 7!" }
}
Write-Host "[OK] Inno Setup compiler: $isccPath" -ForegroundColor DarkGray

# 2. Bam va ma hoa ma nguon thanh tap tin nhi phan core_engine.bin
Write-Host ""
Write-Host "[2/5] Dang bam va ma hoa ma nguon sang khoi nhi phan Hex (core_engine.bin)..." -ForegroundColor Yellow
python "tools/binary_packer.py"
if ($LASTEXITCODE -ne 0) { throw "Loi khi tao core_engine.bin!" }

# 3. Chuan bi thu muc staging
Write-Host ""
Write-Host "[3/5] Don dep thu muc staging cu..." -ForegroundColor Yellow
$stagingDir = Join-Path $Root "build_staging"
if (Test-Path $stagingDir) { Remove-Item $stagingDir -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null

$outputDir = Join-Path $Root "installer_output"
if (-not (Test-Path $outputDir)) { New-Item -ItemType Directory -Path $outputDir -Force | Out-Null }

# 4. Bien dich Bootloader nhan may bang PyInstaller (onedir mode)
Write-Host ""
Write-Host "[4/5] Bien dich Bootloader nhan may bang PyInstaller..." -ForegroundColor Yellow
$iconPath = Join-Path $Root "tools\license-key-icon.ico"
python -m PyInstaller --noconsole `
    --name="Trading AI PRO" `
    --icon="$iconPath" `
    --clean `
    --distpath="$stagingDir\dist" `
    --workpath="$stagingDir\work" `
    --hidden-import="cryptography" `
    --hidden-import="cryptography.hazmat.primitives.asymmetric.ed25519" `
    --hidden-import="cryptography.hazmat.primitives.kdf.pbkdf2" `
    --hidden-import="cryptography.hazmat.primitives.hashes" `
    --hidden-import="cryptography.hazmat.primitives.ciphers.aead" `
    --hidden-import="cryptography.exceptions" `
    --hidden-import="MetaTrader5" `
    --hidden-import="torch" `
    --hidden-import="numpy" `
    --hidden-import="pandas" `
    --hidden-import="PIL" `
    --hidden-import="matplotlib" `
    --hidden-import="tqdm" `
    --hidden-import="huggingface_hub" `
    --hidden-import="gradio_client" `
    --hidden-import="einops" `
    --hidden-import="safetensors" `
    --collect-all="cryptography" `
    --collect-all="einops" `
    --collect-all="safetensors" `
    launcher.py

if ($LASTEXITCODE -ne 0) { throw "PyInstaller gap loi trong qua trinh build bootloader!" }

# 4.1 Bo sung cac DLL Visual C++ Runtime 2015-2022 vao truc tiep thu muc App va _internal
Write-Host "Dang bo sung runtime DLLs (vcruntime140_1.dll, msvcp140*.dll)..." -ForegroundColor DarkGray
$vcDlls = @(
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll"
)
$appDistDir = Join-Path $stagingDir "dist\Trading AI PRO"
$internalDistDir = Join-Path $appDistDir "_internal"

foreach ($dll in $vcDlls) {
    $sysDll = Join-Path $env:SystemRoot "System32\$dll"
    if (Test-Path $sysDll) {
        Copy-Item -Path $sysDll -Destination (Join-Path $appDistDir $dll) -Force -ErrorAction SilentlyContinue
        if (Test-Path $internalDistDir) {
            Copy-Item -Path $sysDll -Destination (Join-Path $internalDistDir $dll) -Force -ErrorAction SilentlyContinue
        }
    }
}

# 4.2 Dam bao file vc_redist.x64.exe co san trong tools de Inno Setup dong goi
$vcRedistTools = Join-Path $Root "tools\vc_redist.x64.exe"
if (-not (Test-Path $vcRedistTools)) {
    $officeRedist = "C:\Program Files\Microsoft Office\root\Integration\Addons\vc_redist.x64.exe"
    if (Test-Path $officeRedist) {
        Copy-Item -Path $officeRedist -Destination $vcRedistTools -Force
    }
}

# 5. Dong goi bo cai dat Setup bang Inno Setup Ultra
Write-Host ""
Write-Host "[5/5] Nen toan bo thanh file Setup duy nhat bang Inno Setup (LZMA2-Ultra)..." -ForegroundColor Yellow
$issPath = Join-Path $Root "tools\app_installer.iss"
& "$isccPath" "$issPath"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compiler gap loi!" }

# 6. Don dep thu muc staging tam
Write-Host ""
Write-Host "Dang don dep thu muc build tam..." -ForegroundColor DarkGray
Remove-Item $stagingDir -Recurse -Force -ErrorAction SilentlyContinue

$setupFile = Join-Path $outputDir "Setup_Trading_AI_PRO_v$version.exe"
if (Test-Path $setupFile) {
    $setupItem = Get-Item $setupFile
    $sizeMB = [math]::Round($setupItem.Length / 1MB, 1)
    Write-Host ""
    Write-Host "====================================================================" -ForegroundColor Green
    Write-Host " DONG GOI THANH CONG BO CAI DAT SETUP THUONG MAI!" -ForegroundColor Green
    Write-Host " Phien ban  : v$version" -ForegroundColor Cyan
    Write-Host " Dung luong : $sizeMB MB" -ForegroundColor Cyan
    Write-Host " File xuat  : $setupFile" -ForegroundColor Yellow
    Write-Host "====================================================================" -ForegroundColor Green
    Write-Host " Huong dan: Gui file Setup nay cho chuyen gia / khach hang cai dat." -ForegroundColor Gray
    Write-Host " Sau khi cai xong, khach se gui ma HWID cho ban de lay Key dung thu." -ForegroundColor Gray
    Write-Host "====================================================================" -ForegroundColor Green
} else {
    Write-Host "[CANH BAO] Khong tim thay file setup xuat ra tai $setupFile" -ForegroundColor Red
}
