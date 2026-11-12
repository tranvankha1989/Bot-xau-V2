@echo off
title Kiem tra khoi dong Trading AI PRO
cd /d "%~dp0"
echo ====================================================================
echo   KIEM TRA KHOI DONG TRADING AI PRO (DEBUG CONSOLE)
echo ====================================================================
echo.
echo Thu muc hien tai: %CD%
echo Dang khoi chay: "Trading AI PRO.exe" ...
echo.
"Trading AI PRO.exe"
set RET=%ERRORLEVEL%
echo.
echo ====================================================================
echo Tien trinh da ket thuc voi ma loi (Exit code): %RET%
if %RET% EQU 0 (
    echo [OK] Ung dung khoi dong va dong thanh cong binh thuong.
) else (
    echo [CANH BAO] Phat hien loi khoi dong. Ma loi thoat: %RET%
    if exist "logs\crash_startup.log" (
        echo.
        echo --- Noi dung logs\crash_startup.log ---
        type "logs\crash_startup.log"
    )
)
echo ====================================================================
echo Nhan phim bat ky de dong cua so nay...
pause >nul

