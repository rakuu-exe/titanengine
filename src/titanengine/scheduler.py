import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path


EVENT_TYPES = {"test", "exam", "project", "attendance", "lesson", "deadline", "other"}


def parse_iso_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_int(value, default=None):
    try:
        if value in ("", None):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def course_week_start(subject_start_date, week_number):
    start_date = parse_iso_date(subject_start_date)
    week = parse_int(week_number)
    if not start_date or not week or week < 1:
        return None
    return start_date + timedelta(days=(week - 1) * 7)


def date_from_week(subject_start_date, week_number):
    week_start = course_week_start(subject_start_date, week_number)
    return week_start.isoformat() if week_start else ""


def normalize_confidence(value):
    value = str(value or "medium").strip().lower()
    return value if value in {"low", "medium", "high"} else "medium"


def normalize_event_type(value):
    value = str(value or "other").strip().lower()
    aliases = {
        "assessment": "test",
        "control work": "test",
        "quiz": "test",
        "test": "test",
        "kontrolltoo": "test",
        "kontrolltöö": "test",
        "kontrollt66": "test",
        "testi": "test",
        "тест": "test",
        "контрольная": "test",
        "контрольная работа": "test",
        "проверочная": "test",
        "проверочная работа": "test",
        "arvestus": "exam",
        "eksam": "exam",
        "eksami": "exam",
        "exam": "exam",
        "examination": "exam",
        "экзамен": "exam",
        "зачет": "exam",
        "зачёт": "exam",
        "practical": "project",
        "assignment": "project",
        "project work": "project",
        "projekt": "project",
        "projekti töö": "project",
        "projekti too": "project",
        "проект": "project",
        "проектная работа": "project",
        "домашнее задание": "project",
        "самостоятельная работа": "project",
        "lesson": "attendance",
        "class": "attendance",
        "lecture": "attendance",
        "loeng": "attendance",
        "seminar": "attendance",
        "praktikum": "attendance",
        "занятие": "attendance",
        "лекция": "attendance",
        "семинар": "attendance",
        "практика": "attendance",
        "deadline": "deadline",
        "tähtaeg": "deadline",
        "tahtaeg": "deadline",
        "срок": "deadline",
        "дедлайн": "deadline",
    }
    value = aliases.get(value, value)
    return value if value in EVENT_TYPES else "other"


def extract_json_payload(text):
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    return json.loads(text)


def normalize_schedule_analysis(raw_analysis, subject_start_date):
    events = []
    for raw_event in raw_analysis.get("events", []) or []:
        event_type = normalize_event_type(raw_event.get("type") or raw_event.get("event_type"))
        week_number = parse_int(raw_event.get("week") or raw_event.get("week_number"))
        event_date = str(raw_event.get("date") or raw_event.get("event_date") or "").strip()
        if not parse_iso_date(event_date) and week_number:
            event_date = date_from_week(subject_start_date, week_number)
        events.append(
            {
                "event_type": event_type,
                "title": str(raw_event.get("title") or event_type.title()).strip(),
                "week_number": week_number,
                "event_date": event_date,
                "source_file": str(raw_event.get("source_file") or "").strip(),
                "confidence": normalize_confidence(raw_event.get("confidence")),
                "notes": str(raw_event.get("notes") or "").strip(),
            }
        )

    source_file_weeks = []
    for raw_mapping in raw_analysis.get("source_file_weeks", []) or []:
        week_start = parse_int(
            raw_mapping.get("week_start") or raw_mapping.get("start_week") or raw_mapping.get("week")
        )
        week_end = parse_int(raw_mapping.get("week_end") or raw_mapping.get("end_week") or week_start)
        if week_start and not week_end:
            week_end = week_start
        if week_start and week_end and week_end < week_start:
            week_start, week_end = week_end, week_start
        source_file_weeks.append(
            {
                "source_file": str(raw_mapping.get("source_file") or raw_mapping.get("file") or "").strip(),
                "week_start": week_start,
                "week_end": week_end,
                "topic": str(raw_mapping.get("topic") or raw_mapping.get("title") or "").strip(),
                "confidence": normalize_confidence(raw_mapping.get("confidence")),
            }
        )

    course_length = parse_int(raw_analysis.get("course_length_weeks"), default=16)
    return {
        "course_length_weeks": max(course_length or 16, 1),
        "events": events,
        "source_file_weeks": source_file_weeks,
        "raw": raw_analysis,
    }


