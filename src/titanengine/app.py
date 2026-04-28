from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import errors, types

from titanengine.scheduler import extract_json_payload, normalize_schedule_analysis, parse_iso_date


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


def packaged_data_dir():
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if root:
        return Path(root) / "TitanEngineApp" / "data"
    return Path.home() / "TitanEngineApp" / "data"


if os.environ.get("TITAN_ENGINE_SCHOOL_DIR"):
    BASE_DIR = Path(os.environ["TITAN_ENGINE_SCHOOL_DIR"])
elif getattr(sys, "_MEIPASS", None) or getattr(sys, "frozen", False):
    BASE_DIR = packaged_data_dir() / "School"
else:
    BASE_DIR = choose_source_base_dir()

BASE_DIR.mkdir(parents=True, exist_ok=True)

if getattr(sys, "_MEIPASS", None) or getattr(sys, "frozen", False):
    CONFIG_DIR = packaged_data_dir() / "config"
else:
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

    subject_dir = BASE_DIR / safe_subject
    ensure_subject_directories(subject_dir)
    with sqlite3.connect(subject_dir / "System" / "state.db") as conn:
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
    return subject_dir


def open_subject_db(subject_dir):
    ensure_subject_directories(subject_dir)
    conn = sqlite3.connect(subject_dir / "System" / "state.db")
    ensure_subject_schema(conn)
    conn.commit()
    return conn


def list_subjects():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(path for path in BASE_DIR.iterdir() if path.is_dir())


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


MASTER_PROMPT = """
You are an AI study assistant inside a local desktop application.
Your job is to help the user summarize study material, generate revision notes,
create quizzes, and build realistic study schedules.

Be concise, accurate, and student-friendly. Use headings and bullet points when useful.
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
        "Detect tests, exams, projects, deadlines, attendance requirements, and map each source file "
        "to the course week or week range it covers. Preserve explicit week numbers. "
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
