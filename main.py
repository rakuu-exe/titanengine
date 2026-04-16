import json
import os
import shutil
import sqlite3
import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path

from google import genai
from google.genai import types
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DirectoryTree, Footer, Header, Input, Label, Log, Markdown, Select

try:
    from textual.widgets import TextArea
except ImportError:
    TextArea = None


BASE_DIR = Path.home() / "Desktop" / "School"
CONFIG_DIR = Path.home() / ".titan_engine"
CONFIG_FILE = CONFIG_DIR / "config.json"
SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv"}
CHUNK_TARGET_CHARS = 5500
MAX_CONCURRENT_CHUNK_REQUESTS = 4
AI_MAX_RETRIES = 3
AI_RETRY_BASE_DELAY = 1.25
DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"


def default_config():
    return {
        "api_key": os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
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
        config["api_key"] = os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    return config


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def ensure_subject_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subject_profile (
            subject_name TEXT PRIMARY KEY,
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


def init_subject_fortress(
    subject_name,
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
    for directory in ["Source_Materials", "Revision", "Quizzes", "Schedules", "System"]:
        (subj_dir / directory).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(subj_dir / "System" / "state.db")
    ensure_subject_schema(conn)
    conn.execute(
        """
        INSERT INTO subject_profile (
            subject_name, exam_date, weekly_hours, priority, confidence_level, quiz_style, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(subject_name) DO UPDATE SET
            exam_date=excluded.exam_date,
            weekly_hours=excluded.weekly_hours,
            priority=excluded.priority,
            confidence_level=excluded.confidence_level,
            quiz_style=excluded.quiz_style,
            updated_at=excluded.updated_at
        """,
        (
            safe_subject,
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
    conn = sqlite3.connect(subject_dir / "System" / "state.db")
    ensure_subject_schema(conn)
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


def read_study_file(path, max_chars=12000):
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
        "flashcards": "Quizzes",
    }
    target_dir = subject_dir / folders.get(output_type, "Revision")
    target_dir.mkdir(parents=True, exist_ok=True)
    source_stem = source_file.stem if source_file else subject_dir.name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = target_dir / f"{source_stem}_{output_type}_{timestamp}.md"
    out_path.write_text(content, encoding="utf-8")
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
            "Open Settings and paste your Google AI Studio key, or set the GEMINI_API_KEY environment variable."
        )

    client = genai.Client(api_key=api_key)
    system_instruction = MASTER_PROMPT + "\n\n" + role_prompt
    last_error = None

    for attempt in range(1, AI_MAX_RETRIES + 1):
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=user_data,
                config=types.GenerateContentConfig(system_instruction=system_instruction),
            )
            return response.text or ""
        except Exception as exc:
            last_error = exc
            if attempt == AI_MAX_RETRIES:
                break
            await asyncio.sleep(AI_RETRY_BASE_DELAY * (2 ** (attempt - 1)))

    return (
        "[ERROR] Gemini API call failed.\n\n"
        "Please check your Gemini API key, internet connection, model access, and free-tier rate limits. "
        f"Last error: {type(last_error).__name__ if last_error else 'Unknown'}."
    )


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


async def generate_flashcards(api_key, content, config):
    role = (
        "Create flashcards in markdown as a table with Front, Back, and Difficulty columns. "
        f"Language: {config.get('output_language', 'English')}."
    )
    return await summarize_chunks(api_key, role, content, config)


def generate_schedule(subject, exam_date, topics, weekly_hours, confidence_level="medium"):
    today = date.today()
    try:
        end_date = datetime.strptime(exam_date, "%Y-%m-%d").date() if exam_date else today + timedelta(days=28)
    except ValueError:
        end_date = today + timedelta(days=28)

    days_until_exam = max((end_date - today).days, 7)
    try:
        minutes_per_week = max(int(float(weekly_hours) * 60), 120)
    except (TypeError, ValueError):
        minutes_per_week = 240

    sessions_per_week = 4 if minutes_per_week >= 240 else 3
    session_minutes = max(25, minutes_per_week // sessions_per_week)
    total_sessions = max(3, min(len(topics) * 2, (days_until_exam // 7 + 1) * sessions_per_week))
    topics = topics or ["Core concepts", "Practice questions", "Final review"]
    weak_multiplier = 2 if confidence_level == "low" else 1

    plan = []
    cursor = today + timedelta(days=1)
    study_days = [0, 1, 3, 5]
    for index in range(total_sessions):
        while cursor.weekday() not in study_days:
            cursor += timedelta(days=1)
        topic = topics[index % len(topics)]
        task_type = "Review + quiz" if index % (3 * weak_multiplier) == 2 else "Study"
        plan.append(
            {
                "subject": subject,
                "task": f"{task_type}: {topic}",
                "due_date": cursor.isoformat(),
                "duration_minutes": session_minutes,
                "status": "planned",
            }
        )
        cursor += timedelta(days=1)
    return plan


def schedule_to_markdown(subject, plan):
    lines = [f"# Weekly Study Schedule: {subject}", ""]
    for item in plan:
        day_name = datetime.strptime(item["due_date"], "%Y-%m-%d").strftime("%A")
        lines.append(f"- {day_name} {item['due_date']}: {item['duration_minutes']} min - {item['task']}")
    return "\n".join(lines)


class SubjectWizardScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="wizard_container"):
            yield Markdown("# New Subject\nCreate a study workspace with schedule metadata.")
            yield Label("Subject name")
            yield Input(placeholder="Math, Biology, History...", id="subject_name")
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
        exam_date = self.query_one("#exam_date", Input).value.strip()
        weekly_hours = self.query_one("#weekly_hours", Input).value.strip()
        priority = self.query_one("#priority", Select).value
        confidence = self.query_one("#confidence_level", Select).value
        quiz_style = self.query_one("#quiz_style", Select).value

        if not subject_name:
            self.query_one("#wizard_status", Label).update("Subject name is required.")
            return

        try:
            init_subject_fortress(subject_name, exam_date, weekly_hours, priority, confidence, quiz_style)
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
                options=[
                    ("Gemini 1.5 Flash", "gemini-1.5-flash"),
                    ("Gemini 2.5 Flash", "gemini-2.5-flash"),
                ],
                value=config.get("ai_model", DEFAULT_GEMINI_MODEL),
                id="ai_model",
            )
            yield Label("Theme")
            yield Select(
                options=[("Obsidian Tactical", "obsidian"), ("Cyberpunk Red", "cyberpunk"), ("Ghost White", "ghost")],
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
            message = "Gemini API key is present." if key or os.environ.get("GEMINI_API_KEY") else "No Gemini API key found."
            self.query_one("#settings_status", Label).update(message)
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
            self.app.query_one(MainScreen).log_message("[DONE] Settings saved.")


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
                yield Button("Generate Flashcards", id="btn_flashcards")
                yield Button("Generate Quiz", id="btn_quiz")
                yield Button("Build Schedule", id="btn_schedule")
                yield Button("Batch Generate", id="btn_batch")
                yield Button("Settings", id="btn_settings")
            with Horizontal(id="workspace"):
                with Vertical(id="left_panel"):
                    yield Markdown(dashboard_markdown(), id="dashboard")
                    yield DirectoryTree(str(BASE_DIR), id="tree")
                with Vertical(id="center_panel"):
                    yield Markdown("# Preview\nChoose a file and generate notes, flashcards, a quiz, or a schedule.", id="preview")
                    if TextArea:
                        yield TextArea("", id="draft_editor")
                    else:
                        yield Input("", id="draft_editor")
                    with Horizontal(id="review_bar"):
                        yield Button("Save", id="btn_save", variant="success")
                        yield Button("Regenerate", id="btn_regenerate")
                        yield Button("Copy", id="btn_copy")
                        yield Button("Export Markdown", id="btn_export")
                with Vertical(id="right_panel"):
                    yield Markdown(
                        "## Actions\n"
                        "- Select a source file for notes, flashcards, or quizzes\n"
                        "- Select a subject folder for schedules\n"
                        "- Review the draft before saving",
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
        elif button_id == "btn_flashcards":
            self.action_generate_flashcards()
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
        self.log_message("[DONE] Draft ready. Review, edit, then Save or Export Markdown.")

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
        elif output_type == "flashcards":
            result = await generate_flashcards(api_key, content, self.app.config)
        else:
            result = await generate_revision(api_key, content, self.app.config)

        self.app.call_from_thread(self.set_pending_output, subject_dir, selected, output_type, result)

    def action_generate_notes(self) -> None:
        self.generate_from_selected_file("notes")

    def action_generate_flashcards(self) -> None:
        self.generate_from_selected_file("flashcards")

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
        self.app.call_from_thread(self.set_status, "Building schedule")
        self.app.call_from_thread(log.write_line, f"[SCHEDULE] Building study plan for {subject_dir.name}")

        with open_subject_db(subject_dir) as conn:
            profile = conn.execute(
                """
                SELECT exam_date, weekly_hours, confidence_level
                FROM subject_profile
                WHERE subject_name = ?
                """,
                (subject_dir.name,),
            ).fetchone()
            exam_date, weekly_hours, confidence = profile if profile else ("", "4", "medium")
            source_dir = subject_dir / "Source_Materials"
            topics = [path.stem.replace("_", " ") for path in source_dir.glob("*") if path.is_file()]
            plan = generate_schedule(subject_dir.name, exam_date, topics, weekly_hours, confidence)
            conn.execute("DELETE FROM study_plan WHERE subject = ? AND status = 'planned'", (subject_dir.name,))
            conn.executemany(
                """
                INSERT INTO study_plan (subject, task, due_date, duration_minutes, status)
                VALUES (:subject, :task, :due_date, :duration_minutes, :status)
                """,
                plan,
            )
            conn.commit()

        markdown = schedule_to_markdown(subject_dir.name, plan)
        self.app.call_from_thread(self.set_pending_output, subject_dir, None, "schedule", markdown)
        self.app.call_from_thread(self.refresh_dashboard)

    @work(exclusive=True, thread=True)
    async def action_batch_generate(self) -> None:
        log = self.query_one("#matrix_log", Log)
        self.app.call_from_thread(self.set_status, "Batch processing")
        self.app.call_from_thread(log.write_line, "[BATCH] Starting notes generation for missing source outputs.")

        for subject_dir in list_subjects():
            init_subject_fortress(subject_dir.name)
            src_dir = subject_dir / "Source_Materials"
            if not src_dir.exists():
                continue

            for file_path in src_dir.iterdir():
                if not file_path.is_file():
                    continue
                existing = list((subject_dir / "Revision").glob(f"{file_path.stem}_notes_*.md"))
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
        background: #050505;
        color: #00ff00;
    }

    #app_body {
        height: 1fr;
    }

    #toolbar {
        height: auto;
        padding: 1;
        border-bottom: solid #00ff00;
    }

    Button {
        margin-right: 1;
    }

    #workspace {
        height: 1fr;
    }

    #left_panel {
        width: 30%;
        min-width: 32;
        border-right: solid #00ff00;
    }

    #dashboard {
        height: 50%;
        padding: 1;
        border-bottom: solid #00ff00;
    }

    DirectoryTree {
        height: 50%;
        padding: 0 1;
    }

    #center_panel {
        width: 50%;
    }

    #preview {
        height: 45%;
        padding: 1;
        border-bottom: solid #00ff00;
    }

    #draft_editor {
        height: 1fr;
        border-bottom: solid #00ff00;
    }

    #review_bar {
        height: auto;
        padding: 1;
    }

    #right_panel {
        width: 20%;
        min-width: 28;
        padding: 1;
        border-left: solid #00ff00;
    }

    Log {
        border-top: solid #00ff00;
        height: 8;
    }

    #settings_container,
    #wizard_container {
        padding: 2;
        width: 70%;
        height: auto;
        border: solid #00ff00;
        content-align: center middle;
    }

    Input,
    Select {
        margin-bottom: 1;
    }

    .button_row {
        height: auto;
        margin-top: 1;
    }

    .theme-cyberpunk Screen {
        background: #0a0a0a;
        color: #ff003c;
    }

    .theme-cyberpunk #toolbar {
        border-bottom: solid #00f0ff;
    }

    .theme-cyberpunk #left_panel {
        border-right: solid #00f0ff;
    }

    .theme-cyberpunk #dashboard,
    .theme-cyberpunk #preview,
    .theme-cyberpunk #draft_editor {
        border-bottom: solid #00f0ff;
    }

    .theme-cyberpunk #right_panel {
        border-left: solid #00f0ff;
    }

    .theme-cyberpunk Log {
        border-top: solid #00f0ff;
    }

    .theme-cyberpunk #settings_container,
    .theme-cyberpunk #wizard_container {
        border: solid #00f0ff;
    }

    .theme-ghost Screen {
        background: #ffffff;
        color: #000000;
    }

    .theme-ghost #toolbar {
        border-bottom: solid #888888;
    }

    .theme-ghost #left_panel {
        border-right: solid #888888;
    }

    .theme-ghost #dashboard,
    .theme-ghost #preview,
    .theme-ghost #draft_editor {
        border-bottom: solid #888888;
    }

    .theme-ghost #right_panel {
        border-left: solid #888888;
    }

    .theme-ghost Log {
        border-top: solid #888888;
    }

    .theme-ghost #settings_container,
    .theme-ghost #wizard_container {
        border: solid #888888;
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
        self.set_class(theme == "ghost", "theme-ghost")


if __name__ == "__main__":
    TitanApp().run()
