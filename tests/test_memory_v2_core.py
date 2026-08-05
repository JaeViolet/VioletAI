"""Tests for the Memory V2 core package: models, normalize, embeddings."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from memory_v2.embeddings import (  # noqa: E402
    EMBEDDING_MODEL_NAME,
    cosine_similarity,
    embed_key_value,
    embed_text,
)
from memory_v2.models import (  # noqa: E402
    CATEGORIES,
    MemoryCategory,
    MemoryLayer,
    MemoryRecord,
    MutationKind,
    MutationStatus,
    ParsedMemory,
    Provenance,
    ProvenanceKind,
)
from memory_v2.normalize import (  # noqa: E402
    canonical_key,
    canonical_text,
    keys_equivalent,
    subjects_overlap,
)


class NormalizeTests(unittest.TestCase):
    def test_canonical_text_case_and_synonyms(self) -> None:
        self.assertEqual(canonical_text("Favourite Colour"), "favorite color")
        self.assertEqual(canonical_text("My Fav Movie"), "my favorite film")
        self.assertEqual(canonical_text("  the   CAT  "), "the cat")

    def test_canonical_key(self) -> None:
        self.assertEqual(
            canonical_key("Preferences", "user", "Favorite Color"),
            canonical_key("preferences", "user", "favourite colour"),
        )
        self.assertEqual(
            canonical_key("User", "user", "location"),
            canonical_key("user", "user", "city"),
        )

    def test_canonical_key_separates_attributes(self) -> None:
        color = canonical_key("Preferences", "user", "favorite color")
        drink = canonical_key("Preferences", "user", "favorite drink")
        self.assertNotEqual(color, drink)

    def test_category_aliases(self) -> None:
        self.assertEqual(
            canonical_key("preference", "user", "x"),
            canonical_key("Preferences", "user", "x"),
        )

    def test_keys_equivalent_synonym_families(self) -> None:
        self.assertTrue(keys_equivalent("favorite color", "favourite colour"))
        self.assertTrue(keys_equivalent("favorite movie", "favorite film"))
        self.assertTrue(keys_equivalent("job", "occupation"))
        self.assertFalse(keys_equivalent("favorite color", "favorite drink"))
        self.assertFalse(keys_equivalent("pet name", "project name"))

    def test_subjects_overlap(self) -> None:
        self.assertTrue(subjects_overlap("project Apollo", "apollo"))
        self.assertTrue(subjects_overlap("project Apollo", "project apollo"))
        self.assertFalse(subjects_overlap("project Apollo", "project Artemis"))


class EmbeddingTests(unittest.TestCase):
    def test_embedding_model_name(self) -> None:
        self.assertEqual(EMBEDDING_MODEL_NAME, "local-hash-ngram-v2")

    def test_deterministic(self) -> None:
        self.assertEqual(embed_text("favorite color violet"), embed_text("favorite color violet"))

    def test_empty_text(self) -> None:
        self.assertEqual(embed_text(""), {})

    def test_cosine_similarity_bounds(self) -> None:
        self.assertEqual(cosine_similarity({}, {}), 0.0)
        self.assertEqual(cosine_similarity(embed_text("a"), {}), 0.0)
        self.assertAlmostEqual(cosine_similarity(embed_text("same"), embed_text("same")), 1.0, places=6)

    def test_semantic_variants_share_signal(self) -> None:
        similar = cosine_similarity(embed_text("favorite color"), embed_text("favourite colour"))
        unrelated = cosine_similarity(embed_text("favorite color"), embed_text("cat food"))
        self.assertGreater(similar, unrelated)

    def test_embed_key_value_uses_all_fields(self) -> None:
        vector = embed_key_value("favorite color", "violet", subject="user", category="Preferences")
        self.assertIn("w:favorite", vector)
        self.assertIn("w:violet", vector)


class ModelTests(unittest.TestCase):
    def test_categories(self) -> None:
        self.assertEqual(CATEGORIES, ("User", "Preferences", "Projects", "People", "Facts"))

    def test_memory_category_enum_values(self) -> None:
        self.assertEqual(MemoryCategory.PREFERENCES.value, "Preferences")

    def test_layer_active_properties(self) -> None:
        durable = MemoryRecord(
            id="1",
            layer=MemoryLayer.DURABLE,
            category="User",
            subject="user",
            key="name",
            value="Ada",
            canonical_key="user:user:name",
            content="name is Ada",
            importance=5,
            confidence=0.9,
            provenance=Provenance(conversation_id="c1", user_text="remember my name is Ada"),
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        self.assertTrue(durable.active)
        self.assertTrue(durable.durable)
        archived = MemoryRecord(
            id="2",
            layer=MemoryLayer.ARCHIVED,
            category="User",
            subject="user",
            key="name",
            value="Grace",
            canonical_key="user:user:name",
            content="name is Grace",
            importance=5,
            confidence=0.9,
            provenance=Provenance(),
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
            archived_at="2026-01-02T00:00:00+00:00",
        )
        self.assertFalse(archived.active)
        self.assertFalse(archived.durable)

    def test_parsed_memory_defaults(self) -> None:
        parsed = ParsedMemory(category="User", subject="user", key="name", value="Ada")
        self.assertEqual(parsed.importance, 5)
        self.assertEqual(parsed.language, "en")
        self.assertEqual(parsed.provenance.kind, ProvenanceKind.EXPLICIT)

    def test_mutation_kinds_and_statuses(self) -> None:
        self.assertIn(MutationKind.MERGE, MutationKind)
        self.assertIn(MutationStatus.MULTIPLE_MATCHES, MutationStatus)


if __name__ == "__main__":
    unittest.main()
