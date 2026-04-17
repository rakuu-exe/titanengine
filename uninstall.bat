@echo off
setlocal enabledelayedexpansion

title Titan Engine Uninstall
cd /d "%~dp0"

echo.
echo ========================================
echo     Titan Engine Uninstall
echo ========================================
echo.

set "DESKTOP=%USERPROFILE%\Desktop"
set "SHORTCUT=!DESKTOP!\Titan Engine.lnk"
set "INSTALL_ROOT=%LOCALAPPDATA%\TitanEngineApp"

echo [*] Removing Desktop shortcut...
if exist "!SHORTCUT!" (
    del /f /q "!SHORTCUT!"
    if errorlevel 1 (
        echo [!] Failed to remove shortcut.
    ) else (
        echo [+] Shortcut removed.
    )
) else (
    echo [+] Shortcut not found.
)

echo [*] Removing installation folder...
if exist "!INSTALL_ROOT!" (
    attrib -h "!INSTALL_ROOT!" 2>nul
    rmdir /s /q "!INSTALL_ROOT!"
    if errorlevel 1 (
        echo [!] Failed to remove folder.
        echo You may need to manually delete: !INSTALL_ROOT!
    ) else (
        echo [+] Installation removed.
    )
) else (
    echo [+] Installation not found.
)

echo.
echo ========================================
echo [+] Uninstall complete!
echo [+] Titan Engine has been removed.
echo ========================================
echo.
pause
