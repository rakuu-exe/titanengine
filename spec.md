Project Specification: Titan Study Engine
1. Project Summary

Titan Study Engine is a local desktop study helper that launches as an .exe and automatically starts a local web app in the user’s browser. The app helps students upload or scan school files, generate study notes, create quizzes, export PDFs, and build an AI-reviewed study schedule.

The project should run locally through a localhost server, for example:

http://127.0.0.1:5000

The first-time setup should be handled by a Windows batch file such as:

setup_titan_engine.bat

The interface should follow the uploaded UI style: dark background, video backdrop, glass-like cards, rounded panels, muted text, strong contrast, and clean table/card layouts. The current uploaded design already uses a fixed video background, dark overlay, centered container, hero section, card components, buttons, input forms, tables, hover previews, and responsive styling. The uploaded HTML also shows the intended Flask/Jinja-style structure with url_for, static files, cards, form sections, result tables, and dynamic client-side preview behavior.

2. Main Goal

The goal is to create a private local AI study assistant that students can run on their own computer.

The app should:

Run from a desktop .exe.
Start a local Flask/FastAPI web server.
Open a browser-based UI automatically.
Read school files from local storage.
Use AI APIs to summarize, explain, and review content.
Generate notes, quizzes, flashcards, and schedules.
Export study material as PDFs.
Store projects locally.
3. Target Users

The main users are students who want to:

Organize school files.
Turn PDFs, Word files, slides, and text into notes.
Make quizzes from their own material.
Get help understanding difficult topics.
Plan study time before tests or exams.
Export polished notes or revision packs.
4. Core Features
4.1 Local Desktop Launcher

The app should have a desktop executable:

TitanStudyEngine.exe

When opened, it should:

Check whether required folders exist.
Start the backend server locally.
Open the web app in the default browser.
Display a startup screen if the server is loading.
Shut down cleanly when the user exits.

Example flow:

User double-clicks TitanStudyEngine.exe
↓
Local server starts
↓
Browser opens http://127.0.0.1:5000
↓
User uses the study helper through the web UI
4.2 First-Time Setup

The project should include:

setup_titan_engine.bat

The setup script should:

Create a Python virtual environment.
Install dependencies.
Create required folders.
Ask for an AI API key or open the settings page.
Create a .env file.
Verify that the app can start.
Optionally create a desktop shortcut.

Suggested folders:

TitanStudyEngine/
├── app/
├── static/
├── templates/
├── uploads/
├── exports/
├── database/
├── logs/
├── setup_titan_engine.bat
├── run_titan_engine.bat
├── requirements.txt
└── .env
5. Web UI Specification
5.1 Visual Style

The UI should be based on the uploaded design.

Style direction:

Dark theme.
Full-screen video background.
Black overlay for readability.
Glassmorphism cards.
Rounded corners.
Soft shadows.
Muted gray/white color palette.
Large hero title.
Uppercase eyebrow text.
Clean input forms.
Table/card-based results.
Hover preview cards for documents, notes, or quizzes.

The existing CSS already defines variables such as --bg, --card, --border, --text, --muted, and glass-style .card components.

5.2 Main Pages
Dashboard

Purpose: show the user’s study workspace.

Sections:

Welcome hero.
Recent subjects.
Recent uploaded files.
Upcoming study tasks.
Quick actions:
Upload file.
Generate notes.
Create quiz.
Build schedule.
Export PDF.
File Library

Purpose: manage uploaded school files.

Features:

Upload PDFs, DOCX, TXT, PPTX, and images.
Show file name, subject, upload date, file type, and processing status.
Allow deleting or reprocessing files.
Allow tagging files by subject.
Show preview metadata.

Example table columns:

File | Subject | Type | Status | Uploaded | Actions
AI Notes Generator

Purpose: convert files into study notes.

User flow:

User selects one or more files.
User chooses note style:
Short summary.
Detailed notes.
Bullet-point revision.
Exam-focused notes.
Simple explanation.
AI generates notes.
User can edit notes.
User can save or export notes.

Note output should include:

Title.
Key concepts.
Main explanation.
Important definitions.
Examples.
Possible exam questions.
Summary.
Quiz Builder

Purpose: generate quizzes from school files or notes.

Quiz types:

Multiple choice.
True/false.
Short answer.
Fill in the blank.
Mixed quiz.

Quiz settings:

Subject
Source file
Difficulty
Number of questions
Question type
Include answers: yes/no
Include explanations: yes/no

After generation, the app should show:

Questions.
Answer choices.
Correct answers.
AI explanation.
Score after completion.
Weak areas.
Flashcards

Purpose: create quick revision cards.

Features:

Generate flashcards from selected notes/files.
Front side: term or question.
Back side: explanation or answer.
Mark cards as easy, medium, or hard.
Repeat hard cards more often.
Study Schedule Builder

