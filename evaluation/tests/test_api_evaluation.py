"""Focused tests for the live API evaluation runner."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import URLError
import zipfile
from io import BytesIO

EVALUATION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVALUATION_ROOT / "scripts"))

import run_api_evaluation as runner  # noqa: E402


class MultipartAndArchiveTests(unittest.TestCase):
    def test_multipart_contains_fields_file_and_closing_boundary(self) -> None:
        body, content_type = runner.build_multipart(
            {"display_name": "evaluation-atlas"},
            "file",
            "atlas.zip",
            b"ZIP-BYTES",
            boundary="fixed-boundary",
        )
        self.assertEqual(
            content_type, "multipart/form-data; boundary=fixed-boundary"
        )
        self.assertIn(b'name="display_name"', body)
        self.assertIn(b"evaluation-atlas", body)
        self.assertIn(b'name="file"; filename="atlas.zip"', body)
        self.assertIn(b"Content-Type: application/zip", body)
        self.assertTrue(body.endswith(b"--fixed-boundary--\r\n"))

    def test_fixture_zip_uses_repository_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package").mkdir()
            (root / "package" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            archive = runner.build_fixture_zip(root)
        with zipfile.ZipFile(BytesIO(archive)) as fixture_zip:
            self.assertEqual(fixture_zip.namelist(), ["package/module.py"])


class PollingAndMappingTests(unittest.TestCase):
    def test_polling_returns_ready_after_queued(self) -> None:
        class StubClient:
            responses = iter([{"status": "queued"}, {"status": "ready"}])

            def request_json(self, method: str, path: str) -> dict[str, object]:
                self.last_request = (method, path)
                return next(self.responses)

        clock_values = iter([0.0, 0.1])
        result = runner.poll_status(
            StubClient(),  # type: ignore[arg-type]
            "jobs/job-1",
            success={"ready"},
            failure={"failed"},
            timeout=1.0,
            interval=0.0,
            clock=lambda: next(clock_values),
            sleep=lambda _: None,
        )
        self.assertEqual(result["status"], "ready")

    def test_polling_surfaces_terminal_failure(self) -> None:
        class StubClient:
            def request_json(self, method: str, path: str) -> dict[str, object]:
                return {"status": "failed", "error": "embedding unavailable"}

        with self.assertRaisesRegex(runner.EvaluationError, "embedding unavailable"):
            runner.poll_status(
                StubClient(),  # type: ignore[arg-type]
                "jobs/job-1",
                success={"ready"},
                failure={"failed"},
                timeout=1.0,
                interval=0.0,
            )

    def test_polling_timeout_is_actionable(self) -> None:
        class StubClient:
            def request_json(self, method: str, path: str) -> dict[str, object]:
                return {"status": "processing"}

        clock_values = iter([0.0, 2.0])
        with self.assertRaisesRegex(runner.EvaluationError, "timed out after 1s"):
            runner.poll_status(
                StubClient(),  # type: ignore[arg-type]
                "jobs/job-1",
                success={"ready"},
                failure={"failed"},
                timeout=1.0,
                interval=0.0,
                clock=lambda: next(clock_values),
                sleep=lambda _: None,
            )

    def test_answer_and_trace_map_to_prediction_contract(self) -> None:
        prediction = runner.map_prediction(
            {"id": "dev-atlas-001"},
            {
                "outcome": "answered",
                "answer": "Supported [C1]",
                "citations": [
                    {
                        "path": "atlas_text/normalize.py",
                        "start_line": 4,
                        "end_line": 8,
                    }
                ],
                "trace_id": "trace-1",
            },
            {
                "duration_ms": 42,
                "retrieved": [
                    {
                        "path": "atlas_text/normalize.py",
                        "start_line": 4,
                        "end_line": 8,
                        "score": 0.02,
                    }
                ],
            },
            request_latency_ms=99,
            model={"name": "qwen", "digest": "sha256:test"},
            config_id="hybrid",
            config_metadata={"embedding": "all-MiniLM"},
        )
        self.assertFalse(prediction["abstained"])
        self.assertEqual(prediction["latency_ms"], 42)
        self.assertEqual(prediction["retrieved"][0]["rank"], 1)
        self.assertEqual(prediction["model"]["digest"], "sha256:test")


class RunnerFlowTests(unittest.TestCase):
    def test_fixture_is_uploaded_once_for_multiple_questions(self) -> None:
        class StubClient:
            def __init__(self) -> None:
                self.uploads: list[str] = []
                self.answer_count = 0

            def upload_fixture(self, fixture: str, archive: bytes) -> tuple[str, str]:
                self.uploads.append(fixture)
                self.assert_archive = bool(archive)
                return "repo-1", "job-1"

            def request_json(
                self,
                method: str,
                path: str,
                *,
                body: bytes | None = None,
                headers: dict[str, str] | None = None,
            ) -> dict[str, object]:
                del headers
                if path == "jobs/job-1":
                    return {"status": "ready"}
                if path.startswith("repositories/repo-1/questions"):
                    self.answer_count += 1
                    request = json.loads((body or b"{}").decode())
                    return {
                        "outcome": "insufficient_evidence",
                        "message": f"No evidence for {request['question']}",
                        "citations": [],
                        "trace_id": f"trace-{self.answer_count}",
                    }
                if path.startswith("traces/"):
                    return {"duration_ms": 5, "retrieved": []}
                raise AssertionError(f"unexpected request: {method} {path}")

        questions = [
            {"id": "q1", "fixture": "fixture", "question": "First?"},
            {"id": "q2", "fixture": "fixture", "question": "Second?"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture"
            fixture.mkdir()
            (fixture / "code.py").write_text("VALUE = 1\n", encoding="utf-8")
            client = StubClient()
            predictions = runner.run_evaluation(
                client,  # type: ignore[arg-type]
                questions,
                Path(directory),
                poll_timeout=1.0,
                poll_interval=0.0,
                model={"name": "model", "digest": "digest"},
                config_id="config",
                config_metadata={},
                delete_after_run=False,
            )
        self.assertEqual(client.uploads, ["fixture"])
        self.assertEqual(len(predictions), 2)
        self.assertTrue(all(item["abstained"] for item in predictions))

    def test_transport_failure_has_actionable_message(self) -> None:
        def failed_transport(request: object, timeout: float) -> object:
            del request, timeout
            raise URLError("connection refused")

        client = runner.ApiClient(
            "http://localhost:8000/api/v1",
            request_timeout=1.0,
            transport=failed_transport,  # type: ignore[arg-type]
        )
        with self.assertRaisesRegex(runner.EvaluationError, "check docker compose"):
            client.request_json("GET", "health")


if __name__ == "__main__":
    unittest.main()
