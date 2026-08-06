"""Ollama client worker and model discovery tests (no server required)."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import requests

from core.config import DEFAULT_MODEL_NAME
from models.manager import discover_models
from models.ollama import OllamaWorker


class OllamaWorkerTests(unittest.TestCase):
    @patch("models.ollama.requests.post")
    def test_combines_streamed_chunks(self, post: Mock) -> None:
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
        self.assertNotIn("think", post.call_args.kwargs["json"])
        response.close.assert_called_once()

    @patch("models.ollama.requests.post")
    def test_many_empty_chunks_then_valid_content(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = [
            "",
            '{"message":{"content":""},"done":false}',
            '{"message":{"content":"Visible"},"done":true}',
        ]
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        answers: list[str] = []
        worker.finished.connect(answers.append)
        worker.run()
        self.assertEqual(answers, ["Visible"])
        response.close.assert_called_once()

    @patch("models.ollama.requests.post")
    def test_http_200_empty_stream_reports_specific_failure(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = []
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        failures: list[str] = []
        worker.failed.connect(failures.append)
        worker.run()
        self.assertEqual(failures, ["Ollama returned no stream events before the response ended."])
        response.close.assert_called_once()

    @patch("models.ollama.requests.post")
    def test_done_event_with_no_content_reports_empty_visible_text(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = ['{"message":{"content":""},"done":true}']
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        failures: list[str] = []
        worker.failed.connect(failures.append)
        worker.run()
        self.assertEqual(
            failures,
            ["Ollama completed the stream before sending visible assistant text (events=1, empty_events=1, done=True)."],
        )
        response.close.assert_called_once()

    @patch("models.ollama.requests.post")
    def test_partial_content_followed_by_error_preserves_chunk_signal(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = [
            '{"message":{"content":"Hello"},"done":false}',
            '{"error":"stream broke"}',
        ]
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        chunks: list[str] = []
        failures: list[str] = []
        worker.chunk_received.connect(chunks.append)
        worker.failed.connect(failures.append)
        worker.run()
        self.assertEqual(chunks, ["Hello"])
        self.assertEqual(failures, ["stream broke"])
        response.close.assert_called_once()

    @patch("models.ollama.requests.post")
    def test_timeout_before_first_token_is_stage_specific(self, post: Mock) -> None:
        post.side_effect = requests.Timeout()
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME, read_timeout_seconds=1)
        failures: list[str] = []
        worker.failed.connect(failures.append)
        worker.run()
        self.assertEqual(failures, ["Ollama request timed out before first event after 1 seconds."])

    @patch("models.ollama.requests.post")
    def test_cancellation_closes_response(self, post: Mock) -> None:
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

    @patch("models.ollama.requests.post")
    def test_cancellation_after_partial_output(self, post: Mock) -> None:
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = [
            '{"message":{"content":"Hello"},"done":false}',
            '{"message":{"content":" there"},"done":true}',
        ]
        post.return_value = response
        worker = OllamaWorker([{"role": "user", "content": "Hi"}], DEFAULT_MODEL_NAME)
        chunks: list[str] = []
        cancelled: list[bool] = []
        finished: list[str] = []
        worker.chunk_received.connect(chunks.append)
        worker.chunk_received.connect(lambda _chunk: worker.cancel())
        worker.cancelled.connect(lambda: cancelled.append(True))
        worker.finished.connect(finished.append)
        worker.run()
        self.assertEqual(chunks, ["Hello"])
        self.assertEqual(cancelled, [True])
        self.assertEqual(finished, [])
        response.close.assert_called()


class ModelDiscoveryTests(unittest.TestCase):
    @patch("models.manager.requests.get")
    def test_reads_ollama_tags(self, get: Mock) -> None:
        response = Mock()
        response.json.return_value = {"models": [{"name": "a:1"}, {"name": "b:2"}]}
        get.return_value = response
        self.assertEqual(discover_models(), ["a:1", "b:2"])


if __name__ == "__main__":
    unittest.main()
