@echo off
chcp 65001 > nul
title Trading AI PRO - Build Setup Installer
echo ===================================================================
echo     DONG GOI BO CAI DAT THUONG MAI - TRADING AI PRO
echo                  by Kha Tran Design
echo ===================================================================
echo.

cd /d "%~dp0.."

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_app_setup.ps1"

echo.
echo Nhan phim bat ky de thoat...
pause > nul
