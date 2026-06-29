from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cross_agent.config import load_settings
from cross_agent.models import SearchRequest, Session, Turn
from cross_agent.pipeline import CrossAgentApp
from cross_agent.utils.time import compare_timestamps, parse_datetime


class TemporalConflictTest(unittest.TestCase):
    def _build_app(self, tmp: str) -> tuple[CrossAgentApp, object, str]:
        sqlite_path = str(Path(tmp) / "memory.sqlite3")
        settings = load_settings(REPO_ROOT / "configs" / "default.json")
        settings = replace(settings, storage=replace(settings.storage, sqlite_path=sqlite_path))
        app = CrossAgentApp(settings)
        app.initialize(reset=True)
        return app, settings, sqlite_path

    def test_late_older_value_is_historical_and_cannot_replace_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, settings, sqlite_path = self._build_app(tmp)
            self._ingest(app, settings, "newer", "2026-06-20", "I prefer Python for code examples.")
            stats = self._ingest(
                app,
                settings,
                "older",
                "2026-06-10",
                "I prefer Java for code examples.",
            )

            self.assertEqual(1, stats["historical"])
            active = app.store.list_active(
                SearchRequest(
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    query="code language",
                )
            )
            language = [
                record
                for record in active
                if record.predicate == "preferred_programming_language"
            ]
            self.assertEqual({"text": "python"}, language[0].value)
            with sqlite3.connect(sqlite_path) as conn:
                java = conn.execute(
                    """
                    SELECT status, valid_from, valid_to
                    FROM memory_state
                    WHERE predicate='preferred_programming_language'
                      AND value_json LIKE '%java%'
                    """
                ).fetchone()
            self.assertEqual(("superseded", "2026-06-10", "2026-06-20"), java)

    def test_same_time_equal_strength_conflict_becomes_tentative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, settings, sqlite_path = self._build_app(tmp)
            self._ingest(app, settings, "coffee", "2026-06-20", "I prefer coffee.")
            stats = self._ingest(app, settings, "tea", "2026-06-20", "I prefer tea.")

            self.assertEqual(1, stats["tentative"])
            active = app.store.list_active(
                SearchRequest(
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    query="preferred drink",
                )
            )
            drinks = [record for record in active if record.predicate == "preferred_drink"]
            self.assertEqual({"text": "coffee"}, drinks[0].value)
            with sqlite3.connect(sqlite_path) as conn:
                tea_status = conn.execute(
                    """
                    SELECT status FROM memory_state
                    WHERE predicate='preferred_drink' AND value_json LIKE '%tea%'
                    """
                ).fetchone()[0]
            self.assertEqual("tentative", tea_status)

    def test_tentative_is_promoted_by_later_independent_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, settings, sqlite_path = self._build_app(tmp)
            self._ingest(app, settings, "coffee", "2026-06-20", "I prefer coffee.")
            self._ingest(app, settings, "tea_1", "2026-06-20", "I prefer tea.")
            stats = self._ingest(
                app,
                settings,
                "tea_2",
                "2026-06-21",
                "I prefer tea.",
            )

            self.assertEqual(1, stats["promoted"])
            active = app.store.list_active(
                SearchRequest(
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    query="preferred drink",
                )
            )
            drinks = [record for record in active if record.predicate == "preferred_drink"]
            self.assertEqual(1, len(drinks))
            self.assertEqual({"text": "tea"}, drinks[0].value)
            with sqlite3.connect(sqlite_path) as conn:
                statuses = conn.execute(
                    """
                    SELECT value_json, status FROM memory_state
                    WHERE predicate='preferred_drink'
                    ORDER BY created_at
                    """
                ).fetchall()
            self.assertIn(("{\"text\": \"coffee\"}", "superseded"), statuses)
            self.assertIn(("{\"text\": \"tea\"}", "active"), statuses)

    def test_retrieval_temporal_score_decays_with_age(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, settings, _ = self._build_app(tmp)
            for session_id, date in [("old", "2025-01-01"), ("new", "2026-06-20")]:
                app.ingest_session(
                    Session(
                        session_id=session_id,
                        tenant_id=settings.app.tenant_id,
                        user_id=settings.app.user_id,
                        occurred_at=date,
                        turns=[Turn(f"{session_id}_1", "user", "Project Alpha note.", date)],
                        metadata={"force_session_evidence": True},
                    )
                )
            evidence = app.search(
                SearchRequest(
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    query="Project Alpha note",
                    occurred_at="2026-06-29",
                    top_k=5,
                )
            )
            scores = {
                item.memory.source_session_id: item.temporal_score
                for item in evidence.items
            }
            self.assertGreater(scores["new"], scores["old"])

    def test_implausible_future_valid_from_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, settings, _ = self._build_app(tmp)
            stats = self._ingest(
                app,
                settings,
                "future",
                "2099-01-01",
                "I prefer Rust for code examples.",
            )
            self.assertGreaterEqual(stats["filtered"], 1)
            self.assertEqual(0, app.debug_counts()["all_memories"])

    def test_actmem_timestamp_format_is_comparable(self) -> None:
        self.assertIsNotNone(parse_datetime("2024/02/20 (Tue) 05:25"))
        self.assertEqual(
            -1,
            compare_timestamps(
                "2024/02/20 (Tue) 05:25",
                "2024/02/21 (Wed) 02:37",
            ),
        )

    def test_expired_active_record_is_hard_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, settings, sqlite_path = self._build_app(tmp)
            self._ingest(app, settings, "old_task", "2026-06-01", "I need to prepare slides.")
            with sqlite3.connect(sqlite_path) as conn:
                conn.execute(
                    "UPDATE memory_state SET valid_to='2026-06-05' WHERE source_session_id='old_task'"
                )
            records = app.store.list_active(
                SearchRequest(settings.app.tenant_id, settings.app.user_id, "slides", "2026-06-06")
            )
            self.assertEqual([], records)

    def _ingest(self, app, settings, session_id: str, date: str, text: str):
        return app.ingest_session(
            Session(
                session_id=session_id,
                tenant_id=settings.app.tenant_id,
                user_id=settings.app.user_id,
                occurred_at=date,
                turns=[Turn(f"{session_id}_1", "user", text, date)],
            )
        )


if __name__ == "__main__":
    unittest.main()
