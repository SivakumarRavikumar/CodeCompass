"""Focused tests for the dependency-free evaluation harness."""

from __future__ import annotations

import sys
from pathlib import Path
import unittest

EVALUATION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVALUATION_ROOT / "scripts"))

import score_results  # noqa: E402
import validate_assets  # noqa: E402


class AssetValidationTests(unittest.TestCase):
    def test_repository_assets_validate(self) -> None:
        fixture_count, question_count = validate_assets.validate_assets(EVALUATION_ROOT)
        self.assertEqual(fixture_count, 2)
        self.assertEqual(question_count, 12)

    def test_unsafe_relative_path_is_rejected(self) -> None:
        with self.assertRaises(validate_assets.ValidationError):
            validate_assets.safe_relative_path("../secret.txt")


class MetricsTests(unittest.TestCase):
    def test_span_overlap_requires_path_and_line_intersection(self) -> None:
        expected = {"path": "pkg/module.py", "line_span": [10, 20]}
        self.assertTrue(
            score_results.overlaps(
                {"path": "pkg/module.py", "start_line": 20, "end_line": 25},
                expected,
            )
        )
        self.assertFalse(
            score_results.overlaps(
                {"path": "other.py", "start_line": 10, "end_line": 20},
                expected,
            )
        )

    def test_metrics_reward_supported_answer_and_correct_abstention(self) -> None:
        questions = {
            "q1": {
                "answerable": True,
                "category": "behavior",
                "expected_evidence": [{"path": "a.py", "line_span": [3, 5]}],
            },
            "q2": {
                "answerable": False,
                "category": "unanswerable",
                "expected_evidence": [],
            },
        }
        predictions = {
            "q1": {
                "abstained": False,
                "answer": "Supported.",
                "citations": [{"path": "a.py", "start_line": 4, "end_line": 4}],
                "retrieved": [{"path": "a.py", "start_line": 1, "end_line": 6}],
                "latency_ms": 10,
            },
            "q2": {
                "abstained": True,
                "answer": "Insufficient evidence.",
                "citations": [],
                "retrieved": [],
                "latency_ms": 20,
            },
        }
        metrics = score_results.compute_metrics(questions, predictions, retrieval_k=5)
        self.assertEqual(metrics["retrieval_hit_at_k"], 1.0)
        self.assertEqual(metrics["citation_precision"], 1.0)
        self.assertEqual(metrics["answerability_accuracy"], 1.0)
        self.assertEqual(metrics["unanswerable_abstention_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
