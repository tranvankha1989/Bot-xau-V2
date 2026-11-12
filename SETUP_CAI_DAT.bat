@echo off
chcp 65001 >nul
title CAI DAT TU DONG - AI (KhaTran) SCALPING VANG MT5
color 0B
cd /d "%~dp0"

echo ============================================================================
echo    HE THONG TU DONG CAI DAT VA TAO SHORTCUT - AI (KhaTran) SCALPING VANG
echo ============================================================================
echo.

set "PY_EXE="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY_EXE goto :FOUND_PY

if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if defined PY_EXE goto :FOUND_PY

if exist "%LOCALAPPDATA%\Programs\Python\Python310\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if defined PY_EXE goto :FOUND_PY

if exist "C:\Python312\python.exe" set "PY_EXE=C:\Python312\python.exe"
if defined PY_EXE goto :FOUND_PY

if exist "C:\Python311\python.exe" set "PY_EXE=C:\Python311\python.exe"
if defined PY_EXE goto :FOUND_PY

if exist "C:\Program Files\Python312\python.exe" set "PY_EXE=C:\Program Files\Python312\python.exe"
if defined PY_EXE goto :FOUND_PY

if exist "C:\Program Files\Python311\python.exe" set "PY_EXE=C:\Program Files\Python311\python.exe"
if defined PY_EXE goto :FOUND_PY

for /f "delims=" %%i in ('where python 2^>nul') do (
    echo "%%i" | findstr /i "WindowsApps" >nul
    if errorlevel 1 if not defined PY_EXE set "PY_EXE=%%i"
)
if defined PY_EXE goto :FOUND_PY

echo [1/4] May chua co Python. Dang tu dong tai va cai dat Python 3.12 64-bit...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '$env:TEMP\py_setup.exe'; Start-Process -FilePath '$env:TEMP\py_setup.exe' -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_test=0' -Wait; Remove-Item '$env:TEMP\py_setup.exe' -Force -ErrorAction SilentlyContinue"
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY_EXE set "PY_EXE=python"

:FOUND_PY
echo [1/4] Da tim thay Python: "%PY_EXE%"
echo.
echo [2/4] Dang kiem tra va cai dat cac thu vien AI va MetaTrader5...
"%PY_EXE%" -B -m pip install torch numpy pandas matplotlib MetaTrader5 huggingface_hub safetensors tqdm einops gradio_client Pillow
echo.
echo [3/4] Dang thiet lap thong so cai dat mac dinh, Icon KhaTran va tao Shortcut...
"%PY_EXE%" -B "%~dp0mt5_scalping_bot.py" --setup

for %%F in ("%PY_EXE%") do set "PYW_EXE=%%~dpFpythonw.exe"
if not exist "%PYW_EXE%" set "PYW_EXE=%PY_EXE%"

echo.
echo ============================================================================
echo [4/4] HOAN TAT 100%%!
echo   - Da tao file chay 'AI Scalping M5' ngay trong thu muc nay va ngoai Desktop.
echo   - Dang tu dong khoi dong phan mem...
echo ============================================================================
start "" "%PYW_EXE%" -B "%~dp0mt5_scalping_bot.py"
echo.
pause
