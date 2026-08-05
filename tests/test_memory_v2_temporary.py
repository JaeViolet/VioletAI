"""Tests for the temporary cross-chat memory lifecycle."""

import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from memory_v2.models import MemoryLayer, ParsedMemory, Provenance  # noqa: E402
from memory_v2.store import MemoryStore  # noqa: E402
from memory_v2.temporary import TemporaryConfig, TemporaryMemory  # noqa: E402


def _events(store: MemoryStore, kind: str) -> list[dict]:
    return [e for e in store.recent_events(limit=500) if e["event"] == kind]


def _temp_memory(store: MemoryStore, key: str, value: str, **kw) -> TemporaryRecord:
    parsed = ParsedMemory(
        category="Temporary",
        subject=kw.get("subject", "user"),
        key=key,
        value=value,
        content=kw.get("content", f"{key}: {value}"),
        importance=kw.get("importance", 5),
        confidence=kw.get("confidence", 0.9),
        provenance=kw.get("provenance", Provenance(conversation_id="conv-1", message_id="m1")),
        unresolved=kw.get("unresolved", False),
        extra=kw.get("extra", {}),
    )
    return store.insert_temporary(
        parsed,
        token_counter=kw.get("token_at_created", 0),
        conversation_index=kw.get("conversation_at_created", 1),
    )


class TemporaryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="vio_tmp_test_"))
        self.db_path = self.tmpdir / "test_tmp.db"
        self.store = MemoryStore(self.db_path)
        self.temp = TemporaryMemory(self.store)


