"""Tests for the semantic embedding abstraction."""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from memory_v2.embeddings import embed_text  # noqa: E402
from memory_v2.semantic import LocalHashSemanticEmbedder, OllamaSemanticEmbedder  # noqa: E402


class LocalHashSemanticEmbedderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.embedder = LocalHashSemanticEmbedder()

    def test_matches_module_functions_exactly(self) -> None:
        self.assertEqual(self.embedder.embed_text("favorite color violet"), embed_text("favorite color violet"))

    def test_deterministic(self) -> None:
        self.assertEqual(self.embedder.embed_text("favorite color"), self.embedder.embed_text("favorite color"))

    def test_empty_text(self) -> None:
        self.assertEqual(self.embedder.embed_text(""), {})

    def test_cosine_similarity_bounds(self) -> None:
        self.assertEqual(self.embedder.cosine_similarity({}, {}), 0.0)
        self.assertAlmostEqual(
            self.embedder.cosine_similarity(self.embedder.embed_text("same"), self.embedder.embed_text("same")),
            1.0,
            places=6,
        )
        unrelated = self.embedder.cosine_similarity(
            self.embedder.embed_text("favorite color"), self.embedder.embed_text("cat food")
        )
        self.assertLess(unrelated, 0.5)

    def test_available(self) -> None:
        self.assertTrue(self.embedder.available)
        self.assertEqual(self.embedder.name, "local-hash")

    def test_key_value_embedding_combines_fields(self) -> None:
        combined = self.embedder.embed_key_value("color", "violet", "user", "Preferences")
        self.assertNotEqual(combined, {})
        self.assertEqual(
            combined,
            self.embedder.embed_key_value("color", "violet", "user", "Preferences"),
        )


class OllamaSemanticEmbedderFallbackTests(unittest.TestCase):
    def test_unreachable_endpoint_degrades_to_local(self) -> None:
        embedder = OllamaSemanticEmbedder(base_url="http://127.0.0.1:59999", timeout=0.5)
        self.assertFalse(embedder.available)
        self.assertEqual(embedder.embed_text("favorite color"), embed_text("favorite color"))
        self.assertEqual(
            embedder.embed_key_value("color", "violet"),
            embed_text("color violet"),
        )


if __name__ == "__main__":
    unittest.main()
