# Titan Engine

Titan Engine is a local study helper. It can read school files, create notes and quizzes, export PDFs, and build an AI-reviewed study schedule.

It is made for a simple folder setup like this:

```text
Desktop
`-- School
    `-- MAJANDUS
        |-- Source_Materials
        |-- Revision
        |-- Quizzes
        |-- Schedules
        `-- System
```

## Repository Structure

```text
TitanEngine/
├── setup.bat               ← Run this to install
├── uninstall.bat           ← Run this to uninstall
├── README.md
├── LICENSE
├── .gitignore
├── src/                    ← All source code & build configuration
│   ├── main.py
│   ├── titanengine/
│   ├── tests/
│   ├── build_exe.ps1
│   ├── install_shortcut.ps1
│   ├── TitanEngine.spec
│   ├── requirements.txt
│   └── requirements-build.txt
└── TitanEngine/            ← App folder with dependencies (created by build)
    ├── TitanEngine.exe     ← Your executable
    ├── _internal/ (dependencies)
    └── (other files)
```

## Easy Setup For Students

Use this on a fresh Windows computer:

1. Double-click `setup_titan_engine.bat`

The setup will:

1. Check that Python is installed.
2. Install the needed Python packages.
3. Build the Titan Engine app.
4. Copy the app to a hidden folder in your user account.
5. Create a Desktop shortcut named `Titan Engine`.

After setup, students only need to open:

```text
Titan Engine
```

from the Desktop.

## Uninstall

To completely remove Titan Engine:

1. Double-click `uninstall.bat`

This will remove:

- The Desktop shortcut named `Titan Engine`
- The app installation folder and all its files

## If Python Is Missing

If setup says Python was not found:

1. Go to https://www.python.org/downloads/
2. Install Python 3.12 or newer.
3. Important: tick `Add python.exe to PATH` during installation.
4. Run `setup_titan_engine.bat` again.

## Where The App Is Installed

The setup copies the app to a hidden folder under:

```text
%LOCALAPPDATA%\TitanEngineApp
```

That folder is hidden so normal users do not need to touch it. The Desktop shortcut is the clean way to open the app.

## First-Time App Setup

1. Open Titan Engine from the Desktop shortcut.
2. Click `Settings`.
3. Paste your Gemini API key.
4. Choose a model. `Gemini 2.5 Flash` is a good default.
5. Click `Save & Apply`.

You can get a Gemini API key from Google AI Studio.

## Create A Subject

1. Click `New Subject`.
2. Enter the subject name, for example `MAJANDUS`.
3. Enter the subject begin date in this format:

```text
YYYY-MM-DD
```

Example:

```text
2026-02-02
```

4. If you know the exam date, enter it too. If not, leave it empty.
5. Enter weekly study hours, for example `4`.
6. Click `Create Subject`.

The subject begin date is important because the scheduler converts course weeks into real calendar dates.

## Add School Files

1. Put your files somewhere easy to find.
2. In Titan Engine, select your subject folder.
3. Select a PDF, DOCX, Markdown, text, or CSV file.
4. Click `Import Material`.

Supported files:

- PDF
- DOCX
- Markdown
- TXT
- CSV

The scheduler can understand English, Estonian, Russian, or mixed language files.

## Make Notes Or Quizzes

1. Select a source file inside `Source_Materials`.
2. Click `Generate Notes` or `Generate Quiz`.
3. Wait for the draft.
4. Review the text.
5. Click `Save`.

Saved outputs are PDF files:

- Notes go to `Revision`
- Quizzes go to `Quizzes`
- Schedules go to `Schedules`

## Build A Smart Schedule

1. Select the subject folder.
2. Click `Build Schedule`.
3. Titan Engine asks Gemini to detect:
   - tests
   - exams
   - projects
   - deadlines
   - lectures or attendance events
   - which files belong to which course weeks
4. Review the detected events.
5. Click `Accept & Build Schedule`.
6. Review the schedule draft.
7. Click `Save` to export it as a PDF.

Example: if a test is in week 8, Titan Engine prepares weeks 1-8. If another test is in week 15, it prepares the later weeks instead of only repeating old material.

## Manual Build Commands

Most students should use `setup_titan_engine.bat`.

For development, navigate to the `src/` folder first:

```powershell
cd src
python -m pip install -r requirements.txt
python main.py
```

To rebuild the Windows app manually:

```powershell
cd src
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1
```

To install or refresh the Desktop shortcut manually:

```powershell
cd src
powershell -NoProfile -ExecutionPolicy Bypass -File .\install_shortcut.ps1
```

The verified app build is at:

```text
TitanEngine\TitanEngine.exe
```

Note: PyInstaller one-file mode failed on this machine because Windows blocked temporary extraction. The folder build was tested and works.

## If Something Does Not Work

- If the app says the API key is missing, open `Settings` and paste the Gemini key again.
- If a PDF gives bad results, it may be a scanned image PDF. Those need OCR support, which is not included yet.
- If the schedule looks wrong, cancel at the review step and try again after adding clearer source files.
- If setup fails, run `setup_titan_engine.bat` again and read the message shown before the window closes.

## Developer Checks

From the `src/` folder:

```powershell
cd src
python -m py_compile main.py titanengine\app.py titanengine\scheduler.py titanengine\pdf_export.py tests\test_scheduler.py
python -m unittest discover
```
