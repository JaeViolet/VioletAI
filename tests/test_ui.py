"""Lightweight tests that do not require a running Ollama server."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QMessageBox, QToolButton, QWidget  # noqa: E402

from config import DEFAULT_MODEL_NAME, MEMORY_LOG_PATH, SYSTEM_PROMPT  # noqa: E402
from conversation_store import ConversationStore  # noqa: E402
import design  # noqa: E402
from design import Colors, PNG_CONTROL_ICON_SIZE, asset_icon_path, icon  # noqa: E402
from main import MainWindow  # noqa: E402
from memory_embeddings import EMBEDDING_MODEL_NAME, canonical_key, cosine_similarity, embed_text  # noqa: E402
from memory_intent import CREATE, DELETE, IGNORE, RETRIEVE, UPDATE, MemoryAnalysis, MemoryIntentClassifier  # noqa: E402
from memory_models import CATEGORIES, ParsedMemory  # noqa: E402
from memory_service import (  # noqa: E402
    INVALID_REFERENCE,
    MULTIPLE_MATCHES,
    NO_MATCH,
    WRITE_FAILED,
    SUCCESS,
    MemoryService,
)
from memory_store import MemoryStore, MemoryStoreError, normalize_category, normalize_key  # noqa: E402
from ollama_client import InvalidStreamError, OllamaWorker, discover_models, iter_message_chunks  # noqa: E402
from prompts import build_ollama_messages, format_relevant_memories  # noqa: E402
from sidebar import ChatSidebar  # noqa: E402
from widgets import AutoGrowingInput, CodeBlock, MarkdownView, MessageActions, MessageBubble  # noqa: E402


class ChatFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _window_with_temp_store(self) -> tuple[MainWindow, tempfile.TemporaryDirectory]:
        temp_dir = tempfile.TemporaryDirectory()
        store = ConversationStore(Path(temp_dir.name))
        memory_store = MemoryStore(Path(temp_dir.name) / "memory.db")
        patcher = patch("main.ConversationStore", return_value=store)
        memory_patcher = patch("main.MemoryStore", return_value=memory_store)
        refresh = patch.object(MainWindow, "_refresh_models")
        patcher.start()
        memory_patcher.start()
        refresh.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(memory_patcher.stop)
        self.addCleanup(refresh.stop)
        self.addCleanup(temp_dir.cleanup)
        return MainWindow(), temp_dir

    def test_markdown_message_splits_out_code_block(self) -> None:
        bubble = MessageBubble(
            "Text before.\n\n```python\nprint('hello')\n```\n\nText after.",
            "assistant",
        )
        self.assertEqual(bubble.layout.count(), 3)

    def test_long_assistant_message_grows_without_vertical_scrollbar(self) -> None:
        bubble = MessageBubble("# Heading\n\n" + "A wrapped assistant sentence. " * 120, "assistant")
        bubble.setFixedWidth(520)
        bubble.show()
        self.app.processEvents()
        markdown = bubble.findChild(MarkdownView)
        self.assertIsNotNone(markdown)
        assert markdown is not None
        self.assertGreater(markdown.height(), 100)
        self.assertEqual(markdown.verticalScrollBar().maximum(), 0)
        bubble.close()

    def test_enter_sends_and_shift_enter_adds_newline(self) -> None:
        editor = AutoGrowingInput()
        emissions: list[bool] = []
        editor.send_requested.connect(lambda: emissions.append(True))
        enter = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        editor.keyPressEvent(enter)
        self.assertEqual(emissions, [True])

        shifted_enter = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.ShiftModifier,
        )
        editor.keyPressEvent(shifted_enter)
        self.assertEqual(editor.toPlainText(), "\n")

    def test_prompt_history_navigation_preserves_draft(self) -> None:
        editor = AutoGrowingInput()
        editor.remember_prompt("first")
        editor.remember_prompt("second")
        editor.setPlainText("draft")
        up = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
        down = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
        editor.keyPressEvent(up)
        self.assertEqual(editor.toPlainText(), "second")
        editor.keyPressEvent(up)
        self.assertEqual(editor.toPlainText(), "first")
        editor.keyPressEvent(down)
        self.assertEqual(editor.toPlainText(), "second")
        editor.keyPressEvent(down)
        self.assertEqual(editor.toPlainText(), "draft")

    def test_stream_parser_combines_chunks_and_rejects_bad_json(self) -> None:
        lines = [
            '{"message":{"content":"Hello"},"done":false}',
            '{"message":{"content":" there"},"done":true}',
        ]
        self.assertEqual(list(iter_message_chunks(lines)), [("Hello", False), (" there", True)])
        with self.assertRaises(InvalidStreamError):
            list(iter_message_chunks(["not-json"]))

    def test_stream_parser_accepts_lines_as_they_arrive(self) -> None:
        lines = (line for line in ['{"message":{"content":"A"},"done":false}', '{"done":true}'])
        self.assertEqual(list(iter_message_chunks(lines)), [("A", False), ("", True)])

    def test_conversation_persistence_round_trips_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Hello, Violet"})
            conversation.messages.append({"role": "assistant", "content": "Saved as UTF-8."})
            store.save(conversation)
            restored = store.load_latest()
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.id, conversation.id)
            self.assertEqual(restored.messages[1]["content"], "Hello, Violet")

    def test_memory_database_creation_schema_and_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.db")
            self.assertEqual(store.schema_version(), 1)
            record = store.add_memory(
                ParsedMemory("Preferences", "user", "favorite color", "紫", "favorite color is 紫"),
                "conversation-1",
                "1",
                "Remember that my favorite color is 紫.",
            )
            self.assertTrue((Path(temp_dir) / "memory.db").exists())
            self.assertEqual(record.source_user_text, "Remember that my favorite color is 紫.")
            self.assertEqual(store.get(record.id).value, "紫")
            self.assertEqual(store.get(record.id).category, "Preferences")

    def test_explicit_memory_creation_duplicate_and_conflict_superseding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            first = service.handle_explicit_intent(
                "Remember that my favorite color is purple.",
                "c1",
                "1",
            )
            self.assertTrue(first.remembered)
            duplicate = service.handle_explicit_intent(
                "Please remember my favorite color is purple.",
                "c1",
                "2",
            )
            self.assertTrue(duplicate.remembered)
            self.assertEqual(len(service.store.list_memories()), 1)
            conflict = service.handle_explicit_intent(
                "Remember that my favorite color is blue.",
                "c1",
                "3",
            )
            self.assertTrue(conflict.remembered)
            active = service.store.list_memories()
            all_records = service.store.list_memories(include_archived=True)
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].value, "blue")
            self.assertEqual(active[0].category, "User")
            self.assertEqual(len(all_records), 2)
            self.assertTrue(any(not record.active for record in all_records))

    def test_explicit_memory_creation_supports_natural_variations(self) -> None:
        phrases = [
            ("Save my favorite snack is apples.", "favorite snack", "apples", "User"),
            ("Store project VioletAI is a local assistant.", "description", "a local assistant", "Projects"),
            ("Note that I prefer compact UI.", "preference", "compact UI", "Preferences"),
            ("Keep in mind my birthday is June 1.", "birthday", "June 1", "User"),
            ("Create a memory my cat is Luna.", "cat", "Luna", "User"),
            ("Add this to your memory: my favorite city is Montreal.", "favorite city", "Montreal", "User"),
        ]
        for phrase, key, value, category in phrases:
            with self.subTest(phrase=phrase), tempfile.TemporaryDirectory() as temp_dir:
                service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
                result = service.handle_explicit_intent(phrase, "c1", "1")
                self.assertTrue(result.remembered)
                self.assertIsNone(result.error_code)
                memory = service.retrieve(key, mark_accessed=False)[0]
                self.assertEqual(memory.value, value)
                self.assertEqual(memory.category, category)

    def test_explicit_memory_update_phrases_supersede_existing_value(self) -> None:
        phrases = [
            "Update my favorite color to blue.",
            "Change my favorite color to blue.",
            "My favorite color is now blue.",
            "In your memory it says my favorite color is purple; change it to blue.",
            "Replace my saved favorite color with blue.",
            "Edit my favorite color to blue.",
            "Replace my favorite color with blue.",
        ]
        for phrase in phrases:
            with self.subTest(phrase=phrase), tempfile.TemporaryDirectory() as temp_dir:
                service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
                service.handle_explicit_intent("Remember that my favorite color is purple.", "c1", "1")
                result = service.handle_explicit_intent(phrase, "c1", "2")
                self.assertTrue(result.updated)
                active = service.retrieve("favorite color", mark_accessed=False)
                all_records = service.store.list_memories(include_archived=True)
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0].value, "blue")
                self.assertTrue(any(record.value == "purple" and not record.active for record in all_records))

    def test_memory_update_non_command_ambiguous_and_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            service.handle_explicit_intent("Remember that my favorite color is purple.", "c1", "1")
            self.assertFalse(service.handle_explicit_intent("Blue is a nice color.", "c1", "2").handled)
            service.handle_explicit_intent("Remember that my favorite color backup is violet.", "c1", "3")
            ambiguous = service.handle_explicit_intent("Change my favorite color to green.", "c1", "4")
            self.assertTrue(ambiguous.clarification_needed)
            self.assertIn("multiple", ambiguous.response.casefold())
            service.store.archive(service.retrieve("favorite color backup", mark_accessed=False)[0].id)
            unicode_update = service.handle_explicit_intent("Update my favorite color to blå.", "c1", "5")
            self.assertTrue(unicode_update.updated)
            self.assertEqual(service.retrieve("favorite color", mark_accessed=False)[0].value, "blå")

    def test_memory_intent_classifier_routes_structured_actions(self) -> None:
        classifier = MemoryIntentClassifier()
        self.assertEqual(classifier.classify("Remember my favorite color is blue.").action, CREATE)
        self.assertEqual(classifier.classify("My favorite color is blue now.").action, UPDATE)
        self.assertEqual(classifier.classify("Change my favorite color to blue.").action, UPDATE)
        self.assertEqual(classifier.classify("Delete the memory about my favorite color.").action, DELETE)
        self.assertEqual(classifier.classify("What do you remember about me?").action, RETRIEVE)
        self.assertEqual(classifier.classify("Blue is a nice color.").action, IGNORE)

    def test_memory_analysis_schema_handles_embedded_and_contextual_phrases(self) -> None:
        classifier = MemoryIntentClassifier(use_llm=False)
        analysis = classifier.analyze("Hello! Please remember that my favorite color is violet.")
        self.assertTrue(analysis.memory_related)
        self.assertEqual(analysis.action, CREATE)
        self.assertEqual(analysis.canonical_key, "favorite color")
        self.assertEqual(analysis.value, "violet")

        contextual = classifier.analyze("Remember that.", previous_user_text="I am from Montreal.")
        self.assertEqual(contextual.referenced_previous_user_message, "I am from Montreal.")

        natural_update = classifier.analyze("The color I like most is green now.")
        self.assertEqual(natural_update.action, UPDATE)
        self.assertEqual(natural_update.canonical_key, "favorite color")
        self.assertEqual(natural_update.value, "green")

    def test_every_message_uses_single_memory_analysis_before_chat(self) -> None:
        class RecordingClassifier:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def analyze(self, text: str, previous_user_text: str | None = None) -> MemoryAnalysis:
                self.calls.append(text)
                return MemoryAnalysis(False, "NONE", 0.0, diagnostic_reasoning="test ignore", original_text=text)

        with tempfile.TemporaryDirectory() as temp_dir:
            classifier = RecordingClassifier()
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"), classifier=classifier)
            result = service.process_user_message("Hello there.", "c1", "1")
            self.assertFalse(result.handled)
            self.assertEqual(classifier.calls, ["Hello there."])

    def test_unified_pipeline_create_update_retrieve_delete_and_contextual_update(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            create = service.process_user_message("Hello! Please remember that my favorite color is violet.", "c1", "1")
            self.assertEqual(create.status, SUCCESS)
            self.assertEqual(create.confirmation, "✓ Remembered")
            self.assertEqual(service.retrieve("favorite color", mark_accessed=False)[0].value, "violet")

            update = service.process_user_message("Change it to red.", "c1", "2")
            self.assertEqual(update.status, SUCCESS)
            self.assertEqual(update.confirmation, "Memory updated.")
            self.assertEqual(service.retrieve("favorite color", mark_accessed=False)[0].value, "red")

            natural = service.process_user_message("The color I like most is green now.", "c1", "3")
            self.assertEqual(natural.status, SUCCESS)
            self.assertEqual(service.retrieve("preferred colour", mark_accessed=False)[0].value, "green")

            retrieve = service.process_user_message("What color do I like most?", "c1", "4")
            self.assertEqual(retrieve.status, SUCCESS)
            self.assertIn("green", retrieve.response)

            delete = service.process_user_message("Delete the memory about my favorite color.", "c1", "5")
            self.assertEqual(delete.status, SUCCESS)
            self.assertEqual(delete.confirmation, "Memory removed.")
            self.assertEqual(service.retrieve("favorite color", mark_accessed=False), [])

    def test_contextual_delete_and_structured_failures_use_unified_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            service.process_user_message("Remember my favorite movie is Interstellar.", "c1", "1")
            delete = service.process_user_message("Delete that.", "c1", "2", previous_user_text="My favorite movie is Interstellar.")
            self.assertEqual(delete.status, SUCCESS)
            self.assertTrue(delete.removed)

            failed = service.process_user_message("Delete that.", "c1", "3")
            self.assertEqual(failed.status, INVALID_REFERENCE)
            self.assertNotEqual(failed.confirmation, "Memory removed.")

    def test_memory_diagnostics_disabled_by_default_and_enabled_writes_log(self) -> None:
        try:
            MEMORY_LOG_PATH.unlink()
        except FileNotFoundError:
            pass
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            service.process_user_message("Remember my favorite color is violet.", "c1", "1")
            self.assertFalse(MEMORY_LOG_PATH.exists())
            result = service.process_user_message("Change it to red.", "c1", "2", diagnostics_enabled=True)
            self.assertEqual(result.status, SUCCESS)
            self.assertTrue(MEMORY_LOG_PATH.exists())
            log_text = MEMORY_LOG_PATH.read_text(encoding="utf-8")
            self.assertIn("[Memory Diagnostics]", log_text)
            self.assertIn("Stage", log_text or "Stage")
            self.assertIn("Memory Related", log_text)
            self.assertIn("Structured Result", log_text)
            self.assertIn("Memory updated.", log_text)

    def test_memory_diagnostics_logs_failed_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            result = service.process_user_message("Delete that.", "c1", "1", diagnostics_enabled=True)
            self.assertEqual(result.status, INVALID_REFERENCE)
            log_text = MEMORY_LOG_PATH.read_text(encoding="utf-8")
            self.assertIn("Failed Stage", log_text)
            self.assertIn("Context Resolution", log_text)

    def test_semantic_memory_retrieval_handles_equivalent_color_queries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            service.handle_explicit_intent("Remember my favorite color is purple.", "c1", "1")
            queries = ["favorite color", "fav color", "preferred colour", "the color I like most"]
            for query in queries:
                with self.subTest(query=query):
                    records = service.retrieve(query, mark_accessed=False)
                    self.assertEqual(records[0].value, "purple")
            self.assertEqual(EMBEDDING_MODEL_NAME, "local-hash-ngram-v1")
            self.assertGreater(cosine_similarity(embed_text("fav colour"), embed_text("favorite color")), 0.2)

    def test_semantic_duplicate_detection_archives_superseded_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            first = service.handle_explicit_intent("Remember my favorite color is purple.", "c1", "1")
            second = service.handle_explicit_intent("Remember my fav colour is blue.", "c1", "2")
            self.assertTrue(first.remembered)
            self.assertTrue(second.remembered)
            active = service.store.list_memories()
            archived = service.store.list_memories(include_archived=True)
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].value, "blue")
            self.assertTrue(any(record.value == "purple" and not record.active for record in archived))
            self.assertEqual(canonical_key("fav colour"), canonical_key("favorite color"))

    def test_duplicate_migration_archives_existing_duplicate_memories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.db")
            store.add_memory(ParsedMemory("User", "user", "favorite color", "purple", "favorite color is purple"), "c1", "1", "old")
            store.add_memory(ParsedMemory("User", "user", "fav colour", "blue", "fav colour is blue"), "c1", "2", "new")
            service = MemoryService(store)
            active = service.store.list_memories()
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0].value, "blue")

    def test_memory_modes_off_suggest_and_automatic(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            off = service.handle_explicit_intent("Remember my favorite color is purple.", "c1", "1", memory_mode="Off")
            self.assertFalse(off.handled)
            self.assertEqual(service.store.list_memories(), [])

            suggest = service.maybe_capture_automatic_memory("My favorite movie is Interstellar.", "c1", "2", "Suggest")
            self.assertTrue(suggest.handled)
            self.assertFalse(suggest.remembered)
            self.assertIn("Would you like me to remember that?", suggest.response)

            automatic = service.maybe_capture_automatic_memory("My favorite movie is Interstellar.", "c1", "3", "Automatic")
            self.assertTrue(automatic.remembered)
            self.assertEqual(service.retrieve("favorite film", mark_accessed=False)[0].value, "Interstellar")

    def test_contextual_memory_creation_uses_previous_user_message(self) -> None:
        phrases = ["Remember that.", "Save that.", "Store that."]
        for phrase in phrases:
            with self.subTest(phrase=phrase), tempfile.TemporaryDirectory() as temp_dir:
                service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
                result = service.handle_explicit_intent(
                    phrase,
                    "c1",
                    "2",
                    previous_user_text="I'm from Montreal.",
                )
                self.assertTrue(result.remembered)
                memory = service.retrieve("where am I from", mark_accessed=False)[0]
                self.assertEqual(memory.key, "location")
                self.assertEqual(memory.value, "Montreal")
                self.assertEqual(memory.category, "User")
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            result = service.handle_explicit_intent(
                "Remember that.",
                "c1",
                "2",
                previous_user_text="I am from Montreal.",
            )
            self.assertTrue(result.remembered)
            self.assertEqual(service.retrieve("where am I from", mark_accessed=False)[0].key, "location")

    def test_contextual_memory_creation_without_previous_user_message_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            result = service.handle_explicit_intent("Remember that.", "c1", "1")
            self.assertTrue(result.handled)
            self.assertFalse(result.remembered)
            self.assertEqual(result.error_code, INVALID_REFERENCE)
            self.assertEqual(service.store.list_memories(), [])

    def test_contextual_forget_uses_previous_user_message(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            service.handle_explicit_intent("Remember that my favorite movie is Interstellar.", "c1", "1")
            result = service.handle_explicit_intent(
                "Forget that.",
                "c1",
                "3",
                previous_user_text="My favorite movie is Interstellar.",
            )
            self.assertTrue(result.removed)
            self.assertIsNone(result.error_code)
            self.assertEqual(service.retrieve("favorite movie", mark_accessed=False), [])

    def test_delete_and_remove_memory_phrases_return_structured_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            service.handle_explicit_intent("Remember that my favorite color is purple.", "c1", "1")
            delete_result = service.handle_explicit_intent("Delete the memory of favorite color.", "c1", "2")
            self.assertTrue(delete_result.removed)
            self.assertEqual(service.retrieve("favorite color", mark_accessed=False), [])

            missing = service.handle_explicit_intent("Remove the memory about favorite color.", "c1", "3")
            self.assertFalse(missing.removed)
            self.assertEqual(missing.error_code, NO_MATCH)
            self.assertIn("favorite color", missing.response)

    def test_multiple_matching_update_returns_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            service.handle_explicit_intent("Remember that my favorite color is purple.", "c1", "1")
            service.handle_explicit_intent("Remember that my favorite color backup is violet.", "c1", "2")
            result = service.handle_explicit_intent("Change my favorite color to green.", "c1", "3")
            self.assertFalse(result.updated)
            self.assertEqual(result.error_code, MULTIPLE_MATCHES)
            self.assertTrue(result.clarification_needed)

    def test_failed_memory_write_does_not_claim_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.db")
            service = MemoryService(store)
            with patch.object(store, "add_memory", side_effect=RuntimeError("disk is read-only")):
                result = service.handle_explicit_intent("Remember that my favorite color is purple.", "c1", "1")
            self.assertTrue(result.handled)
            self.assertFalse(result.remembered)
            self.assertEqual(result.error_code, WRITE_FAILED)
            self.assertIn("disk is read-only", result.response)
            self.assertEqual(store.list_memories(), [])

    def test_profile_category_migrates_to_user_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "memory.db"
            store = MemoryStore(path)
            legacy = store.add_memory(
                ParsedMemory("profile", "user", "birthday", "June 1", "birthday is June 1"),
                "conversation-1",
                "1",
                "Remember that my birthday is June 1.",
            )
            connection = sqlite3.connect(path)
            try:
                connection.execute(
                    "UPDATE memories SET category='profile', normalized_key=? WHERE id=?",
                    ("profile:user:birthday", legacy.id),
                )
                connection.commit()
            finally:
                connection.close()

            migrated = MemoryStore(path)
            records = migrated.search(category="User")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].category, "User")
            self.assertEqual(records[0].value, "June 1")
            self.assertEqual(normalize_category("profile"), "User")
            self.assertEqual(normalize_key("profile", "user", "birthday"), "user:user:birthday")
            self.assertEqual(MemoryService(migrated).retrieve("birthday", mark_accessed=False)[0].value, "June 1")

    def test_memory_categories_use_title_case_and_filters_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MemoryStore(Path(temp_dir) / "memory.db")
            for category in CATEGORIES:
                store.add_memory(
                    ParsedMemory(category, "user", f"{category.casefold()} key", category, f"{category} memory"),
                    "conversation-1",
                    category,
                    f"Remember {category}.",
                )

            self.assertEqual(CATEGORIES, ("User", "Preferences", "Projects", "People", "Facts", "Temporary"))
            for category in CATEGORIES:
                records = store.search(category=category)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].category, category)

    def test_no_memory_creation_without_explicit_user_intent_and_vancouver_scenario(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            result = service.handle_explicit_intent("Search for the artist Ekkstacy.", "c1", "1")
            self.assertFalse(result.handled)
            self.assertEqual(service.store.list_memories(), [])
            result = service.handle_explicit_intent("The artist is from Vancouver.", "c1", "2")
            self.assertFalse(result.handled)
            self.assertEqual(service.retrieve("Where am I from?"), [])

    def test_memory_retrieval_limits_category_archive_and_expiration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"), max_retrieved=2)
            service.remember(ParsedMemory("Preferences", "user", "favorite color", "purple", "favorite color is purple"), "c", "1", "Remember that my favorite color is purple.")
            service.remember(ParsedMemory("User", "user", "location", "Toronto", "I live in Toronto"), "c", "2", "Remember that I live in Toronto.")
            expired = service.remember(ParsedMemory("Temporary", "user", "snack", "tea", "snack is tea", expires_at="2000-01-01T00:00:00+00:00"), "c", "3", "Remember my snack is tea until tomorrow.")
            service.store.archive(expired.id)
            color = service.retrieve("favorite color", category="Preferences")
            self.assertEqual(len(color), 1)
            self.assertEqual(color[0].value, "purple")
            self.assertLessEqual(len(service.retrieve("what do you remember about me")), 2)
            self.assertTrue(all(record.active for record in service.retrieve("tea")))
            self.assertEqual(service.retrieve("snack tea"), [])

    def test_forgetting_manual_edit_and_prompt_construction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = MemoryService(MemoryStore(Path(temp_dir) / "memory.db"))
            result = service.handle_explicit_intent("Remember that my favorite color is purple.", "c1", "1")
            memory = result.memories[0]
            service.store.edit(memory.id, memory.category, memory.subject, memory.key, "blue", "favorite color is blue")
            edited = service.store.get(memory.id)
            self.assertTrue(edited.manually_edited)
            messages = build_ollama_messages(
                [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "What is my favorite color?"}],
                [edited],
                SYSTEM_PROMPT,
            )
            self.assertEqual(messages[0]["role"], "system")
            self.assertIn("[Relevant user memories]", messages[1]["content"])
            self.assertEqual(messages[-1]["content"], "What is my favorite color?")
            forget = service.handle_explicit_intent("Forget my favorite color.", "c1", "2")
            self.assertTrue(forget.removed)
            self.assertEqual(service.retrieve("favorite color"), [])

    def test_memory_store_database_errors_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            bad_path = Path(temp_dir) / "memory.db"
            bad_path.write_text("not sqlite", encoding="utf-8")
            with self.assertRaises(MemoryStoreError):
                MemoryStore(bad_path)

    def test_conversation_grouping_search_rename_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Write Python code"})
            store.save(conversation)
            self.assertEqual(store.search("python")[0].id, conversation.id)
            self.assertTrue(any(item.id == conversation.id for item in store.grouped()["Today"]))
            renamed = store.rename(conversation.id, "Code notes")
            self.assertIsNotNone(renamed)
            assert renamed is not None
            self.assertEqual(renamed.title, "Code notes")
            self.assertTrue(store.delete(conversation.id))
            self.assertEqual(store.list_conversations(), [])

    def test_existing_conversation_file_compatibility_adds_title(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "old.json"
            path.write_text(
                '{"id":"old","created_at":"2026-08-02T00:00:00+00:00",'
                '"updated_at":"2026-08-02T00:00:00+00:00","messages":['
                '{"role":"system","content":"s"},{"role":"user","content":"First prompt here"}]}',
                encoding="utf-8",
            )
            conversation = ConversationStore(Path(temp_dir)).load(path)
            self.assertIsNotNone(conversation)
            assert conversation is not None
            self.assertEqual(conversation.title, "First prompt here")

    def test_window_starts_empty_without_deleting_saved_conversations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Keep me in sidebar"})
            store.save(conversation)
            with patch("main.ConversationStore", return_value=store), patch.object(MainWindow, "_refresh_models"):
                window = MainWindow()
            self.assertEqual(len(window.messages), 1)
            self.assertEqual(window.messages[0]["role"], "system")
            self.assertEqual(store.load_by_id(conversation.id).messages[-1]["content"], "Keep me in sidebar")
            self.assertIn("VioletAI can make mistakes", window.footer_status.text())
            self.assertTrue(window.send_button.isEnabled())
            window.close()

    def test_empty_chat_branding_has_no_auto_assistant_message(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        labels = [label.text() for label in window.findChildren(QLabel)]
        self.assertEqual(len(window.messages), 1)
        self.assertEqual(window.messages[0]["role"], "system")
        self.assertEqual(window.windowTitle(), "VioletAI")
        self.assertIn("VioletAI", labels)
        window.close()

    def test_immediate_scroll_after_user_message(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        with patch.object(window, "_start_generation"):
            for index in range(20):
                window._add_message(f"old {index}", "user")
            self.app.processEvents()
            window.scroll_area.verticalScrollBar().setValue(0)
            window.input_box.setPlainText("Scroll now")
            window.send_message()
            self.app.processEvents()
            bar = window.scroll_area.verticalScrollBar()
            self.assertEqual(bar.value(), bar.maximum())
        window.close()

    def test_user_bubble_is_compact_and_right_aligned_in_content_column(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        bubble = window._add_message("short", "user")
        window.resize(1000, 700)
        window._resize_rows()
        self.app.processEvents()
        content = bubble.parentWidget().parentWidget()
        self.assertLess(bubble.width(), 220)
        self.assertLessEqual(bubble.width(), int(window.composer.maximumWidth() * 2 / 3))
        self.assertEqual(bubble.width(), bubble.parentWidget().width())
        self.assertEqual(bubble.parentWidget().geometry().right(), content.width() - 1)
        self.assertEqual(content.maximumWidth(), window.composer.maximumWidth())
        self.assertIsNone(bubble.parentWidget().parentWidget().graphicsEffect())
        window.close()

    def test_user_bubble_expands_to_two_thirds_before_wrapping(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        text = "word " * 70
        bubble = window._add_message(text, "user")
        window.resize(1000, 700)
        window._resize_rows()
        self.app.processEvents()
        expected_max = int(window.composer.maximumWidth() * 2 / 3)
        self.assertEqual(bubble.width(), expected_max)
        window.close()

    def test_long_user_bubble_wraps_without_internal_scrollbar(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        text = "hello " * 90
        bubble = window._add_message(text, "user")
        window.resize(1000, 700)
        window._resize_rows()
        self.app.processEvents()
        markdown = bubble.findChild(MarkdownView)
        self.assertIsNotNone(markdown)
        assert markdown is not None
        self.assertLessEqual(bubble.width(), int(window.composer.maximumWidth() * 2 / 3))
        self.assertGreater(bubble.height(), 60)
        self.assertEqual(markdown.verticalScrollBar().maximum(), 0)
        window.close()

    def test_mixed_long_messages_do_not_overlap_or_clip(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        messages = [
            ("# Heading\n\n" + "Assistant markdown paragraph. " * 80, "assistant"),
            ("hello " * 120, "user"),
            ("- one\n- two\n- three\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n" + "More text. " * 60, "assistant"),
            ("```python\n" + "\n".join(f"print({index})" for index in range(30)) + "\n```", "assistant"),
        ]
        rows = []
        for text, role in messages:
            bubble = window._add_message(text, role)
            rows.append(bubble.parentWidget().parentWidget().parentWidget())
        window._resize_rows()
        self.app.processEvents()
        last_bottom = -1
        for row in rows:
            self.assertGreaterEqual(row.y(), last_bottom)
            self.assertGreater(row.height(), 0)
            last_bottom = row.geometry().bottom()
        for bubble in window.findChildren(MessageBubble):
            self.assertGreaterEqual(bubble.parentWidget().height(), bubble.height())
        window.close()

    def test_auto_scroll_pauses_and_resumes_near_bottom(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.scroll_area.verticalScrollBar().setRange(0, 100)
        window.scroll_area.verticalScrollBar().setValue(0)
        window._handle_scroll_change(0)
        self.assertFalse(window._auto_scroll_enabled)
        window.scroll_area.verticalScrollBar().setValue(100)
        window._handle_scroll_change(100)
        self.assertTrue(window._auto_scroll_enabled)
        window.close()

    def test_auto_scroll_during_streaming_uses_bottom_when_enabled(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window._auto_scroll_enabled = True
        window._receive_chunk("hello")
        self.app.processEvents()
        self.assertIsNotNone(window.pending_bubble)
        window.close()

    def test_sidebar_filters_conversations_live(self) -> None:
        sidebar = ChatSidebar()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Find me"})
            store.save(conversation)
            sidebar.rebuild(store.grouped("find"), conversation.id)
            self.assertGreater(sidebar.list_layout.count(), 1)

    def test_empty_chats_are_not_saved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            store.save(conversation)
            self.assertEqual(store.list_conversations(), [])

    def test_pinned_conversations_group_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "Pin me"})
            store.save(conversation)
            store.set_pinned(conversation.id, True)
            groups = store.grouped()
            self.assertEqual(groups["Pinned"][0].id, conversation.id)

    def test_sidebar_collapses_to_icon_rail(self) -> None:
        sidebar = ChatSidebar()
        sidebar.set_expanded(False)
        self.assertEqual(sidebar.minimumWidth(), sidebar.COLLAPSED_WIDTH)
        self.assertFalse(sidebar.brand_label.isVisible())
        self.assertFalse(sidebar.expanded_container.isVisible())
        self.assertFalse(sidebar.collapsed_container.isHidden())
        layout = sidebar.collapsed_container.layout()
        self.assertIs(layout.itemAt(0).widget(), sidebar.collapsed_expand_button)
        self.assertIs(layout.itemAt(1).widget(), sidebar.collapsed_search_button)
        self.assertIs(layout.itemAt(2).widget(), sidebar.collapsed_new_chat_button)

    def test_conversation_rows_are_compact_without_permanent_action_buttons(self) -> None:
        sidebar = ChatSidebar()
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ConversationStore(Path(temp_dir))
            conversation = store.create(SYSTEM_PROMPT)
            conversation.messages.append({"role": "user", "content": "A very long row title that should be elided neatly"})
            store.save(conversation)
            sidebar.rebuild(store.grouped(), conversation.id)
            sidebar.resize(sidebar.EXPANDED_WIDTH, 500)
            sidebar.show()
            self.app.processEvents()
        rows = sidebar.findChildren(QWidget, "conversationRow")
        self.assertEqual(rows[0].height(), 34)
        self.assertEqual(rows[0].findChildren(QToolButton), [])
        self.assertEqual(rows[0].x(), 0)
        self.assertEqual(rows[0].width(), sidebar.new_chat_button.width())
        sidebar.close()

    def test_input_placeholder_and_cursor_are_vertically_centered(self) -> None:
        editor = AutoGrowingInput()
        editor.resize(500, editor.MIN_HEIGHT)
        editor._update_height()
        self.assertGreater(editor.document().documentMargin(), 0)
        editor.setPlainText("line one\nline two\nline three")
        self.assertGreater(editor.height(), editor.MIN_HEIGHT)

    def test_composer_switches_between_compact_and_multiline_layouts(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.assertFalse(window._composer_multiline)
        self.assertFalse(window.toolbar_widget.isVisible())
        self.assertEqual(window.input_row.itemAt(0).widget(), window.tools_button)
        self.assertEqual(window.input_row.itemAt(1).widget(), window.input_box)
        self.assertTrue(window.tools_button.isVisible())
        window.input_box.setPlainText("first paragraph\n\nsecond paragraph")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertTrue(window._composer_multiline)
        self.assertTrue(window.toolbar_widget.isVisible())
        self.assertFalse(window.tools_button.isVisible())
        self.assertFalse(window.model_selector.isVisible())
        self.assertEqual(window.input_row.itemAt(1).widget(), window.input_box)
        self.assertEqual(window.toolbar_layout.itemAt(0).widget(), window.toolbar_tools_button)
        self.assertEqual(window.toolbar_layout.itemAt(window.toolbar_layout.count() - 3).widget(), window.toolbar_model_selector)
        window.input_box.setPlainText("short")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertFalse(window._composer_multiline)
        window.close()

    def test_tools_menu_has_independent_placeholder_actions(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        actions = window.tools_button.menu().actions()
        self.assertEqual([action.data() for action in actions], [
            "Web Search",
            "Upload Files",
            "Upload Images",
            "Deep Research",
            "Image Generation",
        ])
        self.assertTrue(all(not action.isEnabled() for action in actions))
        window.close()

    def test_explicit_memory_command_shows_remembered_confirmation(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        with patch.object(window, "_start_generation") as generation:
            window.input_box.setPlainText("Remember that my favorite color is purple.")
            window.send_message()
        generation.assert_not_called()
        labels = [label.text() for label in window.findChildren(QLabel)]
        self.assertIn("✓ Remembered", labels)
        self.assertEqual(window.memory_service.retrieve("favorite color", mark_accessed=False)[0].value, "purple")
        window.close()

    def test_contextual_memory_command_uses_previous_user_message_in_window(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        with patch.object(window, "_start_generation"):
            window.input_box.setPlainText("I'm from Montreal.")
            window.send_message()
        with patch.object(window, "_start_generation") as generation:
            window.input_box.setPlainText("Remember that.")
            window.send_message()
        generation.assert_not_called()
        memory = window.memory_service.retrieve("where am I from", mark_accessed=False)[0]
        self.assertEqual(memory.value, "Montreal")
        labels = [label.text() for label in window.findChildren(QLabel)]
        self.assertIn("✓ Remembered", labels)
        window.close()

    def test_failed_memory_write_does_not_show_remembered_confirmation(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        with patch.object(window.memory_store, "add_memory", side_effect=RuntimeError("disk is read-only")):
            with patch.object(window, "_start_generation") as generation:
                window.input_box.setPlainText("Remember that my favorite color is purple.")
                window.send_message()
        generation.assert_not_called()
        labels = [label.text() for label in window.findChildren(QLabel)]
        self.assertNotIn("✓ Remembered", labels)
        self.assertIn("disk is read-only", window.messages[-1]["content"])
        self.assertEqual(window.memory_store.list_memories(), [])
        window.close()

    def test_settings_overlay_memory_manager_search_edit_delete(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        window.preferences.memory_mode = "Explicit"
        window.memory_service.handle_explicit_intent("Remember that my favorite color is purple.", "c1", "1")
        window.open_settings_overlay()
        self.assertTrue(window.settings_overlay.isVisible())
        self.assertEqual(
            [window.settings_overlay.category_filter.itemText(index) for index in range(window.settings_overlay.category_filter.count())],
            ["All", *CATEGORIES],
        )
        window.settings_overlay.memory_mode.setCurrentText("Explicit")
        self.assertEqual(window.settings_overlay.memory_mode.currentText(), "Explicit")
        window.settings_overlay.memory_mode.setCurrentText("Automatic")
        self.assertEqual(window.preferences.memory_mode, "Automatic")
        self.assertIn("Category", [window.settings_overlay.sort_order.itemText(index) for index in range(window.settings_overlay.sort_order.count())])
        window.settings_overlay.search_input.setText("favorite")
        self.app.processEvents()
        self.assertTrue(window.settings_overlay.store.search("favorite"))
        record = window.settings_overlay.store.search("favorite")[0]
        edited = window.settings_overlay.store.edit(
            record.id,
            record.category,
            record.subject,
            record.key,
            "blue",
            "favorite color is blue",
        )
        self.assertTrue(edited.manually_edited)
        window.settings_overlay.store.archive(record.id)
        self.assertEqual(window.memory_service.retrieve("favorite color"), [])
        window.settings_overlay.store.restore(record.id)
        self.assertEqual(window.memory_service.retrieve("favorite color")[0].value, "blue")
        window.settings_overlay.store.delete(record.id)
        self.assertEqual(window.settings_overlay.store.search("favorite", include_archived=True), [])
        window.close()

    def test_send_and_stop_buttons_have_identical_larger_geometry(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.assertEqual(window.send_button.minimumWidth(), 38)
        self.assertEqual(window.send_button.minimumHeight(), 38)
        self.assertEqual(window.send_button.iconSize().width(), PNG_CONTROL_ICON_SIZE)
        self.assertEqual(window.stop_button.iconSize().width(), PNG_CONTROL_ICON_SIZE)
        self.assertEqual(window.stop_button.minimumWidth(), window.send_button.minimumWidth())
        self.assertEqual(window.stop_button.minimumHeight(), window.send_button.minimumHeight())
        self.assertEqual(window.toolbar_send_button.minimumWidth(), window.send_button.minimumWidth())
        self.assertEqual(window.toolbar_stop_button.minimumHeight(), window.send_button.minimumHeight())
        window.close()

    def test_model_selectors_keep_consistent_geometry_across_composer_modes(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        window._set_model_selector([DEFAULT_MODEL_NAME, "qwen3.5:9b"])
        compact_metrics = (
            window.model_selector.minimumWidth(),
            window.model_selector.maximumWidth(),
            window.model_selector.height(),
            window.model_selector.font().pointSize(),
        )
        window.input_box.setPlainText("line one\nline two\nline three")
        self.app.processEvents()
        window._update_composer_mode()
        expanded_metrics = (
            window.toolbar_model_selector.minimumWidth(),
            window.toolbar_model_selector.maximumWidth(),
            window.toolbar_model_selector.height(),
            window.toolbar_model_selector.font().pointSize(),
        )
        self.assertEqual(compact_metrics, expanded_metrics)
        window.close()

    def test_composer_keeps_pill_radius_and_styled_background(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.assertTrue(window.composer.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
        self.assertEqual(window.composer.frameShape(), QFrame.Shape.NoFrame)
        self.assertTrue(window.composer.property("compact"))
        self.assertIn('border-radius: 25px', window.styleSheet())
        window.input_box.setPlainText("one\ntwo\nthree\nfour")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertFalse(window.composer.property("compact"))
        self.assertTrue(window.composer.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
        self.assertIn("border-radius: 28px", window.styleSheet())
        window.input_box.setPlainText("one")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertTrue(window.composer.property("compact"))
        window.close()

    def test_png_icon_assets_resolve_and_render_at_multiple_sizes(self) -> None:
        for name in ("copy", "regen", "send", "stop"):
            self.assertTrue(asset_icon_path(name).exists())
            for size in (16, 18, 21, 28):
                rendered = icon(name, "white", size)
                self.assertFalse(rendered.isNull())
                pixmap = rendered.pixmap(size, size)
                self.assertFalse(pixmap.isNull(), f"{name} at {size}px did not render")

    def test_missing_png_icon_asset_fails_gracefully(self) -> None:
        with patch.object(design, "ICON_ASSETS_DIR", Path("missing-assets")):
            missing_icon = design.icon("send", "white", 21)
        self.assertTrue(missing_icon.isNull())

    def test_message_action_icons_use_png_assets(self) -> None:
        actions = MessageActions()
        self.assertFalse(actions.copy_button.icon().isNull())
        self.assertFalse(actions.regenerate_button.icon().isNull())
        self.assertEqual(actions.copy_button.iconSize().width(), PNG_CONTROL_ICON_SIZE)
        self.assertEqual(actions.regenerate_button.iconSize().width(), PNG_CONTROL_ICON_SIZE)

    def test_model_selector_chevron_visible_in_compact_expanded_and_disabled(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        self.assertEqual(window.model_selector.arrow_color(), Colors.TEXT_MUTED)
        window.input_box.setPlainText("line one\nline two\nline three")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertEqual(window.toolbar_model_selector.arrow_color(), Colors.TEXT_MUTED)
        window._set_controls_generating(True)
        self.assertEqual(window.toolbar_model_selector.arrow_color(), Colors.TEXT_FAINT)
        window._set_controls_generating(False)
        window.input_box.setPlainText("one")
        self.app.processEvents()
        window._update_composer_mode()
        window._set_controls_generating(True)
        self.assertEqual(window.model_selector.arrow_color(), Colors.TEXT_FAINT)
        window.close()

    def test_composer_width_only_changes_on_window_resize(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.resize(1180, 820)
        window.show()
        self.app.processEvents()
        window._resize_rows()
        initial_width = window.composer.width()
        window.input_box.setPlainText("short single line")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertEqual(window.composer.width(), initial_width)
        window.input_box.setPlainText("one\ntwo\nthree\nfour")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertEqual(window.composer.width(), initial_width)
        window.input_box.setPlainText("short")
        self.app.processEvents()
        window._update_composer_mode()
        self.assertEqual(window.composer.width(), initial_width)
        window._set_controls_generating(True)
        self.assertEqual(window.composer.width(), initial_width)
        window._set_controls_generating(False)
        self.assertEqual(window.composer.width(), initial_width)
        window.resize(900, 700)
        self.app.processEvents()
        window._resize_rows()
        self.assertNotEqual(window.composer.width(), initial_width)
        window.close()

    def test_welcome_content_centers_to_composer_column_after_sidebar_and_resize(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.resize(1180, 820)
        window.show()
        self.app.processEvents()
        window._resize_rows()

        def centers() -> tuple[int, int]:
            welcome_column = window.findChild(QWidget, "welcomeContentColumn")
            self.assertIsNotNone(welcome_column)
            assert welcome_column is not None
            composer_center = window.composer.mapTo(window, window.composer.rect().center()).x()
            welcome_center = welcome_column.mapTo(window, welcome_column.rect().center()).x()
            return composer_center, welcome_center

        composer_center, welcome_center = centers()
        self.assertAlmostEqual(composer_center, welcome_center, delta=2)
        window.sidebar.set_expanded(False)
        self.app.processEvents()
        window._resize_rows()
        composer_center, welcome_center = centers()
        self.assertAlmostEqual(composer_center, welcome_center, delta=2)
        window.sidebar.set_expanded(True)
        window.resize(960, 720)
        self.app.processEvents()
        window._resize_rows()
        composer_center, welcome_center = centers()
        self.assertAlmostEqual(composer_center, welcome_center, delta=2)
        window.close()

    def test_code_blocks_have_no_internal_scrollbars_and_contribute_height(self) -> None:
        code = "def demo():\n" + "\n".join("    print('hello world')" for _ in range(30))
        block = CodeBlock("python", code)
        block.resize(420, 100)
        block.show()
        self.app.processEvents()
        self.assertEqual(block.editor.verticalScrollBar().maximum(), 0)
        self.assertEqual(block.editor.horizontalScrollBar().maximum(), 0)
        self.assertGreater(block.height(), 300)
        block.close()

    def test_search_overlay_filters_and_closes(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        window.open_search_overlay()
        self.assertTrue(window.search_overlay.isVisible())
        self.assertIs(window.search_overlay.parentWidget(), window.chat_panel)
        parent_rect = window.chat_panel.rect()
        self.assertAlmostEqual(
            window.search_overlay.geometry().center().x(),
            parent_rect.center().x(),
            delta=2,
        )
        window.search_overlay.close_overlay()
        self.assertFalse(window.search_overlay.isVisible())
        window.close()

    def test_delete_active_conversation_confirms_and_returns_to_empty_chat(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.messages.append({"role": "user", "content": "delete me"})
        window.store.save(window.conversation)
        deleted_id = window.conversation.id
        with patch("main.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            window.delete_conversation(deleted_id)
        self.assertIsNone(window.store.load_by_id(deleted_id))
        self.assertEqual(len(window.messages), 1)
        self.assertEqual(window.messages[0]["role"], "system")
        window.close()

    @patch("ollama_client.requests.post")
    def test_ollama_worker_combines_streamed_chunks(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = [
            '{"message":{"content":"Hello"},"done":false}',
            '{"message":{"content":" there"},"done":true}',
        ]
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        chunks: list[str] = []
        answers: list[str] = []
        worker.chunk_received.connect(chunks.append)
        worker.finished.connect(answers.append)
        worker.run()
        self.assertEqual(chunks, ["Hello", " there"])
        self.assertEqual(answers, ["Hello there"])
        response.close.assert_called_once()

    @patch("ollama_client.requests.post")
    def test_ollama_worker_cancellation_closes_response(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = ['{"message":{"content":"Hello"},"done":false}']
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        cancelled: list[bool] = []
        finished: list[str] = []
        worker.cancelled.connect(lambda: cancelled.append(True))
        worker.finished.connect(finished.append)
        worker.cancel()
        worker.run()
        self.assertEqual(cancelled, [True])
        self.assertEqual(finished, [])
        response.close.assert_called_once()

    @patch("ollama_client.requests.get")
    def test_model_discovery_reads_ollama_tags(self, get: Mock) -> None:
        response = Mock()
        response.json.return_value = {"models": [{"name": "a:1"}, {"name": "b:2"}]}
        get.return_value = response
        self.assertEqual(discover_models(), ["a:1", "b:2"])

    def test_model_switching_and_disabled_visible_during_generation(self) -> None:
        window, _temp_dir = self._window_with_temp_store()
        window.show()
        window._set_model_selector([DEFAULT_MODEL_NAME, "other:1"])
        window.model_selector.setCurrentText("other:1")
        self.assertEqual(window.active_model, "other:1")
        self.assertEqual(window.toolbar_model_selector.currentText(), "other:1")
        window._set_controls_generating(True)
        self.assertTrue(window.model_selector.isVisible())
        self.assertFalse(window.model_selector.isEnabled())
        self.assertFalse(window.toolbar_model_selector.isEnabled())
        window._set_controls_generating(False)
        self.assertTrue(window.model_selector.isEnabled())
        self.assertTrue(window.toolbar_model_selector.isEnabled())
        window.close()


if __name__ == "__main__":
    unittest.main()
