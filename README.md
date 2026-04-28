# Titan Study Engine

Titan Study Engine is a local Windows study helper. It starts a Flask app on `http://127.0.0.1:5000`, opens it in your browser, reads school files, creates notes and quizzes, exports PDFs, and builds study schedules.

## Student Files

For normal use, students only need these files:

```text
setup.bat
uninstall.bat
TitanEngine.exe
```

`TitanEngine.exe` is created by `setup.bat`.

When the exe is built from source, Windows may also have a hidden `_internal` runtime folder beside it. Leave that folder in place; it is hidden because students normally do not need to touch it.

## Install

1. Double-click `setup.bat`.
2. Wait for the build and install to finish.
3. Open `Titan Engine` from the Desktop, or run `TitanEngine.exe` from this folder.

The installer copies the real app to:

```text
%LOCALAPPDATA%\TitanEngineApp\app
```

That install folder is hidden so students do not have to manage app internals.

## Uninstall

Double-click:

```text
uninstall.bat
```

This removes the Desktop shortcut and the hidden install folder.

## Development

Source code lives under `src/`.

Run from source:

```powershell
cd src
python -m pip install -r requirements.txt
python main.py
```

Build the exe manually:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\src\build_exe.ps1
```

Install the Desktop shortcut manually:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\src\install_shortcut.ps1
```

Run tests:

```powershell
cd src
python -m unittest discover
```

## Notes

- The packaged exe is web-only and leaves out the old terminal UI to keep the app smaller.
- The web UI uses lazy in-page navigation so most clicks and forms do not trigger a full browser refresh.
- Files and generated data are stored locally.
- Online AI is only used when an API key is configured in Settings.
