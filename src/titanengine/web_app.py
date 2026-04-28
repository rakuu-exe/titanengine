import asyncio
import json
import os
import sqlite3
import threading
import time
import uuid
import webbrowser
from datetime import date, datetime
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from titanengine.app import (
    BASE_DIR,
    CONFIG_FILE,
    DEFAULT_GEMINI_MODEL,
    GEMINI_MODEL_OPTIONS,
    analyze_course_schedule,
    collect_schedule_sources,
    generate_quiz,
    generate_revision,
    init_subject_fortress,
    list_subjects,
    load_config,
    open_subject_db,
    parse_iso_date,
    persist_schedule_analysis,
    read_study_file,
    save_config,
    schedule_to_markdown,
)
from titanengine.pdf_export import write_text_pdf
from titanengine.scheduler import generate_smart_schedule, normalize_schedule_analysis


WEB_ROOT = BASE_DIR.parent
UPLOAD_DIR = WEB_ROOT / "uploads"
EXPORT_DIR = WEB_ROOT / "exports"
DATABASE_DIR = WEB_ROOT / "database"
LOG_DIR = WEB_ROOT / "logs"
DATABASE_FILE = DATABASE_DIR / "titan_engine.db"
ALLOWED_UPLOAD_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}


def ensure_web_directories():
    for directory in (BASE_DIR, UPLOAD_DIR, EXPORT_DIR, DATABASE_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def db_connection():
    ensure_web_directories()
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_web_column(conn, table_name, column_name, declaration):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {declaration}")


def init_database():
    ensure_web_directories()
    with db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                file_type TEXT,
                subject TEXT,
                upload_date TEXT,
                extracted_text TEXT,
                status TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER,
                title TEXT,
                content TEXT,
                note_type TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quizzes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                title TEXT,
                difficulty TEXT,
                question_type TEXT,
                content TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quiz_id INTEGER,
                question TEXT,
                choices TEXT,
                correct_answer TEXT,
                explanation TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS study_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                exam_date TEXT,
                plan_json TEXT,
                ai_review TEXT,
                created_at TEXT
            )
            """
        )
        ensure_web_column(conn, "quizzes", "content", "TEXT")
        conn.commit()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def run_async(coro):
    return asyncio.run(coro)


def ai_enabled(config):
    return bool(config.get("api_key")) and os.environ.get("TITAN_ENGINE_OFFLINE") != "1"


def clean_subject_name(value):
    value = (value or "").strip()
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch in " _-.").strip(" .")
    return cleaned or "General"


def get_subject_names():
    return [path.name for path in list_subjects()]


def get_subject_profile(subject):
    subject_dir = BASE_DIR / subject
    if not subject_dir.exists():
        return {}
    columns = [
        "subject_name",
        "subject_start_date",
        "exam_date",
        "weekly_hours",
        "priority",
        "confidence_level",
        "quiz_style",
    ]
    try:
        with open_subject_db(subject_dir) as conn:
            row = conn.execute(
                """
                SELECT subject_name, subject_start_date, exam_date, weekly_hours, priority, confidence_level, quiz_style
                FROM subject_profile
                WHERE subject_name = ?
                """,
                (subject,),
            ).fetchone()
    except sqlite3.Error:
        return {}
    return dict(zip(columns, row)) if row else {}


def get_file_row(file_id):
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    return row


def get_note_row(note_id):
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    return row


def get_quiz_row(quiz_id):
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
    return row


def get_schedule_row(schedule_id):
    with db_connection() as conn:
        row = conn.execute("SELECT * FROM study_schedules WHERE id = ?", (schedule_id,)).fetchone()
    return row


def stored_file_path(row):
    path = Path(row["stored_filename"])
    if path.is_absolute():
        return path
    return BASE_DIR / row["subject"] / "Source_Materials" / path


def file_text(row):
    text = row["extracted_text"] or ""
    if text.strip():
        return text
    path = stored_file_path(row)
    return read_study_file(path)


def selected_file_rows(file_ids):
    ids = [int(file_id) for file_id in file_ids if str(file_id).isdigit()]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    with db_connection() as conn:
        return conn.execute(f"SELECT * FROM files WHERE id IN ({placeholders})", ids).fetchall()


def first_sentences(text, limit=8):
    text = " ".join((text or "").split())
    if not text:
        return []
    parts = []
    current = []
    for word in text.split(" "):
        current.append(word)
        if word.endswith((".", "?", "!")):
            sentence = " ".join(current).strip()
            if len(sentence) > 24:
                parts.append(sentence)
            current = []
        if len(parts) >= limit:
            break
    if not parts and current:
        parts.append(" ".join(current[:40]))
    return parts[:limit]


def make_local_notes(title, text, note_type):
    sentences = first_sentences(text, 10)
    definitions = [line.strip() for line in (text or "").splitlines() if ":" in line or " is " in line.lower()]
    definitions = definitions[:5]
    concepts = sentences[:5] or ["No readable text was extracted from this file yet."]
    lines = [
        f"# {title}",
        "",
        f"## Note Style",
        f"- {note_type.replace('_', ' ').title()}",
        "",
        "## Key Concepts",
    ]
    lines.extend(f"- {item}" for item in concepts)
    lines.extend(["", "## Main Explanation"])
    lines.extend(f"- {item}" for item in sentences[:6])
    lines.extend(["", "## Important Definitions"])
    lines.extend(f"- {item}" for item in definitions or ["Review the source and add definitions while studying."])
    lines.extend(
        [
            "",
            "## Possible Exam Questions",
            "- What are the most important ideas in this material?",
            "- Which definitions or formulas would be easy to confuse?",
            "- How would you explain this topic to a classmate?",
            "",
            "## Summary",
            f"- Local draft created from {len(text or '')} extracted characters. Add an API key in Settings for AI notes.",
        ]
    )
    return "\n".join(lines)


def make_local_quiz(title, text, question_count=5, question_type="mixed"):
    sentences = first_sentences(text, max(question_count, 5))
    if not sentences:
        sentences = ["No readable source text was extracted from this file."]
    lines = [f"# {title}", "", f"- Question type: {question_type}", ""]
    for index, sentence in enumerate(sentences[:question_count], start=1):
        words = [word.strip(".,:;()[]") for word in sentence.split() if len(word.strip(".,:;()[]")) > 5]
        answer = words[0] if words else sentence.split()[0]
        prompt = sentence.replace(answer, "_____", 1)
        lines.extend(
            [
                f"## Question {index}",
                f"{prompt}",
                "",
                f"**Answer:** {answer}",
                f"**Explanation:** This answer comes from the source sentence: {sentence}",
                "",
            ]
        )
    lines.append("_Local draft created without an AI API key. Add an API key in Settings for richer quizzes._")
    return "\n".join(lines)


def make_local_schedule_analysis(subject, subject_start_date, exam_date, sources):
    mappings = []
    for index, source in enumerate(sources, start=1):
        mappings.append(
            {
                "source_file": source["source_file"],
                "week_start": index,
                "week_end": index,
                "topic": Path(source["source_file"]).stem.replace("_", " ").title(),
                "confidence": "medium",
            }
        )
    events = []
    if exam_date:
        events.append(
            {
                "type": "exam",
                "title": f"{subject} final review",
                "week": max(len(mappings), 1),
                "date": exam_date,
                "source_file": "",
                "confidence": "medium",
                "notes": "Generated from the exam date supplied in the web form.",
            }
        )
    return normalize_schedule_analysis(
        {
            "course_length_weeks": max(len(mappings), 8),
            "events": events,
            "source_file_weeks": mappings,
        },
        subject_start_date,
    )


def dashboard_context():
    with db_connection() as conn:
        files = conn.execute("SELECT * FROM files ORDER BY upload_date DESC LIMIT 6").fetchall()
        notes = conn.execute("SELECT * FROM notes ORDER BY created_at DESC LIMIT 4").fetchall()
        quizzes = conn.execute("SELECT * FROM quizzes ORDER BY created_at DESC LIMIT 4").fetchall()
        schedules = conn.execute("SELECT * FROM study_schedules ORDER BY created_at DESC LIMIT 4").fetchall()
        counts = {
            "files": conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "notes": conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0],
            "quizzes": conn.execute("SELECT COUNT(*) FROM quizzes").fetchone()[0],
            "schedules": conn.execute("SELECT COUNT(*) FROM study_schedules").fetchone()[0],
        }
    return {
        "subjects": get_subject_names(),
        "recent_files": files,
        "recent_notes": notes,
        "recent_quizzes": quizzes,
        "recent_schedules": schedules,
        "counts": counts,
        "config": load_config(),
        "base_dir": BASE_DIR,
    }


def create_web_app():
    init_database()
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = "titan-study-engine-local"
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

    @app.context_processor
    def inject_globals():
        return {
            "subjects": get_subject_names(),
            "now": datetime.now(),
        }

    @app.route("/")
    def dashboard():
        return render_template("dashboard.html", **dashboard_context())

    @app.route("/subjects", methods=["POST"])
    def create_subject():
        subject = clean_subject_name(request.form.get("subject_name"))
        start_date = (request.form.get("subject_start_date") or date.today().isoformat()).strip()
        exam_date = (request.form.get("exam_date") or "").strip()
        weekly_hours = (request.form.get("weekly_hours") or "4").strip()
        if not parse_iso_date(start_date):
            flash("Subject begin date must use YYYY-MM-DD.", "error")
            return redirect(url_for("dashboard"))
        if exam_date and not parse_iso_date(exam_date):
            flash("Exam date must use YYYY-MM-DD or stay empty.", "error")
            return redirect(url_for("dashboard"))
        try:
            init_subject_fortress(subject, start_date, exam_date, weekly_hours)
        except (OSError, sqlite3.Error, ValueError) as exc:
            flash(f"Could not create subject: {exc}", "error")
            return redirect(url_for("dashboard"))
        flash(f"Subject workspace created: {subject}", "success")
        return redirect(url_for("library", subject=subject))

    @app.route("/library")
    def library():
        subject = request.args.get("subject", "")
        query = "SELECT * FROM files"
        params = []
        if subject:
            query += " WHERE subject = ?"
            params.append(subject)
        query += " ORDER BY upload_date DESC"
        with db_connection() as conn:
            files = conn.execute(query, params).fetchall()
        return render_template("library.html", files=files, selected_subject=subject)

    @app.route("/upload", methods=["POST"])
    def upload():
        subject = clean_subject_name(request.form.get("subject"))
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            flash("Choose a file to upload.", "error")
            return redirect(url_for("library", subject=subject))
        original_name = secure_filename(uploaded.filename)
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
            flash(f"{suffix or 'This file type'} is not supported yet.", "error")
            return redirect(url_for("library", subject=subject))

        profile = get_subject_profile(subject)
        if not profile:
            init_subject_fortress(subject, date.today().isoformat(), "", "4")

        subject_dir = BASE_DIR / subject
        target_dir = subject_dir / "Source_Materials"
        target_dir.mkdir(parents=True, exist_ok=True)
        stored_name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}_{original_name}"
        target = target_dir / stored_name
        uploaded.save(target)

        try:
            extracted_text = read_study_file(target)
            status = "ready"
        except ValueError:
            extracted_text = ""
            status = "stored - parser pending"
        except OSError as exc:
            extracted_text = ""
            status = f"read error: {exc}"

        with db_connection() as conn:
            conn.execute(
                """
                INSERT INTO files (original_filename, stored_filename, file_type, subject, upload_date, extracted_text, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (uploaded.filename, str(target), suffix.lstrip("."), subject, now_iso(), extracted_text, status),
            )
            conn.commit()

        flash(f"Uploaded {uploaded.filename}. Status: {status}", "success")
        return redirect(url_for("library", subject=subject))

    @app.route("/files/<int:file_id>/delete", methods=["POST"])
    def delete_file(file_id):
        row = get_file_row(file_id)
        if not row:
            abort(404)
        path = stored_file_path(row)
        try:
            if path.exists() and path.resolve().is_relative_to(BASE_DIR.resolve()):
                path.unlink()
        except OSError:
            pass
        with db_connection() as conn:
            conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
            conn.commit()
        flash(f"Removed {row['original_filename']} from the library.", "success")
        return redirect(url_for("library", subject=row["subject"]))

    @app.route("/notes")
    def notes():
        with db_connection() as conn:
            files = conn.execute("SELECT * FROM files ORDER BY upload_date DESC").fetchall()
            rows = conn.execute("SELECT * FROM notes ORDER BY created_at DESC").fetchall()
        return render_template("notes.html", files=files, notes=rows)

    @app.route("/notes/generate", methods=["POST"])
    def generate_notes_route():
        rows = selected_file_rows(request.form.getlist("file_ids"))
        note_type = request.form.get("note_type") or "detailed"
        if not rows:
            flash("Select at least one source file.", "error")
            return redirect(url_for("notes"))
        texts = []
        for row in rows:
            try:
                texts.append(f"Source: {row['original_filename']}\n\n{file_text(row)}")
            except (OSError, ValueError) as exc:
                flash(f"Could not read {row['original_filename']}: {exc}", "error")
        combined = "\n\n---\n\n".join(texts).strip()
        title = request.form.get("title") or f"{rows[0]['subject']} study notes"
        config = load_config()
        if ai_enabled(config):
            result = run_async(generate_revision(config.get("api_key"), combined, {**config, "output_length": note_type}))
        else:
            result = make_local_notes(title, combined, note_type)
        with db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO notes (file_id, title, content, note_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (rows[0]["id"], title, result, note_type, now_iso(), now_iso()),
            )
            conn.commit()
            note_id = cursor.lastrowid
        flash("Notes generated. Review and edit before exporting.", "success")
        return redirect(url_for("note_detail", note_id=note_id))

    @app.route("/notes/<int:note_id>")
    def note_detail(note_id):
        row = get_note_row(note_id)
        if not row:
            abort(404)
        return render_template("note_detail.html", note=row)

    @app.route("/notes/<int:note_id>/update", methods=["POST"])
    def update_note(note_id):
        content = request.form.get("content") or ""
        title = request.form.get("title") or "Study notes"
        with db_connection() as conn:
            conn.execute(
                "UPDATE notes SET title = ?, content = ?, updated_at = ? WHERE id = ?",
                (title, content, now_iso(), note_id),
            )
            conn.commit()
        flash("Notes updated.", "success")
        return redirect(url_for("note_detail", note_id=note_id))

    @app.route("/quiz")
    def quizzes():
        with db_connection() as conn:
            files = conn.execute("SELECT * FROM files ORDER BY upload_date DESC").fetchall()
            rows = conn.execute("SELECT * FROM quizzes ORDER BY created_at DESC").fetchall()
        return render_template("quiz.html", files=files, quizzes=rows)

    @app.route("/quiz/generate", methods=["POST"])
    def generate_quiz_route():
        file_id = request.form.get("file_id")
        row = get_file_row(int(file_id)) if file_id and file_id.isdigit() else None
        if not row:
            flash("Select one source file for the quiz.", "error")
            return redirect(url_for("quizzes"))
        difficulty = request.form.get("difficulty") or "medium"
        question_type = request.form.get("question_type") or "mixed"
        question_count = int(request.form.get("question_count") or "5")
        title = request.form.get("title") or f"{row['original_filename']} quiz"
        text = file_text(row)
        config = {**load_config(), "quiz_difficulty": difficulty}
        if ai_enabled(config):
            prompt_text = (
                f"Create {question_count} {question_type} questions from this material. "
                "Include correct answers and short explanations.\n\n"
                f"{text}"
            )
            result = run_async(generate_quiz(config.get("api_key"), prompt_text, config))
        else:
            result = make_local_quiz(title, text, question_count, question_type)
        with db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO quizzes (source_id, title, difficulty, question_type, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (row["id"], title, difficulty, question_type, result, now_iso()),
            )
            conn.commit()
            quiz_id = cursor.lastrowid
        flash("Quiz generated.", "success")
        return redirect(url_for("quiz_detail", quiz_id=quiz_id))

    @app.route("/quiz/<int:quiz_id>")
    def quiz_detail(quiz_id):
        row = get_quiz_row(quiz_id)
        if not row:
            abort(404)
        return render_template("quiz_detail.html", quiz=row)

    @app.route("/flashcards")
    def flashcards():
        note_id = request.args.get("note_id")
        cards = []
        active_note = None
        with db_connection() as conn:
            notes_rows = conn.execute("SELECT * FROM notes ORDER BY created_at DESC").fetchall()
            if note_id and note_id.isdigit():
                active_note = conn.execute("SELECT * FROM notes WHERE id = ?", (int(note_id),)).fetchone()
        if active_note:
            for index, sentence in enumerate(first_sentences(active_note["content"], 12), start=1):
                cards.append(
                    {
                        "front": f"Card {index}",
                        "back": sentence,
                        "level": "medium" if index % 3 else "hard",
                    }
                )
        return render_template("flashcards.html", notes=notes_rows, active_note=active_note, cards=cards)

    @app.route("/schedule")
    def schedules():
        with db_connection() as conn:
            rows = conn.execute("SELECT * FROM study_schedules ORDER BY created_at DESC").fetchall()
        return render_template("schedule.html", schedules=rows)

    @app.route("/schedule/generate", methods=["POST"])
    def generate_schedule_route():
        subject = clean_subject_name(request.form.get("subject"))
        if subject not in get_subject_names():
            flash("Create or select a subject before building a schedule.", "error")
            return redirect(url_for("schedules"))
        subject_dir = BASE_DIR / subject
        profile = get_subject_profile(subject)
        subject_start_date = request.form.get("subject_start_date") or profile.get("subject_start_date") or date.today().isoformat()
        exam_date = request.form.get("exam_date") or profile.get("exam_date") or ""
        study_days = request.form.getlist("study_days") or ["Mon", "Wed", "Sat"]
        minutes_per_day = max(int(request.form.get("minutes_per_day") or "45"), 20)
        weekly_hours = str(round((len(study_days) * minutes_per_day) / 60, 2))
        confidence = request.form.get("confidence_level") or profile.get("confidence_level") or "medium"
        sources = collect_schedule_sources(subject_dir)
        if not sources:
            flash("Upload at least one source file before building a schedule.", "error")
            return redirect(url_for("schedules"))
        config = load_config()
        try:
            if ai_enabled(config):
                analysis = run_async(
                    analyze_course_schedule(config.get("api_key"), subject, subject_start_date, sources, config)
                )
                review = "AI reviewed course files, detected deadlines, and mapped files to course weeks."
            else:
                analysis = make_local_schedule_analysis(subject, subject_start_date, exam_date, sources)
                review = "Local schedule draft created without online AI. Add an API key for AI-reviewed event detection."
        except (ValueError, json.JSONDecodeError) as exc:
            analysis = make_local_schedule_analysis(subject, subject_start_date, exam_date, sources)
            review = f"AI analysis failed, so Titan used a local schedule draft. Details: {exc}"

        plan = generate_smart_schedule(subject, subject_start_date, analysis, weekly_hours, confidence)
        markdown = schedule_to_markdown(subject, plan)
        with open_subject_db(subject_dir) as subject_conn:
            persist_schedule_analysis(subject_conn, subject, analysis)
            subject_conn.execute("DELETE FROM study_plan WHERE subject = ? AND status = 'planned'", (subject,))
            subject_conn.executemany(
                """
                INSERT INTO study_plan (subject, task, due_date, duration_minutes, status)
                VALUES (:subject, :task, :due_date, :duration_minutes, :status)
                """,
                plan,
            )
            subject_conn.commit()
        with db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO study_schedules (title, exam_date, plan_json, ai_review, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (f"{subject} study schedule", exam_date, json.dumps({"markdown": markdown, "plan": plan}), review, now_iso()),
            )
            conn.commit()
            schedule_id = cursor.lastrowid
        flash("Study schedule generated.", "success")
        return redirect(url_for("schedule_detail", schedule_id=schedule_id))

    @app.route("/schedule/<int:schedule_id>")
    def schedule_detail(schedule_id):
        row = get_schedule_row(schedule_id)
        if not row:
            abort(404)
        payload = json.loads(row["plan_json"] or "{}")
        return render_template("schedule_detail.html", schedule=row, markdown=payload.get("markdown", ""), plan=payload.get("plan", []))

    @app.route("/export/pdf", methods=["POST"])
    def export_pdf():
        export_type = request.form.get("type")
        item_id = request.form.get("id")
        if not item_id or not item_id.isdigit():
            abort(400)
        title = "Titan Study Export"
        content = ""
        if export_type == "notes":
            row = get_note_row(int(item_id))
            if not row:
                abort(404)
            title, content = row["title"], row["content"]
        elif export_type == "quiz":
            row = get_quiz_row(int(item_id))
            if not row:
                abort(404)
            title, content = row["title"], row["content"]
        elif export_type == "schedule":
            row = get_schedule_row(int(item_id))
            if not row:
                abort(404)
            payload = json.loads(row["plan_json"] or "{}")
            title, content = row["title"], payload.get("markdown", "")
        else:
            abort(400)

        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"{secure_filename(title) or 'titan_export'}_{datetime.now():%Y%m%d_%H%M%S}.pdf"
        out_path = EXPORT_DIR / filename
        write_text_pdf(out_path, title, content)
        return send_file(out_path, as_attachment=True, download_name=filename)

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            config = {
                "api_key": request.form.get("api_key", "").strip(),
                "ai_provider": request.form.get("ai_provider", "gemini"),
                "ai_model": request.form.get("ai_model") or DEFAULT_GEMINI_MODEL,
                "theme": request.form.get("theme", "obsidian"),
                "output_length": request.form.get("output_length", "balanced"),
                "quiz_difficulty": request.form.get("quiz_difficulty", "medium"),
                "schedule_preferences": request.form.get("schedule_preferences", "").strip(),
                "output_language": request.form.get("output_language", "English").strip() or "English",
                "local_storage_path": str(BASE_DIR),
                "export_folder": str(EXPORT_DIR),
            }
            save_config(config)
            flash("Settings saved locally.", "success")
            return redirect(url_for("settings"))
        return render_template(
            "settings.html",
            config=load_config(),
            models=GEMINI_MODEL_OPTIONS,
            config_file=CONFIG_FILE,
            export_dir=EXPORT_DIR,
            base_dir=BASE_DIR,
        )

    @app.route("/health")
    def health():
        return {"status": "ok", "database": str(DATABASE_FILE)}

    return app


def open_browser_later(url, delay=1.0):
    def open_browser():
        time.sleep(delay)
        webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()


def run_web_app(host="127.0.0.1", port=5000, open_browser=True):
    app = create_web_app()
    url = f"http://{host}:{port}"
    if open_browser:
        open_browser_later(url)
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