def schedule_analysis_to_markdown(subject, subject_start_date, analysis):
    lines = [
        f"# Schedule Analysis: {subject}",
        "",
        f"- Subject begins: {subject_start_date}",
        f"- Course length guess: {analysis.get('course_length_weeks', 16)} weeks",
        "",
        "## Detected Events",
    ]
    events = analysis.get("events", [])
    if not events:
        lines.append("- No tests, exams, projects, or attendance items detected")
    for event in sorted(events, key=lambda item: (item.get("event_date") or "9999-99-99", item.get("week_number") or 999)):
        week = f"week {event['week_number']}" if event.get("week_number") else "week unknown"
        event_date = event.get("event_date") or "date unknown"
        lines.append(
            f"- {event['event_type'].title()}: {event['title']} ({week}, {event_date}, {event['confidence']} confidence)"
        )
        if event.get("notes"):
            lines.append(f"  - {event['notes']}")

    lines.extend(["", "## Source File Week Mapping"])
    mappings = analysis.get("source_file_weeks", [])
    if not mappings:
        lines.append("- No file-to-week mapping detected")
    for mapping in mappings:
        week_start = mapping.get("week_start") or "?"
        week_end = mapping.get("week_end") or week_start
        week_text = f"week {week_start}" if week_start == week_end else f"weeks {week_start}-{week_end}"
        topic = mapping.get("topic") or Path(mapping.get("source_file") or "source").stem
        lines.append(f"- {mapping.get('source_file') or 'Unknown file'}: {week_text} - {topic} ({mapping['confidence']})")
    return "\n".join(lines)


