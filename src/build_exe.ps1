$ErrorActionPreference = "Stop"

# Change to the script's directory so relative paths work correctly
Push-Location $PSScriptRoot

try {
    python -m pip install -r requirements.txt
    python -m pip install -r requirements-build.txt
    python -m PyInstaller --onedir --name TitanEngine `
        --exclude-module 81d243bd2c585b0f4821__mypyc `
        --add-data "titanengine\templates;titanengine\templates" `
        --add-data "titanengine\static;titanengine\static" `
        --clean -y --distpath .\dist main.py

    Write-Host "Build complete."
} finally {
    Pop-Location
}
