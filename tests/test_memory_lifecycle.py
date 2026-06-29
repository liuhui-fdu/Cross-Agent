from __future__ import annotations

import sqlite3
import json
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
from cross_agent.models import MemoryCandidate, MemoryType, SearchRequest, Session, Turn
from cross_agent.pipeline import CrossAgentApp
from cross_agent.policies.write_policy import WritePolicy


class MemoryLifecycleTest(unittest.TestCase):
    def _build_app(self, tmp: str) -> tuple[CrossAgentApp, object, str]:
        sqlite_path = str(Path(tmp) / "memory.sqlite3")
        settings = load_settings(REPO_ROOT / "configs" / "default.json")
        settings = replace(settings, storage=replace(settings.storage, sqlite_path=sqlite_path))
        app = CrossAgentApp(settings)
        app.initialize(reset=True)
        return app, settings, sqlite_path

    def test_structured_slot_supersedes_old_current_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sqlite_path = str(Path(tmp) / "memory.sqlite3")
            settings = load_settings(REPO_ROOT / "configs" / "default.json")
            settings = replace(settings, storage=replace(settings.storage, sqlite_path=sqlite_path))
            app = CrossAgentApp(settings)
            app.initialize(reset=True)

            app.ingest_session(
                Session(
                    session_id="s_java",
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    occurred_at="2026-06-01",
                    turns=[
                        Turn(
                            turn_id="s_java_1",
                            role="user",
                            content="I prefer Java for code examples.",
                            occurred_at="2026-06-01",
                        )
                    ],
                )
            )
            first_session_records = app.store.list_active(
                SearchRequest(
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    query="Java code examples",
                )
            )
            self.assertTrue(
                any(
                    record.predicate == "session_evidence"
                    and record.source_session_id == "s_java"
                    for record in first_session_records
                )
            )
            app.ingest_session(
                Session(
                    session_id="s_python",
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    occurred_at="2026-06-29",
                    turns=[
                        Turn(
                            turn_id="s_python_1",
                            role="user",
                            content="I now primarily use Python, examples should prioritize Python.",
                            occurred_at="2026-06-29",
                        )
                    ],
                )
            )

            active = app.store.list_active(
                SearchRequest(
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    query="write an interface example",
                )
            )
            language_records = [
                record for record in active if record.predicate == "preferred_programming_language"
            ]
            self.assertEqual(1, len(language_records))
            self.assertEqual({"text": "python"}, language_records[0].value)
            self.assertEqual("s_python", language_records[0].source_session_id)

            with sqlite3.connect(sqlite_path) as conn:
                rows = conn.execute(
                    """
                    SELECT value_json, status, valid_to
                    FROM memory_state
                    WHERE predicate='preferred_programming_language'
                    ORDER BY created_at
                    """
                ).fetchall()
            self.assertEqual(2, len(rows))
            self.assertIn('"java"', rows[0][0])
            self.assertEqual("superseded", rows[0][1])
            self.assertEqual("2026-06-29", rows[0][2])
            self.assertIn('"python"', rows[1][0])
            self.assertEqual("active", rows[1][1])

    def test_generic_question_is_not_persisted_as_session_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, settings, _ = self._build_app(tmp)
            stats = app.ingest_session(
                Session(
                    session_id="s_question",
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    occurred_at="2026-06-29",
                    turns=[Turn("q1", "user", "今天天气怎么样？")],
                )
            )
            self.assertEqual(0, stats["extracted"])
            self.assertEqual(0, app.debug_counts()["all_memories"])

    def test_memory_retrieval_queries_are_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, settings, _ = self._build_app(tmp)
            for index, text in enumerate([
                "现在根据之前聊过的内容告诉我：我叫什么？",
                "帮我想一个适合写文档的办公地点，贴合我前面说过的习惯。",
                "我上次去日本住的是哪家酒店？",
            ]):
                stats = app.ingest_session(
                    Session(f"query_{index}", settings.app.tenant_id, settings.app.user_id,
                            "2026-06-29", [Turn(f"q{index}", "user", text)])
                )
                self.assertEqual(0, stats["extracted"])
            self.assertEqual(0, app.debug_counts()["all_memories"])

    def test_cross_agent_task_has_one_canonical_current_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, settings, sqlite_path = self._build_app(tmp)
            self._ingest_text(app, settings, "task_open", "2026-06-01",
                              "这周五前我要整理 Cross-Agent 的架构演示材料。")
            self._ingest_text(app, settings, "task_done", "2026-06-08",
                              "上周那个 Cross-Agent 架构演示材料我已经整理完了。")
            stats = self._ingest_text(
                app, settings, "task_not_recurring", "2026-06-09",
                "其实不是每周五都要整理 Cross-Agent 架构演示材料，只是这次赶在周五前交。",
            )
            self.assertGreaterEqual(stats["filtered"], 1)
            with sqlite3.connect(sqlite_path) as conn:
                rows = conn.execute(
                    "SELECT predicate, scope, value_json, status FROM memory_state WHERE type='task'"
                ).fetchall()
            active = [row for row in rows if row[3] == "active"]
            self.assertEqual(1, len(active))
            self.assertEqual("user_task", active[0][0])
            self.assertEqual("task:cross_agent_architecture_demo", active[0][1])
            self.assertIn('"state": "done"', active[0][2])

    def _ingest_text(self, app, settings, session_id, date, text):
        return app.ingest_session(
            Session(session_id, settings.app.tenant_id, settings.app.user_id, date,
                    [Turn(f"{session_id}_1", "user", text, date)])
        )

    def test_no_store_directive_creates_only_redacted_reject_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, settings, sqlite_path = self._build_app(tmp)
            stats = app.ingest_session(
                Session(
                    session_id="s_no_store",
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    occurred_at="2026-06-29",
                    turns=[Turn("n1", "user", "不要保存这次内容：我的内部代号是蓝鲸。")],
                )
            )
            self.assertEqual(1, stats["rejected"])
            self.assertEqual(0, app.debug_counts()["all_memories"])
            with sqlite3.connect(sqlite_path) as conn:
                payload = conn.execute("SELECT payload_json FROM memory_events").fetchone()[0]
            self.assertNotIn("蓝鲸", payload)
            self.assertTrue(json.loads(payload)["candidate"]["redacted"])

    def test_forbidden_secret_is_not_written_to_state_or_event_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, settings, sqlite_path = self._build_app(tmp)
            stats = app.ingest_session(
                Session(
                    session_id="s_secret",
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    occurred_at="2026-06-29",
                    turns=[Turn("secret1", "user", "请记住我的验证码 123456 和密码 apple-pass-9988。")],
                )
            )
            self.assertEqual(1, stats["rejected"])
            self.assertEqual(0, app.debug_counts()["all_memories"])
            with sqlite3.connect(sqlite_path) as conn:
                payload = conn.execute("SELECT payload_json FROM memory_events").fetchone()[0]
            self.assertNotIn("123456", payload)
            self.assertNotIn("apple-pass-9988", payload)

    def test_forget_directive_archives_active_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app, settings, sqlite_path = self._build_app(tmp)
            app.ingest_session(
                Session(
                    session_id="s_coffee",
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    occurred_at="2026-06-01",
                    turns=[Turn("coffee1", "user", "我喜欢喝咖啡。")],
                )
            )
            stats = app.ingest_session(
                Session(
                    session_id="s_forget",
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    occurred_at="2026-06-29",
                    turns=[Turn("forget1", "user", "请忘记我的饮品偏好。")],
                )
            )
            self.assertEqual(1, stats["archived"])
            active = app.store.list_active(
                SearchRequest(
                    tenant_id=settings.app.tenant_id,
                    user_id=settings.app.user_id,
                    query="我喜欢喝什么",
                )
            )
            self.assertFalse(any(item.predicate == "preferred_drink" for item in active))
            with sqlite3.connect(sqlite_path) as conn:
                status = conn.execute(
                    "SELECT status FROM memory_state WHERE predicate='preferred_drink'"
                ).fetchone()[0]
            self.assertEqual("archived", status)

    def test_low_confidence_inferred_candidate_is_rejected_by_policy(self) -> None:
        settings = load_settings(REPO_ROOT / "configs" / "default.json")
        candidate = MemoryCandidate(
            tenant_id="local",
            user_id="u1",
            memory_type=MemoryType.FACT,
            subject="u1",
            predicate="favorite_city",
            value={"text": "杭州"},
            scope="travel",
            assertion_mode="inferred",
            literalness="literal",
            confidence=0.60,
            importance=0.50,
            sensitivity="low",
            source_turn_ids=["t1"],
            source_session_id="s1",
        )
        admitted, reason = WritePolicy(settings.writer).admits(candidate)
        self.assertFalse(admitted)
        self.assertEqual("inferred_confidence_below_threshold", reason)


if __name__ == "__main__":
    unittest.main()