Purpose: create an AI-reviewed study plan.

Inputs:

Exam date
Subjects
Available study days
Study time per day
Difficulty level per subject
Files/notes to include

The AI should generate:

Daily study blocks.
Topic order.
Revision days.
Quiz days.
Break recommendations.
Final review day.
Risk warnings, for example: “Too much material for the time available.”

The schedule should be editable by the user.

PDF Export

Purpose: export generated study material.

Export options:

Notes PDF.
Quiz PDF.
Flashcards PDF.
Full study pack PDF.
Study schedule PDF.

PDF should include:

Cover title.
Subject name.
Generated date.
Table of contents if needed.
Clean formatting.
Page numbers.
Settings Page

Settings should include:

AI API key.
AI provider selection.
Default model.
Local storage path.
Export folder.
Theme options.
Privacy options.
Clear cache.
Reset app.

The API key should be stored locally in .env or encrypted local config.

6. AI Features
6.1 AI Assistant Modes

The AI should support different modes:

Explain Mode

Explains selected content in simpler words.

Notes Mode

Creates structured notes.

Quiz Mode

Creates questions and answers.

Schedule Mode

Reviews workload and builds a study plan.

Review Mode

Checks the user’s answers and explains mistakes.

6.2 AI Safety and Privacy

Because the app reads school files, privacy should be clear.

Requirements:

Files are stored locally.
The app should only send necessary text snippets to the AI API.
The user should be warned before using online AI processing.
API keys must not be hardcoded.
Uploaded files should not be sent fully unless required.
The user should be able to delete all local data.
7. Technical Architecture
7.1 Recommended Stack

Backend:

Python + Flask

Alternative:

Python + FastAPI

Frontend:

HTML + CSS + JavaScript + Jinja templates

Database:

SQLite

PDF export:

ReportLab, WeasyPrint, or Playwright-based HTML-to-PDF

File parsing:

PyPDF2 or pypdf
python-docx
python-pptx
plain text reader
OCR later as optional feature

Packaging:

PyInstaller
7.2 Backend Modules

Suggested modules:

app/
├── main.py
├── config.py
├── database.py
├── ai_client.py
├── file_parser.py
├── note_service.py
├── quiz_service.py
├── schedule_service.py
├── pdf_service.py
├── routes/
│   ├── dashboard_routes.py
│   ├── file_routes.py
│   ├── notes_routes.py
│   ├── quiz_routes.py
│   ├── schedule_routes.py
│   └── settings_routes.py
├── templates/
└── static/
8. Data Model
8.1 Files Table
files
- id
- original_filename
- stored_filename
- file_type
- subject
- upload_date
- extracted_text
- status
8.2 Notes Table
notes
- id
- file_id
- title
- content
- note_type
- created_at
- updated_at
8.3 Quizzes Table
quizzes
- id
- source_id
- title
- difficulty
- question_type
- created_at
8.4 Questions Table
questions
- id
- quiz_id
- question
- choices
- correct_answer
- explanation
8.5 Study Schedule Table
study_schedules
- id
- title
- exam_date
- plan_json
- ai_review
- created_at
9. Main Routes

Example Flask routes:

GET  /
GET  /library
POST /upload
GET  /files/<id>
POST /notes/generate
GET  /notes/<id>
POST /quiz/generate
GET  /quiz/<id>
POST /schedule/generate
GET  /schedule/<id>
POST /export/pdf
GET  /settings
POST /settings
10. MVP Scope

The first version should include:

Local Flask web app.
Desktop .exe launcher.
Setup .bat.
Upload PDF/TXT/DOCX files.
Extract text from files.
Generate AI notes.
Generate AI quizzes.
Generate AI study schedule.
Export notes and quizzes as PDF.
Dark video-background UI based on the uploaded design.
11. Later Features

Future versions can add:

OCR for scanned files.
User accounts on local machine.
Calendar integration.
Spaced repetition flashcards.
Voice explanation mode.
Offline local AI model support.
Drag-and-drop file upload.
Better document preview.
Progress analytics.
Multi-language support.
Mobile-friendly local network mode.
12. Success Criteria

The project is successful when:

The user can run one .exe.
A localhost website opens automatically.
The UI matches the dark glass-style design.
The user can upload school material.
AI can create useful notes.
AI can create quizzes with answers.
AI can build a study schedule.
The user can export study material as PDF.
All data is stored locally unless AI API processing is used.
Setup works from a .bat file without manual technical steps.
13. Suggested Project Name

Main name:

Titan Study Engine

Possible UI subtitle:

Local AI-powered study command center

Possible hero section:

Titan Study Engine
Turn your school files into notes, quizzes, PDFs, and AI-reviewed study plans.

Suggested call-to-action buttons:

Upload Files
Generate Notes
Create Quiz
Build Schedule
Export PDF