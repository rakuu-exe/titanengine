import unittest

from titanengine.scheduler import (
    course_week_start,
    generate_smart_schedule,
    normalize_event_type,
    normalize_schedule_analysis,
)


class SchedulerTests(unittest.TestCase):
    def test_week_numbers_convert_from_subject_start_date(self):
        self.assertEqual(course_week_start("2026-04-13", 1).isoformat(), "2026-04-13")
        self.assertEqual(course_week_start("2026-04-13", 8).isoformat(), "2026-06-01")
        self.assertEqual(course_week_start("2026-04-13", 15).isoformat(), "2026-07-20")

    def test_test_windows_use_only_relevant_week_ranges(self):
        analysis = normalize_schedule_analysis(
            {
                "events": [
                    {"type": "test", "title": "Test 1", "week": 8, "confidence": "high"},
                    {"type": "test", "title": "Test 2", "week": 15, "confidence": "high"},
                ],
                "source_file_weeks": [
                    {"source_file": "week_01.pdf", "week_start": 1, "week_end": 1, "topic": "Intro"},
                    {"source_file": "week_08.pdf", "week_start": 8, "week_end": 8, "topic": "Markets"},
                    {"source_file": "week_09.pdf", "week_start": 9, "week_end": 9, "topic": "Finance"},
                    {"source_file": "week_15.pdf", "week_start": 15, "week_end": 15, "topic": "Strategy"},
                ],
            },
            "2026-04-13",
        )
        plan = generate_smart_schedule("MAJANDUS", "2026-04-13", analysis, "4", "medium")
        test_two_tasks = [item["task"] for item in plan if "Test 2" in item["task"]]
        self.assertTrue(any("Finance" in task for task in test_two_tasks))
        self.assertTrue(any("Strategy" in task for task in test_two_tasks))
        self.assertFalse(any("Intro" in task for task in test_two_tasks))

    def test_exam_schedule_uses_all_mapped_weeks(self):
        analysis = normalize_schedule_analysis(
            {
                "events": [{"type": "exam", "title": "Final Exam", "week": 16, "confidence": "high"}],
                "source_file_weeks": [
                    {"source_file": "week_01.pdf", "week_start": 1, "week_end": 1, "topic": "Intro"},
                    {"source_file": "week_16.pdf", "week_start": 16, "week_end": 16, "topic": "Wrapup"},
                ],
            },
            "2026-04-13",
        )
        plan = generate_smart_schedule("MAJANDUS", "2026-04-13", analysis, "4", "medium")
        exam_tasks = [item["task"] for item in plan if "Final Exam" in item["task"]]
        self.assertTrue(any("Intro" in task for task in exam_tasks))
        self.assertTrue(any("Wrapup" in task for task in exam_tasks))

    def test_attendance_only_does_not_create_fake_study_tasks(self):
        analysis = normalize_schedule_analysis(
            {
                "events": [{"type": "attendance", "title": "Lecture", "week": 2, "confidence": "high"}],
                "source_file_weeks": [],
            },
            "2026-04-13",
        )
        plan = generate_smart_schedule("MAJANDUS", "2026-04-13", analysis, "4", "medium")
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["task"], "Attend: Lecture")

    def test_event_type_aliases_cover_english_estonian_and_russian(self):
        self.assertEqual(normalize_event_type("kontrolltöö"), "test")
        self.assertEqual(normalize_event_type("экзамен"), "exam")
        self.assertEqual(normalize_event_type("loeng"), "attendance")
        self.assertEqual(normalize_event_type("домашнее задание"), "project")
        self.assertEqual(normalize_event_type("tähtaeg"), "deadline")


if __name__ == "__main__":
    unittest.main()
