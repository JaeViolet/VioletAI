"""Semantic embedding abstraction for VioletAI Memory V2.

Retrieval depends on a ``SemanticEmbedder`` interface instead of concrete
embedding code so the implementation can be swapped (e.g. remote Ollama
embeddings) without touching ranking logic.

Embeddings are represented as ``dict[str, float]`` feature vectors. The local
implementation uses sparse string n-gram features; remote dense vectors are
represented with integer-index keys. Cosine similarity over these dicts is the
usual sparse cosine, so both representations rank identically through the same
code path as long as a single embedder is used for both query and record.

The default is a purely local, deterministic character n-gram hash embedder
that needs no network and is fully testable. Remote embedders degrade to this
local fallback whenever the remote service is unreachable or malformed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol

from memory_v2.embeddings import cosine_similarity as _cosine_similarity
from memory_v2.embeddings import embed_key_value as _embed_key_value
from memory_v2.embeddings import embed_text as _embed_text
from memory_v2.normalize import canonical_text


class SemanticEmbedder(Protocol):
    """Embedding contract used by the retriever."""

    name: str

    def embed_text(self, text: str) -> dict[str, float]:
        ...

    def embed_key_value(
        self, key: str, value: str, subject: str = "", category: str = ""
    ) -> dict[str, float]:
        ...

    def cosine_similarity(self, left: dict[str, float], right: dict[str, float]) -> float:
        ...

    @property
    def available(self) -> bool:
        ...


class LocalHashSemanticEmbedder:
    """Deterministic local n-gram hash embedding; the offline fallback."""

    name = "local-hash"

    def embed_text(self, text: str) -> dict[str, float]:
        return _embed_text(text)

    def embed_key_value(
        self, key: str, value: str, subject: str = "", category: str = ""
    ) -> dict[str, float]:
        return _embed_key_value(key, value, subject, category)

    def cosine_similarity(self, left: dict[str, float], right: dict[str, float]) -> float:
        return _cosine_similarity(left, right)

    @property
    def available(self) -> bool:
        return True


class OllamaSemanticEmbedder:
    """Optional remote embeddings via Ollama's ``/api/embed`` endpoint.

    Any failure (network, timeout, malformed response) silently degrades to the
    local deterministic embedder so retrieval remains deterministic and safe.
    Dense vectors are returned with integer-index keys, so cosine similarity
    over them is the standard dense cosine.
    """

    name = "ollama"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen3.5:9b",
        timeout: float = 5.0,
    ) -> None:
        self.base_url = (base_url or "http://127.0.0.1:11434").rstrip("/")
        self.model = model or "qwen3.5:9b"
        self.timeout = timeout
        self._fallback = LocalHashSemanticEmbedder()
        self._healthy: bool | None = None

    @property
    def available(self) -> bool:
        if self._healthy is None:
            self._healthy = self._probe()
        return self._healthy

    def _probe(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=self.timeout) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def _fetch(self, text: str) -> dict[str, float] | None:
        payload = json.dumps({"model": self.model, "input": text}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        raw = data.get("embeddings")
        if isinstance(raw, list) and raw and isinstance(raw[0], list):
            return {str(index): float(value) for index, value in enumerate(raw[0])}
        if isinstance(data.get("embedding"), list):
            return {str(index): float(value) for index, value in enumerate(data["embedding"])}
        return None

    def _embed_or_fallback(self, text: str) -> dict[str, float]:
        try:
            vector = self._fetch(text)
        except (
            urllib.error.URLError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ):
            vector = None
        return vector if vector else self._fallback.embed_text(text)

    def embed_text(self, text: str) -> dict[str, float]:
        return self._embed_or_fallback(text)

    def embed_key_value(
        self, key: str, value: str, subject: str = "", category: str = ""
    ) -> dict[str, float]:
        return self._embed_or_fallback(canonical_text(f"{category} {subject} {key} {value}"))

    def cosine_similarity(self, left: dict[str, float], right: dict[str, float]) -> float:
        return _cosine_similarity(left, right)