def session_settings(weekly_hours, confidence_level):
    try:
        minutes_per_week = max(int(float(weekly_hours) * 60), 120)
    except (TypeError, ValueError):
        minutes_per_week = 240
    sessions_per_week = 4 if minutes_per_week >= 240 else 3
    if confidence_level == "low":
        sessions_per_week += 1
    return sessions_per_week, max(25, minutes_per_week // sessions_per_week)


def topics_for_week_range(source_file_weeks, week_start, week_end):
    topics = []
    for mapping in source_file_weeks:
        mapping_start = mapping.get("week_start")
        mapping_end = mapping.get("week_end") or mapping_start
        if not mapping_start or not mapping_end:
            continue
        if mapping_start <= week_end and mapping_end >= week_start:
            label = mapping.get("topic") or Path(mapping.get("source_file") or "Course material").stem
            if mapping.get("source_file"):
                label = f"{label} ({mapping['source_file']})"
            topics.append(label)
    return topics or [f"Weeks {week_start}-{week_end} material"]


def add_study_sessions(plan, subject, label, topics, deadline, weekly_hours, confidence_level, start_date=None):
    deadline_date = parse_iso_date(deadline)
    if not deadline_date:
        return

    sessions_per_week, session_minutes = session_settings(weekly_hours, confidence_level)
    study_days = {0, 1, 3, 5}
    cursor = max(start_date or date.today(), date.today()) + timedelta(days=1)
    available_dates = []
    while cursor < deadline_date:
        if cursor.weekday() in study_days:
            available_dates.append(cursor)
        cursor += timedelta(days=1)

    if not available_dates:
        available_dates = [max(date.today(), deadline_date - timedelta(days=1))]

    desired_sessions = max(2, len(topics) * (2 if confidence_level == "low" else 1))
    max_window_sessions = max(1, int(((deadline_date - available_dates[0]).days + 1) / 7 * sessions_per_week))
    session_count = min(len(available_dates), max(desired_sessions, min(max_window_sessions, len(available_dates))))

    for index, session_date in enumerate(available_dates[-session_count:]):
        topic = topics[index % len(topics)]
        task_type = "Review + quiz" if index % 3 == 2 else "Study"
        plan.append(
            {
                "subject": subject,
                "task": f"{task_type} for {label}: {topic}",
                "due_date": session_date.isoformat(),
                "duration_minutes": session_minutes,
                "status": "planned",
            }
        )


def add_event_reminder(plan, subject, event, prefix=None):
    event_date = event.get("event_date")
    if not parse_iso_date(event_date):
        return
    label = prefix or event.get("event_type", "Event").title()
    title = event.get("title") or label
    notes = f" - {event['notes']}" if event.get("notes") else ""
    plan.append(
        {
            "subject": subject,
            "task": f"{label}: {title}{notes}",
            "due_date": event_date,
            "duration_minutes": 0,
            "status": "planned",
        }
    )


def event_sort_key(event):
    event_date = parse_iso_date(event.get("event_date"))
    fallback_date = course_week_start("2000-01-03", event.get("week_number")) or date.max
    return event_date or fallback_date


def generate_smart_schedule(subject, subject_start_date, analysis, weekly_hours, confidence_level="medium"):
    events = sorted(analysis.get("events", []), key=event_sort_key)
    mappings = analysis.get("source_file_weeks", [])
    tests = [event for event in events if event.get("event_type") == "test"]
    exams = [event for event in events if event.get("event_type") == "exam"]
    projects = [event for event in events if event.get("event_type") in {"project", "deadline"}]
    attendance = [event for event in events if event.get("event_type") in {"attendance", "lesson"}]

    plan = []
    for event in attendance:
        add_event_reminder(plan, subject, event, "Attend")

    phase_start = date.today()
    previous_test_week = 0
    for test in tests:
        test_week = test.get("week_number") or previous_test_week + 1
        week_start = previous_test_week + 1
        week_end = max(test_week, week_start)
        topics = topics_for_week_range(mappings, week_start, week_end)
        add_study_sessions(
            plan,
            subject,
            test.get("title") or f"Week {test_week} test",
            topics,
            test.get("event_date"),
            weekly_hours,
            confidence_level,
            phase_start,
        )
        add_event_reminder(plan, subject, test, "Test")
        previous_test_week = week_end
        test_date = parse_iso_date(test.get("event_date"))
        if test_date:
            phase_start = test_date

    if exams:
        mapped_weeks = [
            week
            for mapping in mappings
            for week in (mapping.get("week_start"), mapping.get("week_end"))
            if week
        ]
        course_end_week = max(mapped_weeks or [analysis.get("course_length_weeks", 16)])
        topics = topics_for_week_range(mappings, 1, course_end_week)
        for exam in exams:
            add_study_sessions(
                plan,
                subject,
                exam.get("title") or "Final exam",
                topics,
                exam.get("event_date"),
                weekly_hours,
                confidence_level,
                phase_start,
            )
            add_event_reminder(plan, subject, exam, "Exam")

    for project in projects:
        week = project.get("week_number") or 1
        topics = topics_for_week_range(mappings, week, week)
        add_study_sessions(
            plan,
            subject,
            project.get("title") or "Project deadline",
            topics,
            project.get("event_date"),
            weekly_hours,
            confidence_level,
            date.today(),
        )
        add_event_reminder(plan, subject, project, "Deadline")

    if not tests and not exams and not projects and not attendance:
        mapped_weeks = [
            week
            for mapping in mappings
            for week in (mapping.get("week_start"), mapping.get("week_end"))
            if week
        ]
        course_end_week = max(mapped_weeks or [analysis.get("course_length_weeks", 16)])
        fallback_date = course_week_start(subject_start_date, course_end_week) or (date.today() + timedelta(days=28))
        add_study_sessions(
            plan,
            subject,
            "course progress",
            topics_for_week_range(mappings, 1, course_end_week),
            fallback_date.isoformat(),
            weekly_hours,
            confidence_level,
            date.today(),
        )

    return sorted(plan, key=lambda item: (item["due_date"], item["task"]))
