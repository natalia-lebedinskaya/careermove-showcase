from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app import live_jobs, public_release
from api.database import Database


UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]


class RuntimeRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with patch.dict(os.environ, {"CAREERMOVE_SCHEMA_ON_START": "0"}):
            cls.api_main = importlib.import_module("api.main")

    def test_latest_jobs_does_not_use_sqlite_only_group_concat(self) -> None:
        source = (ROOT / "app" / "live_jobs.py").read_text(encoding="utf-8")
        start = source.index("def latest_jobs(")
        end = source.index("\ndef _candidate_options", start)
        self.assertNotIn("GROUP_CONCAT", source[start:end].upper())

    def test_empty_showcase_database_has_every_api_owned_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"DATA_DIR": directory, "DATABASE_URL": "", "BLOB_READ_WRITE_TOKEN": ""},
        ):
            database = Database()
            database.ensure_schema()
            tables = database.query("SELECT name FROM sqlite_master WHERE type='table'")
        names = set(tables["name"].tolist())
        self.assertTrue({"social_links", "company_ratings", "daily_tasks"}.issubset(names))

    def test_interactive_search_batches_are_bounded_and_complete(self) -> None:
        source = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        batch_size = next(
            int(node.value.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "INTERACTIVE_SOURCE_BATCH_SIZE" for target in node.targets)
            and isinstance(node.value, ast.Constant)
        )
        self.assertLessEqual(batch_size, 9)
        self.assertIn("tuple(dict.fromkeys(live_jobs.FAST_SOURCE_NAMES))", source)

    def test_interactive_search_syncs_internships_before_reading_result(self) -> None:
        source = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        start = source.index("def continue_interactive_search(")
        end = source.index("\n@app.post(\"/api/search\"", start)
        block = source[start:end]
        assignment = block.index("internship_result = sync_internships_from_jobs(")
        first_read = block.index("internship_result.get(")
        self.assertLess(assignment, first_read)

    def test_scheduled_search_is_bounded_and_resumable(self) -> None:
        source = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        daily_start = source.index('@app.get("/api/cron/daily-search")')
        daily_end = source.index('\n\n@app.get("/api/cron/search-step")', daily_start)
        daily_block = source[daily_start:daily_end]
        self.assertIn("run_scheduled_search_steps(start_due=True, max_steps=2)", daily_block)
        self.assertNotIn("run_due_scheduled_searches", daily_block)
        self.assertIn('status="queued" if can_retry else "failed"', source)
        self.assertIn('stage=f"retry-{batch_index + 1}"', source)

    def test_scheduled_resume_route_never_starts_an_extra_search(self) -> None:
        source = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        start = source.index('@app.get("/api/cron/search-step")')
        end = source.index("\ndef interactive_source_batches", start)
        block = source[start:end]
        self.assertIn("run_scheduled_search_steps(start_due=False, max_steps=2)", block)

    def test_vercel_cron_has_two_hobby_compatible_daily_slots(self) -> None:
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        schedules = [item["schedule"] for item in config["crons"] if item["path"] == "/api/cron/daily-search"]
        self.assertEqual(schedules, ["0 7 * * *", "0 15 * * *"])

    def test_twice_daily_schedule_uses_independent_moscow_slots(self) -> None:
        morning_finished = datetime(2026, 8, 17, 8, 22, tzinfo=UTC)
        evening_trigger = datetime(2026, 8, 17, 15, 0, tzinfo=UTC)
        self.assertEqual(
            self.api_main._scheduled_slot_id("twice", morning_finished),
            "2026-08-17:morning",
        )
        self.assertEqual(
            self.api_main._scheduled_slot_id("twice", evening_trigger),
            "2026-08-17:evening",
        )
        self.assertTrue(
            self.api_main._scheduled_search_due(
                morning_finished.isoformat(),
                "twice",
                evening_trigger,
            )
        )

    def test_manual_search_does_not_send_a_scheduled_telegram_digest(self) -> None:
        source = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        start = source.index("def run_search_task(")
        end = source.index("\ndef _scheduled_slot_id", start)
        self.assertNotIn("send_telegram_golden_digest", source[start:end])

    def test_scheduled_digest_has_cross_scheduler_slot_deduplication(self) -> None:
        source = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        start = source.index("def _claim_scheduled_slot(")
        end = source.index("\ndef _release_scheduled_slot", start)
        block = source[start:end]
        self.assertIn("ON CONFLICT(user_id,key)", block)
        self.assertIn("WHERE settings.value<>excluded.value", block)
        self.assertIn("RETURNING value", block)

    def test_internships_require_a_verifiable_entry_signal(self) -> None:
        self.assertFalse(self.api_main.internship_without_experience({
            "title": "QA Tester Entry Level",
            "description": "Candidates are reviewed for QA opportunities.",
        }))
        self.assertTrue(self.api_main.internship_without_experience({
            "title": "QA Trainee",
            "description": "Mentorship and training are provided.",
        }))
        self.assertTrue(self.api_main.internship_without_experience({
            "title": "Junior QA",
            "description": "No previous experience required; full training is provided.",
        }))
        self.assertTrue(self.api_main.internship_without_experience({
            "title": "Junior Support Specialist",
            "description": "Structured onboarding and mentorship are provided.",
        }))
        self.assertFalse(self.api_main.internship_without_experience({
            "title": "QA Intern",
            "description": "2 years of commercial experience required.",
        }))

    def test_dedicated_internship_search_is_small_and_non_destructive(self) -> None:
        source = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        self.assertLessEqual(len(self.api_main.INTERNSHIP_SOURCE_NAMES), 16)
        self.assertIn("Himalayas · QA intern", self.api_main.INTERNSHIP_SOURCE_NAMES)
        self.assertIn("Jobicy · Intern", self.api_main.INTERNSHIP_SOURCE_NAMES)
        start = source.index('def collect_internships(')
        end = source.index('\n\n@app.get("/api/hh/status")', start)
        block = source[start:end]
        self.assertIn("stored_jobs_for_internship_scan", block)
        self.assertIn("collection_warning = True", block)
        self.assertNotIn("DELETE FROM side_gigs", block)
        self.assertNotIn("raise HTTPException(status_code=503", block)

    def test_freshly_refound_cards_are_revived_from_archive(self) -> None:
        source = (ROOT / "app" / "live_jobs.py").read_text(encoding="utf-8")
        start = source.index("def save_job(")
        end = source.index("\n\ndef sync_live_job_actuality", start)
        block = source[start:end]
        self.assertIn("status=CASE WHEN COALESCE(status,'found')='archived' THEN 'found' ELSE status END", block)

    def test_cross_origin_settings_allow_put(self) -> None:
        source = (ROOT / "api" / "main.py").read_text(encoding="utf-8")
        self.assertIn('allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]', source)

    def test_api_uses_qualified_live_job_date_parser(self) -> None:
        tree = ast.parse((ROOT / "api" / "main.py").read_text(encoding="utf-8"))
        unqualified = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "parse_datetime"
        ]
        self.assertEqual([], unqualified)

    def test_special_sources_participate_in_interactive_search(self) -> None:
        self.assertTrue(set(live_jobs.SPECIAL_SOURCE_NAMES).issubset(live_jobs.FAST_SOURCE_NAMES))

    def test_special_source_scan_depth_survives_cache_payload(self) -> None:
        rows = live_jobs.annotate_checked_count([{"title": "QA", "url": "https://example.test/qa"}], 50)
        self.assertEqual(50, live_jobs.source_checked_count(rows))

    def test_hirify_dedicated_remote_feed_keeps_bare_remote_for_review(self) -> None:
        page = """
        <article class="vacancy-card" data-vacancy-id="review-1">
          <a class="vacancy-card-link" href="/jobs/review-1"></a>
          <h3 class="title">Junior QA Reviewer</h3>
          <span class="company">Example</span>
          <div class="common-tags"><span class="tag">remote</span><span class="tag">parttime</span></div>
          <div class="vacancy-tags"><span class="tag">manual qa</span></div>
          <span class="date-full">17 авг</span>
        </article>
        """
        now = datetime(2026, 8, 17, tzinfo=UTC)
        self.assertEqual([], live_jobs.parse_hirify_listing(page, now=now))
        rows = live_jobs.parse_hirify_listing(page, now=now, allow_unspecified_remote=True)
        self.assertEqual(1, len(rows))
        self.assertIn("geography to verify", rows[0]["location"])

    def test_pre_migrated_production_skips_search_ddl(self) -> None:
        statements: list[str] = []
        with patch.dict("os.environ", {"CAREERMOVE_SCHEMA_ON_START": "0"}):
            live_jobs.ensure_schema(lambda sql, _params=(): statements.append(sql))
        self.assertEqual([], statements)

    def test_recent_session_does_not_write_on_every_poll(self) -> None:
        token = "x" * 48
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = datetime.now(UTC).replace(microsecond=0)
        frame = pd.DataFrame([{
            "user_id": 7,
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "last_seen_at": now.isoformat(),
            "revoked": 0,
        }])
        writes: list[tuple[str, tuple[object, ...]]] = []

        def query(sql: str, params: tuple[object, ...] = ()) -> pd.DataFrame:
            self.assertIn("last_seen_at", sql)
            self.assertEqual((digest,), params)
            return frame

        def execute(sql: str, params: tuple[object, ...] = ()) -> None:
            writes.append((sql, params))

        self.assertEqual(7, public_release.restore_session(query, execute, token))
        self.assertEqual([], writes)

    def test_stale_session_is_touched(self) -> None:
        token = "y" * 48
        now = datetime.now(UTC).replace(microsecond=0)
        frame = pd.DataFrame([{
            "user_id": 9,
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "last_seen_at": (now - timedelta(minutes=20)).isoformat(),
            "revoked": 0,
        }])
        writes: list[str] = []

        def query(_sql: str, _params: tuple[object, ...] = ()) -> pd.DataFrame:
            return frame

        def execute(sql: str, _params: tuple[object, ...] = ()) -> None:
            writes.append(sql)

        self.assertEqual(9, public_release.restore_session(query, execute, token))
        self.assertEqual(1, len(writes))
        self.assertIn("last_seen_at", writes[0])

    def test_support_candidate_cover_letters_use_masculine_forms_and_public_contacts(self) -> None:
        candidate = {
            "name": "Demo Support Candidate",
            "contact_email": "support.candidate@example.com",
            "salary_min": 700,
        }
        item = {
            "position": "Тестировщик",
            "company": "Example",
            "description": "Ручное тестирование, SQL и Postman. Возможна релокация во Вьетнам.",
            "salary_text": "Не указана",
            "score": 88,
            "work_type": "full-time",
        }
        links = [{"platform": "GitHub", "url": "https://github.com/example"}]
        formal, friendly, detailed = self.api_main._compose_cover_variants(
            item, candidate, ["Postman - Basic", "SQL - Basic", "Bug reports - Basic+"], links, "RU",
        )
        combined = "\n".join((formal, friendly, detailed))
        self.assertIn("Меня зовут Демо-кандидат Support", friendly)
        self.assertIn("Готов коротко рассказать", friendly)
        self.assertNotIn("Готова/готов", combined)
        self.assertNotIn("рада/рад", combined)
        self.assertIn("support.candidate@example.com", combined)
        self.assertIn("https://github.com/example", combined)
        self.assertIn("Postman", detailed)
        self.assertIn("релокационный пакет", combined)
        self.assertIn("международный employment/contractor contract", combined)

    def test_qa_candidate_cover_letters_include_portfolio_and_hide_internal_match_notes(self) -> None:
        candidate = {
            "name": "Demo QA Candidate",
            "contact_email": "qa.candidate@example.com",
            "salary_min": 1000,
        }
        item = {
            "position": "Manual QA Engineer",
            "company": "Example",
            "description": "Manual QA, API testing, regression testing and SQL.",
            "strengths": "совпадает целевая QA/тестовая роль; совпали требования: SQL/databases",
            "final_salary_advice": "Ориентир для переговоров: $1200–$1900 в месяц.",
            "score": 90,
            "work_type": "full-time",
        }
        links = [
            {"platform": "GitHub", "url": "https://github.com/example"},
            {"platform": "Portfolio", "url": "https://example.com/qa-portfolio"},
        ]
        formal, friendly, detailed = self.api_main._compose_cover_variants(
            item, candidate, ["Manual QA - Advanced", "API testing - Intermediate", "SQL - Intermediate"], links, "RU",
        )
        combined = "\n".join((formal, friendly, detailed))
        self.assertIn("Готова коротко рассказать", friendly)
        self.assertIn("Портфолио: https://example.com/qa-portfolio", combined)
        self.assertIn("$1200–$1900 gross/месяц", combined)
        self.assertNotIn("совпадает целевая", detailed)
        self.assertNotIn("совпали требования", detailed)
        self.assertIn("Manual QA", detailed)

    def test_moonlight_cover_letter_asks_about_combining_and_exclusivity(self) -> None:
        questions = self.api_main._cover_questions(
            {
                "title": "Technical Support",
                "category": "Подработка",
                "work_format": "part-time",
                "pay_text": "$12/hour",
                "moonlight_compatible": 1,
            },
            "Demo Support Candidate",
            700,
            "RU",
        )
        text = "\n".join(questions)
        self.assertIn("совмещать эту работу с другой занятостью", text)
        self.assertIn("эксклюзивности", text)


if __name__ == "__main__":
    unittest.main()