class TemporaryLifecycleTests(TemporaryTestBase):
    def test_persists_across_conversations(self) -> None:
        rec = _temp_memory(self.store, "current project", "build a chat UI", importance=7)
        self.temp.begin_turn("conv-2", 500)
        self.temp.begin_turn("conv-3", 300)
        active = self.temp.active_context()
        self.assertEqual([r.id for r in active], [rec.id])

    def test_expire_by_token_distance_hard_cap(self) -> None:
        rec = _temp_memory(self.store, "note", "shopping list", importance=7, unresolved=1)
        config = TemporaryConfig(max_token_distance=1_000)
        self.temp = TemporaryMemory(self.store, config)
        self.temp.begin_turn("conv-1", 1_500)
        expired = self.temp.sweep()
        reasons = [reason for _, reason in expired]
        self.assertIn("token_distance_exceeded", reasons)
        self.assertEqual([r.id for r in self.store.list_temporary("active")], [])
        events = _events(self.store, "temporary_expired")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["memory_id"], rec.id)

    def test_expire_by_conversation_distance_hard_cap(self) -> None:
        rec = _temp_memory(self.store, "note", "shopping list", importance=7)
        config = TemporaryConfig(max_conversation_distance=3)
        self.temp = TemporaryMemory(self.store, config)
        self.temp.begin_turn("conv-1", 0)
        self.temp.begin_turn("conv-2", 0)
        self.temp.begin_turn("conv-3", 0)
        self.temp.begin_turn("conv-4", 0)
        self.temp.begin_turn("conv-5", 0)
        expired = self.temp.sweep()
        self.assertIn("conversation_distance_exceeded", [reason for _, reason in expired])
        self.assertNotIn(rec.id, [r.id for r in self.temp.active_context()])

    def test_not_expired_in_same_conversation(self) -> None:
        rec = _temp_memory(self.store, "current activity", "refactoring tests", importance=3)
        self.temp.begin_turn("conv-1", 40_000)
        self.temp.begin_turn("conv-1", 40_000)
        self.temp.begin_turn("conv-1", 20_000)
        expired = self.temp.sweep()
        self.assertEqual(expired, [])
        self.assertEqual([r.id for r in self.temp.active_context()], [rec.id])

    def test_decays_across_conversations_but_not_purely_by_time(self) -> None:
        _temp_memory(self.store, "current activity", "refactoring tests", importance=3)
        self.temp.begin_turn("conv-2", 0)
        self.temp.begin_turn("conv-3", 0)
        self.temp.begin_turn("conv-4", 0)
        self.temp.begin_turn("conv-5", 0)
        self.temp.begin_turn("conv-6", 0)
        expired = self.temp.sweep()
        self.assertEqual([reason for _, reason in expired], ["decayed_score_1.93"])

    def test_pending_task_outlives_current_activity(self) -> None:
        _temp_memory(self.store, "current activity", "refactoring tests", importance=3)
        task = _temp_memory(self.store, "plan", "buy milk and pay bills", importance=5, unresolved=1)
        self.temp.begin_turn("conv-2", 0)
        self.temp.begin_turn("conv-3", 0)
        self.temp.begin_turn("conv-4", 0)
        self.temp.begin_turn("conv-5", 0)
        self.temp.begin_turn("conv-6", 0)
        expired = self.temp.sweep()
        expired_ids = {r.id for r, _ in expired}
        self.assertNotIn(task.id, expired_ids)
        self.assertEqual(len(expired), 1)

    def test_reuse_extends_lifetime(self) -> None:
        rec = _temp_memory(self.store, "current project", "chat UI", importance=5)
        for _ in range(5):
            self.store.touch_temporary(rec.id, 0, 1)
        self.temp.begin_turn("conv-2", 0)
        self.temp.begin_turn("conv-3", 0)
        self.temp.begin_turn("conv-4", 0)
        self.temp.begin_turn("conv-5", 0)
        self.temp.begin_turn("conv-6", 0)
        self.temp.begin_turn("conv-7", 0)
        expired = self.temp.sweep()
        self.assertEqual(expired, [])
        self.assertEqual([r.id for r in self.temp.active_context()], [rec.id])

    def test_budget_eviction_keeps_highest_scoring(self) -> None:
        for i in range(8):
            _temp_memory(
                self.store,
                f"item {i}",
                f"value {i}",
                importance=1,
                conversation_at_created=1,
                conversation_at_last_seen=1,
            )
        top = _temp_memory(self.store, "current project", "priority", importance=9)
        config = TemporaryConfig(max_active=5)
        self.temp = TemporaryMemory(self.store, config)
        self.temp.begin_turn("conv-2", 0)
        self.temp.begin_turn("conv-3", 0)
        self.temp.sweep()
        active = self.temp.active_context()
        self.assertLessEqual(len(active), 5)
        self.assertIn(top.id, [r.id for r in active])
        events = _events(self.store, "temporary_expired")
        self.assertIn("budget_eviction", [e["detail"]["reason"] for e in events])

    def test_sweep_records_audit_event(self) -> None:
        _temp_memory(self.store, "note", "stale note", importance=3)
        config = TemporaryConfig(max_conversation_distance=2)
        self.temp = TemporaryMemory(self.store, config)
        self.temp.begin_turn("conv-2", 0)
        self.temp.begin_turn("conv-3", 0)
        self.temp.begin_turn("conv-4", 0)
        self.temp.begin_turn("conv-5", 0)
        self.temp.sweep()
        events = _events(self.store, "temporary_expired")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["layer"], MemoryLayer.TEMPORARY.value)


class TemporaryScoreTests(TemporaryTestBase):
    def test_score_combines_multiple_signals(self) -> None:
        rec = _temp_memory(self.store, "current project", "chat UI", importance=8)
        token_distance = 1_000
        conversation_distance = 2
        now = datetime.now(UTC)
        rec.updated_at = (now - timedelta(hours=1)).isoformat()
        score = self.temp._score(rec, token_distance, conversation_distance, now=now)
        recency = self.temp.config.weight_recency * (1.0 - 1 / self.temp.config.recency_half_life_hours)
        expected = round(
            recency
            + self.temp.config.weight_importance * 0.8
            + self.temp.config.weight_conversation_distance * (1.0 - 2 / self.temp.config.conversation_span)
            + self.temp.config.weight_reuse * 0
            + self.temp.config.weight_unresolved * 0
            - self.temp.config.token_penalty_scale * (1_000 / self.temp.config.max_token_distance),
            3,
        )
        self.assertAlmostEqual(score, expected, places=3)

    def test_high_token_distance_lowers_score(self) -> None:
        rec = _temp_memory(self.store, "current project", "chat UI", importance=5)
        far = self.temp._score(rec, 90_000, 2)
        near = self.temp._score(rec, 1_000, 2)
        self.assertLess(far, near)


if __name__ == "__main__":
    unittest.main()
