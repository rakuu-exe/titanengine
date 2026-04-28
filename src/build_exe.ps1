$ErrorActionPreference = "Stop"

# Change to the script's directory so relative paths work correctly
Push-Location $PSScriptRoot

try {
    $TemplateDir = Join-Path $PSScriptRoot "titanengine\templates"
    $StaticDir = Join-Path $PSScriptRoot "titanengine\static"
    $VideoFile = Join-Path $StaticDir "videos\noire_night_v2.mp4"
    $RootDir = Split-Path $PSScriptRoot -Parent
    $RootExe = Join-Path $RootDir "TitanEngine.exe"
    $RootInternal = Join-Path $RootDir "_internal"

    if (-not (Test-Path $TemplateDir)) {
        throw "Missing templates folder: $TemplateDir"
    }
    if (-not (Test-Path $StaticDir)) {
        throw "Missing static folder: $StaticDir"
    }
    if (-not (Test-Path $VideoFile)) {
        throw "Missing background video: $VideoFile"
    }

    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed with exit code $LASTEXITCODE."
    }

    python -m PyInstaller --onedir --name TitanEngine `
        --exclude-module 81d243bd2c585b0f4821__mypyc `
        --exclude-module textual `
        --exclude-module rich `
        --exclude-module PIL `
        --exclude-module numpy `
        --exclude-module scipy `
        --collect-submodules charset_normalizer `
        --add-data "$TemplateDir;titanengine\templates" `
        --add-data "$StaticDir;titanengine\static" `
        --clean -y --distpath .\dist --specpath .\build main.py
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE."
    }

    if (Test-Path $RootInternal) {
        attrib -h $RootInternal 2>$null
        Remove-Item -LiteralPath $RootInternal -Recurse -Force
    }
    if (Test-Path $RootExe) {
        Remove-Item -LiteralPath $RootExe -Force
    }

    Copy-Item -Path ".\dist\TitanEngine\*" -Destination $RootDir -Recurse -Force
    if (Test-Path $RootInternal) {
        attrib +h $RootInternal
    }

    Write-Host "Build complete. Static folder included, including videos."
} finally {
    Pop-Location
}
