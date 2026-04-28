@echo off
setlocal enabledelayedexpansion

title Titan Engine Setup
cd /d "%~dp0"

echo.
echo ========================================
echo        Titan Engine Setup
echo ========================================
echo.

REM Check Python
where python >nul 2>nul
if errorlevel 1 (
    echo [!] Python was not found.
    echo Please install Python 3.12+ from: https://www.python.org/downloads/
    echo Make sure to tick "Add python.exe to PATH"
    echo.
    pause
    exit /b 1
)

echo [*] Checking Python...
python --version >nul 2>nul
if errorlevel 1 (
    echo [!] Python check failed.
    pause
    exit /b 1
)

echo [*] Upgrading pip...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo [!] Pip upgrade failed.
    pause
    exit /b 1
)

echo [*] Building Titan Engine...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0src\build_exe.ps1"
if errorlevel 1 (
    echo [!] Build failed.
    pause
    exit /b 1
)

echo [*] Installing Titan Engine...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0src\install_shortcut.ps1"
if errorlevel 1 (
    echo [!] Installation failed.
    pause
    exit /b 1
)

echo.
echo ========================================
echo [+] Setup complete!
echo [+] Open "Titan Engine" from Desktop
echo [+] Or run TitanEngine.exe from this folder
echo ========================================
echo.
pause
