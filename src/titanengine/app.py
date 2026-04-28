import json
import os
import shutil
import sqlite3
import sys
import asyncio
import threading
import time
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import errors, types
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DirectoryTree, Footer, Header, Input, Label, Log, Markdown, Select
from titanengine.pdf_export import write_text_pdf
from titanengine.scheduler import (
    extract_json_payload,
    generate_smart_schedule,
    normalize_schedule_analysis,
    parse_iso_date,
    schedule_analysis_to_markdown,
)

try:
    from textual.widgets import TextArea
except ImportError:
    TextArea = None


def choose_source_base_dir():
    preferred = Path(__file__).resolve().parents[2] / "_runtime" / "School"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write_test"
        probe.mkdir(exist_ok=True)
        probe.rmdir()
        return preferred
    except OSError:
        return Path.cwd() / "_runtime" / "School"


# Source checkouts should keep generated data near the repo. Packaged builds use
# a stable user folder so the desktop app can be launched from anywhere.
if os.environ.get("TITAN_ENGINE_SCHOOL_DIR"):
    BASE_DIR = Path(os.environ["TITAN_ENGINE_SCHOOL_DIR"])
elif getattr(sys, "_MEIPASS", None) or getattr(sys, "frozen", False):
    BASE_DIR = Path.home() / "Documents" / "TitanEngine" / "School"
else:
    BASE_DIR = choose_source_base_dir()

# Ensure the base directory exists
BASE_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_DIR = Path.home() / ".titan_engine"
CONFIG_FILE = CONFIG_DIR / "config.json"
SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv"}
MAX_STUDY_FILE_CHARS = 50000
MAX_ANALYSIS_CHARS_PER_FILE = 9000
CHUNK_TARGET_CHARS = MAX_STUDY_FILE_CHARS
MAX_CONCURRENT_CHUNK_REQUESTS = 2
AI_MAX_RETRIES = 3
AI_RETRY_BASE_DELAY = 1.25
GEMINI_MIN_SECONDS_BETWEEN_REQUESTS = 60
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_MODEL_OPTIONS = [
    ("Gemini 2.5 Flash", "gemini-2.5-flash"),
    ("Gemini 2.5 Flash-Lite", "gemini-2.5-flash-lite"),
    ("Gemini 3 Flash Preview", "gemini-3-flash-preview"),
]
SUPPORTED_GEMINI_MODELS = {value for _, value in GEMINI_MODEL_OPTIONS}
SUBJECT_DIRECTORIES = ["Source_Materials", "Revision", "Quizzes", "Schedules", "System"]
gemini_rate_limit_lock = threading.Lock()
last_gemini_request_at = 0.0




def default_config():
    return {
        "api_key": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", ""),
        "ai_provider": "gemini",
        "ai_model": DEFAULT_GEMINI_MODEL,
        "theme": "obsidian",
        "output_length": "balanced",
        "quiz_difficulty": "medium",
        "schedule_preferences": "Short sessions with spaced review",
        "output_language": "English",
    }


def load_config():
    config = default_config()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config.update(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass
    if not config.get("api_key"):
        config["api_key"] = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
    if config.get("ai_model") not in SUPPORTED_GEMINI_MODELS:
        config["ai_model"] = DEFAULT_GEMINI_MODEL
    return config


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def ensure_column(conn, table_name, column_name, declaration):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {declaration}")


def ensure_subject_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subject_profile (
            subject_name TEXT PRIMARY KEY,
            subject_start_date TEXT,
            exam_date TEXT,
            weekly_hours TEXT,
            priority TEXT,
            confidence_level TEXT,
            quiz_style TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS study_plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            task TEXT,
            due_date TEXT,
            duration_minutes INTEGER,
            status TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            file_name TEXT,
            score REAL,
            taken_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            source_file TEXT,
            output_type TEXT,
            output_path TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS course_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            event_type TEXT,
            title TEXT,
            week_number INTEGER,
            event_date TEXT,
            source_file TEXT,
            confidence TEXT,
            notes TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_file_weeks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT,
            source_file TEXT,
            week_start INTEGER,
            week_end INTEGER,
            topic TEXT,
            confidence TEXT
        )
        """
    )
    ensure_column(conn, "subject_profile", "subject_start_date", "TEXT")


def ensure_subject_directories(subject_dir):
    for directory in SUBJECT_DIRECTORIES:
        (subject_dir / directory).mkdir(parents=True, exist_ok=True)


def init_subject_fortress(
    subject_name,
    subject_start_date="",
    exam_date="",
    weekly_hours="",
    priority="medium",
    confidence_level="medium",
    quiz_style="mixed",
):
    safe_subject = subject_name.strip()
    if not safe_subject:
        raise ValueError("Subject name is required.")
    subj_dir = BASE_DIR / safe_subject
    ensure_subject_directories(subj_dir)
    conn = sqlite3.connect(subj_dir / "System" / "state.db")
    ensure_subject_schema(conn)
    conn.execute(
        """
        INSERT INTO subject_profile (
            subject_name, subject_start_date, exam_date, weekly_hours, priority, confidence_level, quiz_style, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(subject_name) DO UPDATE SET
            subject_start_date=excluded.subject_start_date,
            exam_date=excluded.exam_date,
            weekly_hours=excluded.weekly_hours,
            priority=excluded.priority,
            confidence_level=excluded.confidence_level,
            quiz_style=excluded.quiz_style,
            updated_at=excluded.updated_at
        """,
        (
            safe_subject,
            subject_start_date,
            exam_date,
            weekly_hours,
            priority,
            confidence_level,
            quiz_style,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return subj_dir, conn


def open_subject_db(subject_dir):
    ensure_subject_directories(subject_dir)
    conn = sqlite3.connect(subject_dir / "System" / "state.db")
    ensure_subject_schema(conn)
    conn.commit()
    return conn


def infer_subject_dir(path):
    try:
        resolved = path.resolve()
        base = BASE_DIR.resolve()
    except OSError:
        return None
    for parent in [resolved] + list(resolved.parents):
        if parent == base:
            return None
        if parent.parent == base and parent.is_dir():
            return parent
        if (parent / "System").exists() and (parent / "Source_Materials").exists():
            return parent
    return None


def list_subjects():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(path for path in BASE_DIR.iterdir() if path.is_dir())


def recent_files(limit=5):
    files = []
    for subject in list_subjects():
        source_dir = subject / "Source_Materials"
        if source_dir.exists():
            files.extend(path for path in source_dir.rglob("*") if path.is_file())
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def recent_outputs(limit=5):
    rows = []
    for subject in list_subjects():
        if not (subject / "System" / "state.db").exists():
            continue
        try:
            with open_subject_db(subject) as conn:
                rows.extend(
                    conn.execute(
                        """
                        SELECT subject, source_file, output_type, output_path, created_at
                        FROM outputs
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                )
        except sqlite3.Error:
            continue
    return sorted(rows, key=lambda row: row[4], reverse=True)[:limit]


def upcoming_tasks(limit=5):
    rows = []
    for subject in list_subjects():
        if not (subject / "System" / "state.db").exists():
            continue
        try:
            with open_subject_db(subject) as conn:
                rows.extend(
                    conn.execute(
                        """
                        SELECT subject, task, due_date, duration_minutes, status
                        FROM study_plan
                        WHERE status != 'done'
                        ORDER BY due_date ASC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
                )
        except sqlite3.Error:
            continue
    return sorted(rows, key=lambda row: row[2])[:limit]


def dashboard_markdown():
    subjects = list_subjects()
    if not subjects:
        return "\n".join(
            [
                "# Study Dashboard",
                "",
                "No subjects yet.",
                "",
                "- Create your first subject",
                "- Import your first study file into `Source_Materials`",
                "- Open Settings to add your API key",
            ]
        )

    lines = ["# Study Dashboard", "", "## Subjects"]
    lines.extend(f"- {subject.name}" for subject in subjects[:8])

    files = recent_files()
    lines.extend(["", "## Recent Files"])
    if files:
        lines.extend(f"- {path.parent.parent.name}: {path.name}" for path in files)
    else:
        lines.append("- No imported files yet")

    outputs = recent_outputs()
    lines.extend(["", "## Recent Outputs"])
    if outputs:
        lines.extend(
            f"- {row[0]}: {row[2]} from {Path(row[1]).name if row[1] else 'manual'}" for row in outputs
        )
    else:
        lines.append("- No generated notes or quizzes yet")

    tasks = upcoming_tasks()
    lines.extend(["", "## Upcoming Study Tasks"])
    if tasks:
        lines.extend(f"- {row[2]}: {row[0]} - {row[1]} ({row[3]} min)" for row in tasks)
    else:
        lines.append("- No study tasks planned yet")
    return "\n".join(lines)


def read_study_file(path, max_chars=MAX_STUDY_FILE_CHARS):
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_TEXT_EXTENSIONS:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]

    if suffix == ".pdf":
        try:
            from PyPDF2 import PdfReader
        except ImportError as exc:
            raise ValueError("PDF support needs PyPDF2 installed.") from exc
        reader = PdfReader(str(path))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        return text[:max_chars]

    if suffix == ".docx":
        try:
            from docx import Document
        except ImportError as exc:
            raise ValueError("DOCX support needs python-docx installed.") from exc
        document = Document(str(path))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        return text[:max_chars]

    raise ValueError(f"{suffix or 'This file type'} is not supported yet.")


def split_long_paragraph(paragraph, chunk_size):
    sentences = paragraph.replace("? ", "?\n").replace("! ", "!\n").replace(". ", ".\n").splitlines()
    chunks = []
    current = []
    current_size = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > chunk_size:
            if current:
                chunks.append(" ".join(current))
                current = []
                current_size = 0
            chunks.extend(sentence[index : index + chunk_size] for index in range(0, len(sentence), chunk_size))
            continue
        next_size = current_size + len(sentence) + 1
        if current and next_size > chunk_size:
            chunks.append(" ".join(current))
            current = [sentence]
            current_size = len(sentence)
        else:
            current.append(sentence)
            current_size = next_size

    if current:
        chunks.append(" ".join(current))
    return chunks


def chunk_text(text, chunk_size=CHUNK_TARGET_CHARS):
    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    chunks = []
    current = []
    current_size = 0

    for paragraph in paragraphs:
        paragraph_size = len(paragraph)
        if paragraph_size > chunk_size:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_size = 0
            chunks.extend(split_long_paragraph(paragraph, chunk_size))
            continue

        next_size = current_size + paragraph_size + 2
        if current and next_size > chunk_size:
            chunks.append("\n\n".join(current))
            current = [paragraph]
            current_size = paragraph_size
        else:
            current.append(paragraph)
            current_size = next_size

    if current:
        chunks.append("\n\n".join(current))
    return chunks or [""]


def store_output_metadata(subject_dir, subject, source_file, output_type, output_path):
    with open_subject_db(subject_dir) as conn:
        conn.execute(
            """
            INSERT INTO outputs (subject, source_file, output_type, output_path, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                subject,
                str(source_file) if source_file else "",
                output_type,
                str(output_path),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()


def save_generated_output(subject_dir, source_file, output_type, content):
    folders = {
        "notes": "Revision",
        "quiz": "Quizzes",
        "schedule": "Schedules",
    }
    target_dir = subject_dir / folders.get(output_type, "Revision")
    target_dir.mkdir(parents=True, exist_ok=True)
    source_stem = source_file.stem if source_file else subject_dir.name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = target_dir / f"{source_stem}_{output_type}_{timestamp}.pdf"
    write_text_pdf(out_path, f"{source_stem} {output_type}", content)
    store_output_metadata(subject_dir, subject_dir.name, source_file, output_type, out_path)
    return out_path


def import_file_to_subject(source_file, subject_dir):
    target_dir = subject_dir / "Source_Materials"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source_file.name
    if source_file.resolve() != target.resolve():
        shutil.copy2(source_file, target)
    return target


MASTER_PROMPT = """
You are an AI study assistant inside a local desktop application.
Your job is to help the user:
1. summarize study material clearly,
2. generate revision notes,
3. create quizzes,
4. help build realistic study schedules.

Be concise, accurate, and student-friendly.
Use headings and bullet points when useful.
"""


async def run_agent(api_key, role_prompt, user_data, model=DEFAULT_GEMINI_MODEL):
    if not api_key:
        return (
            "[ERROR] Missing Gemini API key.\n\n"
            "Open Settings and paste your Google AI Studio key, or set GEMINI_API_KEY or GOOGLE_API_KEY."
        )

    system_instruction = MASTER_PROMPT + "\n\n" + role_prompt

    for attempt in range(1, AI_MAX_RETRIES + 1):
        try:
            await asyncio.to_thread(wait_for_gemini_rate_limit)
            async with genai.Client(api_key=api_key).aio as client:
                response = await client.models.generate_content(
                    model=model,
                    contents=user_data,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=0.3,
                    ),
                )
            return response.text or "[INFO] Gemini returned an empty response."
        except errors.APIError as exc:
            code = getattr(exc, "code", None)
            if attempt < AI_MAX_RETRIES and code in {429, 500, 503, 504}:
                await asyncio.sleep(AI_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
                continue

            message = getattr(exc, "message", str(exc))
            return (
                "[ERROR] Gemini API call failed.\n\n"
                f"HTTP {code or 'unknown'}: {message}"
            )
        except Exception as exc:
            if attempt < AI_MAX_RETRIES:
                await asyncio.sleep(AI_RETRY_BASE_DELAY * (2 ** (attempt - 1)))
                continue

            return (
                "[ERROR] Gemini API call failed.\n\n"
                f"{type(exc).__name__}: {exc}"
            )


def wait_for_gemini_rate_limit():
    global last_gemini_request_at

    with gemini_rate_limit_lock:
        now = time.monotonic()
        elapsed = now - last_gemini_request_at
        if last_gemini_request_at and elapsed < GEMINI_MIN_SECONDS_BETWEEN_REQUESTS:
            time.sleep(GEMINI_MIN_SECONDS_BETWEEN_REQUESTS - elapsed)
        last_gemini_request_at = time.monotonic()


async def summarize_chunks(api_key, role_prompt, content, config):
    chunks = chunk_text(content)
    model = config.get("ai_model", DEFAULT_GEMINI_MODEL)
    if len(chunks) == 1:
        return await run_agent(api_key, role_prompt, content, model)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHUNK_REQUESTS)

    async def summarize_chunk(index, chunk):
        async with semaphore:
            return await run_agent(
                api_key,
                "Summarize this source chunk for later synthesis. Keep key facts and examples.",
                f"Chunk {index} of {len(chunks)}:\n\n{chunk}",
                model,
            )

    partials = await asyncio.gather(
        *(summarize_chunk(index, chunk) for index, chunk in enumerate(chunks, start=1))
    )

    combined = "\n\n".join(f"Chunk {index} summary:\n{summary}" for index, summary in enumerate(partials, start=1))
    return await run_agent(api_key, role_prompt, combined, model)


async def generate_revision(api_key, content, config):
    role = (
        "Create clear revision notes with headings, key concepts, examples, and short summaries. "
        f"Length: {config.get('output_length', 'balanced')}. "
        f"Language: {config.get('output_language', 'English')}."
    )
    return await summarize_chunks(api_key, role, content, config)


async def generate_quiz(api_key, content, config):
    role = (
        "Create an interactive-ready quiz in markdown. Use multiple-choice and short-answer questions. "
        "For every question include the answer and a short explanation under an Answer section. "
        f"Difficulty: {config.get('quiz_difficulty', 'medium')}. "
        f"Language: {config.get('output_language', 'English')}."
    )
    return await summarize_chunks(api_key, role, content, config)


def collect_schedule_sources(subject_dir):
    source_dir = subject_dir / "Source_Materials"
    if not source_dir.exists():
        return []

    sources = []
    for path in sorted(source_dir.iterdir()):
        if not path.is_file():
            continue
        try:
            content = read_study_file(path, max_chars=MAX_ANALYSIS_CHARS_PER_FILE)
        except (OSError, ValueError):
            content = ""
        sources.append(
            {
                "source_file": path.name,
                "file_stem": path.stem,
                "text_excerpt": content,
            }
        )
    return sources


async def analyze_course_schedule(api_key, subject, subject_start_date, sources, config):
    role = (
        "Extract course schedule structure from study files. Return only valid JSON, no markdown. "
        "The source files may be in English, Estonian, Russian, or a mix of those languages. "
        "Understand common course words across those languages, including: test, exam, quiz, assignment, "
        "project, lecture, lesson, attendance; kontrolltöö, test, eksam, arvestus, projekt, loeng, seminar, "
        "praktikum, tähtaeg; тест, контрольная работа, экзамен, зачёт, проект, домашнее задание, лекция, "
        "семинар, занятие, срок. "
        "Keep detected titles in the source language when useful, but always use the normalized English "
        "event type values required by the JSON schema. "
        "Detect tests, exams, projects, deadlines, attendance/lesson requirements, and map each source file "
        "to the course week or week range it covers. If an item says week 8 or 15, preserve that week number. "
        "Use this schema exactly: "
        '{"course_length_weeks":16,"events":[{"type":"test|exam|project|attendance|deadline|other",'
        '"title":"...","week":8,"date":"YYYY-MM-DD or empty","source_file":"...","confidence":"low|medium|high",'
        '"notes":"..."}],"source_file_weeks":[{"source_file":"...","week_start":1,"week_end":1,'
        '"topic":"...","confidence":"low|medium|high"}]}.'
    )
    payload = {
        "subject": subject,
        "subject_start_date": subject_start_date,
        "week_date_rule": "Week 1 starts on subject_start_date. Week N starts 7*(N-1) days later.",
        "sources": sources,
    }
    response = await run_agent(api_key, role, json.dumps(payload, ensure_ascii=False), config.get("ai_model", DEFAULT_GEMINI_MODEL))
    if response.startswith("[ERROR]"):
        raise ValueError(response)
    try:
        parsed = extract_json_payload(response)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{exc}\n\nRaw AI response:\n{response}") from exc
    analysis = normalize_schedule_analysis(parsed, subject_start_date)
    analysis["raw_response"] = response
    return analysis


def persist_schedule_analysis(conn, subject, analysis):
    conn.execute("DELETE FROM course_events WHERE subject = ?", (subject,))
    conn.execute("DELETE FROM source_file_weeks WHERE subject = ?", (subject,))
    conn.executemany(
        """
        INSERT INTO course_events (
            subject, event_type, title, week_number, event_date, source_file, confidence, notes
        ) VALUES (:subject, :event_type, :title, :week_number, :event_date, :source_file, :confidence, :notes)
        """,
        [{"subject": subject, **event} for event in analysis.get("events", [])],
    )
    conn.executemany(
        """
        INSERT INTO source_file_weeks (
            subject, source_file, week_start, week_end, topic, confidence
        ) VALUES (:subject, :source_file, :week_start, :week_end, :topic, :confidence)
        """,
        [{"subject": subject, **mapping} for mapping in analysis.get("source_file_weeks", [])],
    )


def schedule_to_markdown(subject, plan):
    lines = [f"# Weekly Study Schedule: {subject}", ""]
    for item in plan:
        day_name = datetime.strptime(item["due_date"], "%Y-%m-%d").strftime("%A")
        duration = f"{item['duration_minutes']} min" if item["duration_minutes"] else "Reminder"
        lines.append(f"- {day_name} {item['due_date']}: {duration} - {item['task']}")
    return "\n".join(lines)


class SubjectWizardScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="wizard_container"):
            yield Markdown("# New Subject\nCreate a study workspace with schedule metadata.")
            yield Label("Subject name")
            yield Input(placeholder="Math, Biology, History...", id="subject_name")
            yield Label("Subject begin date (YYYY-MM-DD)")
            yield Input(placeholder="2026-02-02", id="subject_start_date")
            yield Label("Exam date (YYYY-MM-DD)")
            yield Input(placeholder="2026-06-30", id="exam_date")
            yield Label("Weekly study hours")
            yield Input(placeholder="4", id="weekly_hours")
            yield Label("Priority")
            yield Select(
                options=[("High", "high"), ("Medium", "medium"), ("Low", "low")],
                value="medium",
                id="priority",
            )
            yield Label("Confidence level")
            yield Select(
                options=[("High", "high"), ("Medium", "medium"), ("Low", "low")],
                value="medium",
                id="confidence_level",
            )
            yield Label("Preferred quiz style")
            yield Select(
                options=[("Mixed", "mixed"), ("Multiple choice", "mcq"), ("Short answer", "short_answer"), ("Exam style", "exam")],
                value="mixed",
                id="quiz_style",
            )
            with Horizontal(classes="button_row"):
                yield Button("Create Subject", id="create_subject", variant="success")
                yield Button("Cancel", id="cancel_subject")
            yield Label("", id="wizard_status")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel_subject":
            self.app.pop_screen()
            return
        if event.button.id != "create_subject":
            return

        subject_name = self.query_one("#subject_name", Input).value.strip()
        subject_start_date = self.query_one("#subject_start_date", Input).value.strip()
        exam_date = self.query_one("#exam_date", Input).value.strip()
        weekly_hours = self.query_one("#weekly_hours", Input).value.strip()
        priority = self.query_one("#priority", Select).value
        confidence = self.query_one("#confidence_level", Select).value
        quiz_style = self.query_one("#quiz_style", Select).value

        if not subject_name:
            self.query_one("#wizard_status", Label).update("Subject name is required.")
            return
        if not parse_iso_date(subject_start_date):
            self.query_one("#wizard_status", Label).update("Subject begin date must use YYYY-MM-DD.")
            return
        if exam_date and not parse_iso_date(exam_date):
            self.query_one("#wizard_status", Label).update("Exam date must use YYYY-MM-DD or stay empty.")
            return

        try:
            init_subject_fortress(subject_name, subject_start_date, exam_date, weekly_hours, priority, confidence, quiz_style)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self.query_one("#wizard_status", Label).update(f"Could not create subject: {exc}")
            return

        self.app.pop_screen()
        main = self.app.query_one(MainScreen)
        main.refresh_dashboard()
        main.log_message(f"[DONE] Subject created: {subject_name}")


class SettingsScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        config = load_config()
        yield Header()
        with Container(id="settings_container"):
            yield Markdown("# Settings")
            yield Label("Gemini API Key")
            yield Input(value=config.get("api_key", ""), password=True, id="api_key_input")
            yield Label("Gemini model")
            yield Select(
                options=GEMINI_MODEL_OPTIONS,
                value=config.get("ai_model", DEFAULT_GEMINI_MODEL),
                id="ai_model",
            )
            yield Label("Theme")
            yield Select(
                options=[("Academic Dark", "obsidian"), ("Cyberpunk Red", "cyberpunk"), ("Midnight Blue", "midnight"), ("Ghost White", "ghost")],
                value=config.get("theme", "obsidian"),
                id="theme_select",
            )
            yield Label("Default output length")
            yield Select(
                options=[("Concise", "concise"), ("Balanced", "balanced"), ("Detailed", "detailed")],
                value=config.get("output_length", "balanced"),
                id="output_length",
            )
            yield Label("Quiz difficulty")
            yield Select(
                options=[("Easy", "easy"), ("Medium", "medium"), ("Hard", "hard")],
                value=config.get("quiz_difficulty", "medium"),
                id="quiz_difficulty",
            )
            yield Label("Schedule preferences")
            yield Input(value=config.get("schedule_preferences", ""), id="schedule_preferences")
            yield Label("Output language")
            yield Input(value=config.get("output_language", "English"), id="output_language")
            with Horizontal(classes="button_row"):
                yield Button("Test API Key", id="test_key")
                yield Button("Save & Apply", id="save_btn", variant="success")
            yield Label("", id="settings_status")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "test_key":
            key = self.query_one("#api_key_input", Input).value.strip()
            status = self.query_one("#settings_status", Label)
            if not key:
                status.update("No Gemini API key found.")
                return

            async def test_key():
                status.update("Testing Gemini API key...")
                try:
                    await asyncio.to_thread(wait_for_gemini_rate_limit)
                    async with genai.Client(api_key=key).aio as client:
                        response = await client.models.generate_content(
                            model=DEFAULT_GEMINI_MODEL,
                            contents="Reply with exactly: OK",
                        )
                    status.update(f"Key works. Model replied: {(response.text or '').strip()}")
                except errors.APIError as exc:
                    code = getattr(exc, "code", None)
                    message = getattr(exc, "message", str(exc))
                    status.update(f"Gemini error {code or 'unknown'}: {message}")
                except Exception as exc:
                    status.update(f"{type(exc).__name__}: {exc}")

            self.run_worker(test_key(), exclusive=True)
            return

        if event.button.id == "save_btn":
            config = {
                "api_key": self.query_one("#api_key_input", Input).value.strip(),
                "ai_provider": "gemini",
                "ai_model": self.query_one("#ai_model", Select).value,
                "theme": self.query_one("#theme_select", Select).value,
                "output_length": self.query_one("#output_length", Select).value,
                "quiz_difficulty": self.query_one("#quiz_difficulty", Select).value,
                "schedule_preferences": self.query_one("#schedule_preferences", Input).value.strip(),
                "output_language": self.query_one("#output_language", Input).value.strip() or "English",
            }
            save_config(config)
            self.app.config = load_config()
            self.app.apply_theme()
            self.app.pop_screen()
            try:
                self.app.query_one(MainScreen).log_message("[DONE] Settings saved.")
            except Exception:
                pass


class ScheduleReviewScreen(Screen):
    BINDINGS = [Binding("escape", "cancel_review", "Cancel")]

    def __init__(self, subject_dir, profile, analysis):
        super().__init__()
        self.subject_dir = subject_dir
        self.profile = profile
        self.analysis = analysis

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="schedule_review_container"):
            yield Markdown(
                schedule_analysis_to_markdown(
                    self.subject_dir.name,
                    self.profile["subject_start_date"],
                    self.analysis,
                ),
                id="schedule_review_markdown",
            )
            with Horizontal(classes="button_row"):
                yield Button("Accept & Build Schedule", id="accept_schedule", variant="success")
                yield Button("Regenerate Analysis", id="regenerate_schedule")
                yield Button("Cancel", id="cancel_schedule")
            yield Label("Review AI-detected events before saving study tasks.", id="schedule_review_status")
        yield Footer()

    def action_cancel_review(self):
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel_schedule":
            self.app.pop_screen()
            return
        if event.button.id == "regenerate_schedule":
            self.app.pop_screen()
            self.app.query_one(MainScreen).action_build_schedule()
            return
        if event.button.id != "accept_schedule":
            return

        plan = generate_smart_schedule(
            self.subject_dir.name,
            self.profile["subject_start_date"],
            self.analysis,
            self.profile["weekly_hours"],
            self.profile["confidence_level"],
        )
        try:
            with open_subject_db(self.subject_dir) as conn:
                persist_schedule_analysis(conn, self.subject_dir.name, self.analysis)
                conn.execute("DELETE FROM study_plan WHERE subject = ? AND status = 'planned'", (self.subject_dir.name,))
                conn.executemany(
                    """
                    INSERT INTO study_plan (subject, task, due_date, duration_minutes, status)
                    VALUES (:subject, :task, :due_date, :duration_minutes, :status)
                    """,
                    plan,
                )
                conn.commit()
        except sqlite3.Error as exc:
            self.query_one("#schedule_review_status", Label).update(f"Could not save accepted schedule: {exc}")
            return

        markdown = schedule_to_markdown(self.subject_dir.name, plan)
        self.app.pop_screen()
        main = self.app.query_one(MainScreen)
        main.set_pending_output(self.subject_dir, None, "schedule", markdown)
        main.refresh_dashboard()
        main.set_status("Accepted schedule ready for PDF save")
        main.log_message("[DONE] Accepted schedule built. Review it, then Save to export PDF.")


class MainScreen(Screen):
    BINDINGS = [
        Binding("f2", "push_screen('settings')", "Settings"),
        Binding("ctrl+n", "push_screen('subject_wizard')", "New Subject"),
        Binding("ctrl+i", "import_material", "Import Material"),
        Binding("enter", "generate_notes", "Generate Notes"),
        Binding("ctrl+q", "generate_quiz", "Generate Quiz"),
        Binding("ctrl+s", "save_output", "Save Output"),
        Binding("ctrl+b", "batch_generate", "Batch Generate"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="app_body"):
            with Horizontal(id="toolbar"):
                yield Button("New Subject", id="btn_new_subject", variant="primary")
                yield Button("Import Material", id="btn_import")
                yield Button("Generate Notes", id="btn_notes")
                yield Button("Generate Quiz", id="btn_quiz")
                yield Button("Build Schedule", id="btn_schedule")
                yield Button("Batch Generate", id="btn_batch")
                yield Button("Settings", id="btn_settings")
            with Horizontal(id="workspace"):
                with Vertical(id="left_panel"):
                    yield Markdown(dashboard_markdown(), id="dashboard")
                    yield DirectoryTree(str(BASE_DIR), id="tree")
                with Vertical(id="center_panel"):
                    yield Markdown("# Preview\nChoose a file and generate notes, a quiz, or a schedule.", id="preview")
                    if TextArea:
                        yield TextArea("", id="draft_editor")
                    else:
                        yield Input("", id="draft_editor")
                    with Horizontal(id="review_bar"):
                        yield Button("Save", id="btn_save", variant="success")
                        yield Button("Regenerate", id="btn_regenerate")
                        yield Button("Copy", id="btn_copy")
                        yield Button("Export PDF", id="btn_export")
                with Vertical(id="right_panel"):
                    yield Markdown(
                        "## Actions\n"
                        "- Select a source file for notes or quizzes\n"
                        "- Select a subject folder for AI schedules\n"
                        "- Review detected course events before saving PDFs",
                        id="actions_help",
                    )
                    yield Label("Status: Idle", id="status_label")
            yield Log(id="matrix_log")
        yield Footer()

    def on_mount(self) -> None:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        self.pending_output = None
        self.last_action = "notes"
        self.app.sub_title = "Dashboard"
        self.clear_pending_output()
        self.log_message("[READY] Study workspace mounted. Use visible buttons or shortcuts.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "btn_new_subject":
            self.app.push_screen("subject_wizard")
        elif button_id == "btn_import":
            self.action_import_material()
        elif button_id == "btn_notes":
            self.action_generate_notes()
        elif button_id == "btn_quiz":
            self.action_generate_quiz()
        elif button_id == "btn_schedule":
            self.action_build_schedule()
        elif button_id == "btn_batch":
            self.action_batch_generate()
        elif button_id == "btn_settings":
            self.app.push_screen("settings")
        elif button_id in {"btn_save", "btn_export"}:
            self.action_save_output()
        elif button_id == "btn_regenerate":
            self.regenerate_last_action()
        elif button_id == "btn_copy":
            self.copy_draft()

    def log_message(self, message):
        self.query_one("#matrix_log", Log).write_line(message)

    def set_status(self, message):
        self.query_one("#status_label", Label).update(f"Status: {message}")

    def refresh_dashboard(self):
        self.query_one("#dashboard", Markdown).update(dashboard_markdown())
        tree = self.query_one("#tree", DirectoryTree)
        if hasattr(tree, "reload"):
            tree.reload()

    def selected_path(self):
        tree = self.query_one("#tree", DirectoryTree)
        node = tree.cursor_node
        if node and node.data:
            return Path(node.data.path)
        return BASE_DIR

    def selected_subject_dir(self):
        subject_dir = infer_subject_dir(self.selected_path())
        if subject_dir:
            return subject_dir
        subjects = list_subjects()
        return subjects[0] if subjects else None

    def selected_source_file(self):
        selected = self.selected_path()
        return selected if selected.is_file() else None

    def set_draft_text(self, text):
        editor = self.query_one("#draft_editor")
        if TextArea and isinstance(editor, TextArea):
            editor.load_text(text)
        else:
            editor.value = text
        self.query_one("#preview", Markdown).update(text or "# Preview\nNo draft yet.")

    def get_draft_text(self):
        editor = self.query_one("#draft_editor")
        if TextArea and isinstance(editor, TextArea):
            return editor.text
        return editor.value

    def set_pending_output(self, subject_dir, source_file, output_type, content):
        self.pending_output = {
            "subject_dir": subject_dir,
            "source_file": source_file,
            "output_type": output_type,
        }
        self.set_draft_text(content)
        for button_id in ("#btn_save", "#btn_regenerate", "#btn_copy", "#btn_export"):
            self.query_one(button_id, Button).disabled = False
        self.set_status("Completed - review before saving")
        self.log_message("[DONE] Draft ready. Review, edit, then Save or Export PDF.")

    def clear_pending_output(self):
        self.pending_output = None
        for button_id in ("#btn_save", "#btn_regenerate", "#btn_copy", "#btn_export"):
            self.query_one(button_id, Button).disabled = True

    def friendly_read_error(self, exc):
        return (
            "Could not read that file. Supported imports are .txt, .md, .pdf, and .docx. "
            f"Details: {exc}"
        )

    def action_import_material(self) -> None:
        selected = self.selected_source_file()
        subject_dir = self.selected_subject_dir()
        if not subject_dir:
            self.log_message("[ERROR] Create or select a subject before importing material.")
            self.set_status("Failed")
            return
        if not selected:
            self.log_message("[ERROR] Select a file to import into Source_Materials.")
            self.set_status("Failed")
            return

        try:
            target = import_file_to_subject(selected, subject_dir)
        except OSError as exc:
            self.log_message(f"[ERROR] Import failed: {exc}")
            self.set_status("Failed")
            return

        self.refresh_dashboard()
        self.set_status("Imported")
        self.log_message(f"[DONE] Imported {target.name} into {subject_dir.name}.")

    @work(exclusive=True, thread=True)
    async def generate_from_selected_file(self, output_type):
        log = self.query_one("#matrix_log", Log)
        selected = self.selected_source_file()
        if not selected:
            self.app.call_from_thread(self.set_status, "Failed")
            self.app.call_from_thread(log.write_line, "[ERROR] Select a source file first.")
            return

        subject_dir = infer_subject_dir(selected)
        if not subject_dir:
            self.app.call_from_thread(self.set_status, "Failed")
            self.app.call_from_thread(log.write_line, "[ERROR] Put the file inside a subject folder or create a subject first.")
            return

        self.last_action = output_type
        self.app.call_from_thread(self.set_status, f"Reading {selected.name}")
        self.app.call_from_thread(log.write_line, f"[READING] {selected.name}")

        try:
            content = read_study_file(selected)
        except (OSError, ValueError) as exc:
            self.app.call_from_thread(self.set_status, "Failed")
            self.app.call_from_thread(log.write_line, f"[ERROR] {self.friendly_read_error(exc)}")
            return

        self.app.call_from_thread(self.set_status, f"Generating {output_type}")
        self.app.call_from_thread(log.write_line, f"[GENERATING] {output_type.title()} for {selected.name}")
        api_key = self.app.config.get("api_key")
        if output_type == "quiz":
            result = await generate_quiz(api_key, content, self.app.config)
        else:
            result = await generate_revision(api_key, content, self.app.config)

        self.app.call_from_thread(self.set_pending_output, subject_dir, selected, output_type, result)

    def action_generate_notes(self) -> None:
        self.generate_from_selected_file("notes")

    def action_generate_quiz(self) -> None:
        self.generate_from_selected_file("quiz")

    def regenerate_last_action(self):
        if self.last_action == "schedule":
            self.action_build_schedule()
        else:
            self.generate_from_selected_file(self.last_action)

    def action_save_output(self) -> None:
        if not self.pending_output:
            self.log_message("[ERROR] Nothing to save yet.")
            return

        content = self.get_draft_text().strip()
        if not content:
            self.log_message("[ERROR] Draft is empty.")
            return

        output_path = save_generated_output(
            self.pending_output["subject_dir"],
            self.pending_output["source_file"],
            self.pending_output["output_type"],
            content,
        )
        self.clear_pending_output()
        self.refresh_dashboard()
        self.set_status("Saved")
        self.log_message(f"[SAVED] {output_path}")

    def copy_draft(self):
        content = self.get_draft_text()
        if hasattr(self.app, "copy_to_clipboard"):
            self.app.copy_to_clipboard(content)
            self.log_message("[DONE] Draft copied to clipboard.")
        else:
            self.log_message("[INFO] Clipboard copy is unavailable in this Textual version.")

    @work(exclusive=True, thread=True)
    async def action_build_schedule(self) -> None:
        subject_dir = self.selected_subject_dir()
        log = self.query_one("#matrix_log", Log)
        if not subject_dir:
            self.app.call_from_thread(self.set_status, "Failed")
            self.app.call_from_thread(log.write_line, "[ERROR] Create a subject before building a schedule.")
            return

        self.last_action = "schedule"
        self.app.call_from_thread(self.set_status, "Analyzing course files")
        self.app.call_from_thread(self.clear_pending_output)
        self.app.call_from_thread(log.write_line, f"[SCHEDULE] Analyzing course files for {subject_dir.name}")

        try:
            with open_subject_db(subject_dir) as conn:
                profile = conn.execute(
                    """
                    SELECT subject_start_date, exam_date, weekly_hours, confidence_level
                    FROM subject_profile
                    WHERE subject_name = ?
                    """,
                    (subject_dir.name,),
                ).fetchone()
                subject_start_date, exam_date, weekly_hours, confidence = profile if profile else ("", "", "4", "medium")
        except (OSError, sqlite3.Error) as exc:
            self.app.call_from_thread(self.set_status, "Failed")
            self.app.call_from_thread(log.write_line, f"[ERROR] Could not read subject profile: {exc}")
            return

        if not parse_iso_date(subject_start_date):
            self.app.call_from_thread(self.set_status, "Failed")
            self.app.call_from_thread(
                log.write_line,
                "[ERROR] Add a subject begin date in YYYY-MM-DD format before building an AI schedule.",
            )
            return

        sources = collect_schedule_sources(subject_dir)
        if not sources:
            self.app.call_from_thread(self.set_status, "Failed")
            self.app.call_from_thread(log.write_line, "[ERROR] Import at least one source file before building a schedule.")
            return

        try:
            analysis = await analyze_course_schedule(
                self.app.config.get("api_key"),
                subject_dir.name,
                subject_start_date,
                sources,
                self.app.config,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            self.app.call_from_thread(self.set_status, "Failed")
            self.app.call_from_thread(log.write_line, f"[ERROR] Could not parse AI schedule analysis: {exc}")
            self.app.call_from_thread(self.clear_pending_output)
            self.app.call_from_thread(
                self.set_draft_text,
                f"# Schedule Analysis Failed\n\nThe AI did not return usable JSON.\n\n```text\n{exc}\n```",
            )
            return

        profile_data = {
            "subject_start_date": subject_start_date,
            "exam_date": exam_date,
            "weekly_hours": weekly_hours or "4",
            "confidence_level": confidence or "medium",
        }
        self.app.call_from_thread(self.set_status, "Review detected events")
        self.app.call_from_thread(log.write_line, "[SCHEDULE] Review detected events before building the schedule.")
        self.app.call_from_thread(self.app.push_screen, ScheduleReviewScreen(subject_dir, profile_data, analysis))

    @work(exclusive=True, thread=True)
    async def action_batch_generate(self) -> None:
        log = self.query_one("#matrix_log", Log)
        self.app.call_from_thread(self.set_status, "Batch processing")
        self.app.call_from_thread(log.write_line, "[BATCH] Starting notes generation for missing source outputs.")

        for subject_dir in list_subjects():
            with open_subject_db(subject_dir):
                pass
            src_dir = subject_dir / "Source_Materials"
            if not src_dir.exists():
                continue

            for file_path in src_dir.iterdir():
                if not file_path.is_file():
                    continue
                existing = list((subject_dir / "Revision").glob(f"{file_path.stem}_notes_*.pdf"))
                if existing:
                    continue

                self.app.call_from_thread(log.write_line, f"[BATCH] Generating notes for {file_path.name}")
                try:
                    content = read_study_file(file_path)
                except (OSError, ValueError) as exc:
                    self.app.call_from_thread(log.write_line, f"[ERROR] {file_path.name}: {exc}")
                    continue

                result = await generate_revision(self.app.config.get("api_key"), content, self.app.config)
                output_path = save_generated_output(subject_dir, file_path, "notes", result)
                self.app.call_from_thread(log.write_line, f"[SAVED] {output_path.name}")

        self.app.call_from_thread(self.refresh_dashboard)
        self.app.call_from_thread(self.set_status, "Completed")
        self.app.call_from_thread(log.write_line, "[DONE] Batch generation complete.")


class TitanApp(App):
    CSS = """
    Screen {
        background: #111111;
        color: #f3eee6;
    }

    #app_body {
        height: 1fr;
    }

    Header {
        background: #1d1b18;
        border-bottom: solid #d89b3d;
        text-style: bold;
    }

    Footer {
        background: #1d1b18;
        border-top: solid #d89b3d;
    }

    #toolbar {
        height: auto;
        padding: 1;
        background: #1d1b18;
        border-bottom: solid #d89b3d;
    }

    Button {
        margin-right: 1;
        min-width: 13;
        background: #3f7f83;
        color: #ffffff;
        border: solid #d89b3d;
    }

    Button:hover {
        background: #d89b3d;
        text-style: bold;
    }

    Button.success {
        background: #27ae60;
        border: solid #27ae60;
    }

    Button.success:hover {
        background: #f39c12;
        text-style: bold;
    }

    Button.primary {
        background: #d89b3d;
        border: solid #d89b3d;
    }

    Button:disabled {
        background: #2c3e50;
        color: #7f8c8d;
        opacity: 0.5;
    }

    #workspace {
        height: 1fr;
    }

    #left_panel {
        width: 30%;
        min-width: 32;
        background: #171717;
        border-right: solid #d89b3d;
    }

    #dashboard {
        height: 50%;
        padding: 1;
        border-bottom: solid #d89b3d;
        background: #1d1b18;
    }

    Markdown {
        background: transparent;
    }

    DirectoryTree {
        height: 50%;
        padding: 0 1;
        background: #1d1b18;
    }

    #center_panel {
        width: 50%;
        background: #1d1b18;
    }

    #preview {
        height: 45%;
        padding: 1;
        background: #2c3e50;
        border-bottom: solid #d89b3d;
    }

    #draft_editor {
        height: 1fr;
        border-bottom: solid #d89b3d;
        background: #111111;
    }

    #review_bar {
        height: auto;
        padding: 1;
        background: #1d1b18;
        border-top: solid #d89b3d;
    }

    #right_panel {
        width: 20%;
        min-width: 28;
        padding: 1;
        background: #171717;
        border-left: solid #d89b3d;
    }

    Log {
        border-top: solid #d89b3d;
        height: 8;
        background: #1d1b18;
    }

    Label {
        text-style: bold;
        color: #7f8c8d;
    }

    Input,
    Select {
        margin-bottom: 1;
        border: solid #d89b3d;
        background: #1d1b18;
    }

    #settings_container,
    #wizard_container,
    #schedule_review_container {
        padding: 2;
        width: 70%;
        height: auto;
        border: heavy #d89b3d;
        background: #1d1b18;
        content-align: center middle;
    }

    #schedule_review_markdown {
        height: 1fr;
        padding: 1;
    }

    .button_row {
        height: auto;
        margin-top: 1;
    }

    /* Cyberpunk Theme */
    .theme-cyberpunk Screen {
        background: #0a0a0a;
        color: #00ff00;
    }

    .theme-cyberpunk Header,
    .theme-cyberpunk Footer,
    .theme-cyberpunk #toolbar,
    .theme-cyberpunk #review_bar {
        background: #1a0a2e;
        border-bottom: heavy #ff006e;
        text-style: bold;
    }

    .theme-cyberpunk Button {
        background: #16213e;
        color: #00f5ff;
        border: solid #00f5ff;
        text-style: bold;
    }

    .theme-cyberpunk Button:hover {
        background: #0f4c75;
        border: heavy #ff006e;
        color: #ff006e;
    }

    .theme-cyberpunk Button.success {
        background: #2a9d8f;
        border: solid #2a9d8f;
        color: #000;
    }

    .theme-cyberpunk Button.success:hover {
        background: #00f5ff;
        text-style: bold;
    }

    .theme-cyberpunk #left_panel,
    .theme-cyberpunk #right_panel {
        background: #0f3460;
        border-right: solid #00f5ff;
    }

    .theme-cyberpunk #center_panel {
        background: #16213e;
    }

    .theme-cyberpunk #preview {
        background: #16213e 30%;
        border-bottom: solid #00f5ff;
    }

    .theme-cyberpunk #dashboard {
        background: #0f3460;
        border-bottom: solid #00f5ff;
    }

    .theme-cyberpunk DirectoryTree {
        background: #0f3460;
    }

    .theme-cyberpunk Log {
        background: #1a0a2e;
        border-top: heavy #00f5ff;
    }

    .theme-cyberpunk #settings_container,
    .theme-cyberpunk #wizard_container,
    .theme-cyberpunk #schedule_review_container {
        border: heavy #00f5ff;
        background: #0f3460;
    }

    .theme-cyberpunk Input,
    .theme-cyberpunk Select {
        border: solid #00f5ff;
        background: #16213e;
        color: #00f5ff;
    }

    /* Midnight Blue Theme */
    .theme-midnight Screen {
        background: #0d1b2a;
        color: #e0f4ff;
    }

    .theme-midnight Header,
    .theme-midnight Footer,
    .theme-midnight #toolbar,
    .theme-midnight #review_bar {
        background: #1a3a52;
        border-bottom: heavy #00d9ff;
        text-style: bold;
    }

    .theme-midnight Button {
        background: #0f3f66;
        color: #00d9ff;
        border: solid #00d9ff;
        text-style: bold;
    }

    .theme-midnight Button:hover {
        background: #1a5f99;
        border: heavy #00d9ff;
        color: #ffffff;
        text-style: bold;
    }

    .theme-midnight Button.success {
        background: #2d7a5a;
        border: solid #4dff99;
        color: #ffffff;
    }

    .theme-midnight Button.success:hover {
        background: #4dff99;
        color: #0d1b2a;
        text-style: bold;
    }

    .theme-midnight #left_panel,
    .theme-midnight #right_panel {
        background: #1a3a52;
        border: solid #00d9ff;
    }

    .theme-midnight #center_panel {
        background: #0f2d45;
    }

    .theme-midnight #preview {
        background: #1a3a52 30%;
        border-bottom: solid #00d9ff;
    }

    .theme-midnight #dashboard {
        background: #1a3a52;
        border-bottom: solid #00d9ff;
    }

    .theme-midnight DirectoryTree {
        background: #1a3a52;
    }

    .theme-midnight Log {
        background: #1a3a52;
        border-top: heavy #00d9ff;
    }

    .theme-midnight #settings_container,
    .theme-midnight #wizard_container,
    .theme-midnight #schedule_review_container {
        border: heavy #00d9ff;
        background: #1a3a52;
    }

    .theme-midnight Input,
    .theme-midnight Select {
        border: solid #00d9ff;
        background: #0f3f66;
        color: #00d9ff;
    }

    /* Ghost Theme (Light) */
    .theme-ghost Screen {
        background: #f8f9fa;
        color: #2c3e50;
    }

    .theme-ghost Header,
    .theme-ghost Footer,
    .theme-ghost #toolbar,
    .theme-ghost #review_bar {
        background: #ecf0f1;
        border-bottom: solid #3498db;
        text-style: bold;
    }

    .theme-ghost Button {
        background: #3498db;
        color: #ffffff;
        border: solid #2980b9;
        text-style: bold;
    }

    .theme-ghost Button:hover {
        background: #2980b9;
        border: solid #2980b9;
        text-style: bold;
    }

    .theme-ghost Button.success {
        background: #27ae60;
        border: solid #1e8449;
        color: #ffffff;
    }

    .theme-ghost Button.success:hover {
        background: #1e8449;
        text-style: bold;
    }

    .theme-ghost #left_panel,
    .theme-ghost #right_panel {
        background: #ecf0f1;
        border: solid #bdc3c7;
    }

    .theme-ghost #center_panel {
        background: #ffffff;
    }

    .theme-ghost #preview {
        background: #e8f4f8;
        border-bottom: solid #3498db;
    }

    .theme-ghost #dashboard {
        background: #ecf0f1;
        border-bottom: solid #3498db;
    }

    .theme-ghost DirectoryTree {
        background: #ecf0f1;
    }

    .theme-ghost Log {
        background: #ecf0f1;
        border-top: solid #3498db;
    }

    .theme-ghost #settings_container,
    .theme-ghost #wizard_container,
    .theme-ghost #schedule_review_container {
        border: heavy #3498db;
        background: #ecf0f1;
    }

    .theme-ghost Input,
    .theme-ghost Select {
        border: solid #3498db;
        background: #ffffff;
        color: #2c3e50;
    }
    """

    SCREENS = {"main": MainScreen, "settings": SettingsScreen, "subject_wizard": SubjectWizardScreen}

    def on_mount(self) -> None:
        self.config = load_config()
        self.apply_theme()
        self.push_screen("main")

    def apply_theme(self):
        theme = self.config.get("theme", "obsidian")
        self.set_class(theme == "cyberpunk", "theme-cyberpunk")
        self.set_class(theme == "midnight", "theme-midnight")
        self.set_class(theme == "ghost", "theme-ghost")
