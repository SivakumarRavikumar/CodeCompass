#!/usr/bin/env python3
"""Score CodeCompass prediction JSONL against an evaluation question set."""

from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path, PurePosixPath
import sys
from typing import Any

VERSION = "1.0.0"
LOGGER = logging.getLogger("codecompass.score_results")


class ScoringError(ValueError):
    """Raised when questions or predictions violate the scoring contract."""


def configure_logging(log_file: Path, level: str) -> None:
    """Configure timestamped console and plain-text file logs."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=getattr(logging, level), handlers=handlers, force=True)


def load_questions(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Load and index a validated question set."""
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ScoringError(f"cannot load questions from {path}: {error}") from error
    questions = dataset.get("questions") if isinstance(dataset, dict) else None
    if not isinstance(questions, list) or not questions:
        raise ScoringError(f"{path}: questions must be a non-empty list")
    indexed: dict[str, dict[str, Any]] = {}
    for question in questions:
        if not isinstance(question, dict) or not isinstance(question.get("id"), str):
            raise ScoringError(f"{path}: every question needs a string id")
        if question["id"] in indexed:
            raise ScoringError(f"{path}: duplicate question id {question['id']}")
        indexed[question["id"]] = question
    return indexed, dataset


def load_predictions(path: Path) -> dict[str, dict[str, Any]]:
    """Load JSONL predictions and reject duplicate IDs."""
    predictions: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ScoringError(f"cannot load predictions from {path}: {error}") from error
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            prediction = json.loads(line)
        except json.JSONDecodeError as error:
            raise ScoringError(f"{path}:{line_number}: invalid JSON: {error}") from error
        if not isinstance(prediction, dict):
            raise ScoringError(f"{path}:{line_number}: prediction must be an object")
        question_id = prediction.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            raise ScoringError(f"{path}:{line_number}: question_id is required")
        if question_id in predictions:
            raise ScoringError(f"{path}:{line_number}: duplicate question_id {question_id}")
        predictions[question_id] = prediction
    return predictions


def validate_span(item: dict[str, Any], label: str) -> None:
    """Validate a citation or retrieval span."""
    path = item.get("path")
    if not isinstance(path, str):
        raise ScoringError(f"{label}: path must be a string")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise ScoringError(f"{label}: path must be safe and repository-relative")
    start, end = item.get("start_line"), item.get("end_line")
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        raise ScoringError(f"{label}: invalid inclusive line span {start!r}-{end!r}")


def validate_predictions(
    questions: dict[str, dict[str, Any]], predictions: dict[str, dict[str, Any]]
) -> None:
    """Validate prediction coverage and required fields."""
    missing = sorted(set(questions) - set(predictions))
    unknown = sorted(set(predictions) - set(questions))
    if missing or unknown:
        raise ScoringError(f"prediction ID mismatch; missing={missing}, unknown={unknown}")
    for question_id, prediction in predictions.items():
        if not isinstance(prediction.get("abstained"), bool):
            raise ScoringError(f"{question_id}: abstained must be boolean")
        if not isinstance(prediction.get("answer"), str):
            raise ScoringError(f"{question_id}: answer must be a string")
        for field in ("citations", "retrieved"):
            spans = prediction.get(field)
            if not isinstance(spans, list):
                raise ScoringError(f"{question_id}: {field} must be a list")
            for index, span in enumerate(spans):
                if not isinstance(span, dict):
                    raise ScoringError(f"{question_id}: {field}[{index}] must be an object")
                validate_span(span, f"{question_id}: {field}[{index}]")
        latency = prediction.get("latency_ms")
        if latency is not None and (
            not isinstance(latency, (int, float)) or isinstance(latency, bool) or latency < 0
        ):
            raise ScoringError(f"{question_id}: latency_ms must be non-negative")


def overlaps(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Return whether an actual span overlaps expected evidence."""
    expected_start, expected_end = expected["line_span"]
    return (
        actual["path"] == expected["path"]
        and actual["start_line"] <= expected_end
        and actual["end_line"] >= expected_start
    )


def matches_any(actual: dict[str, Any], expected: list[dict[str, Any]]) -> bool:
    """Return whether an actual span overlaps any expected span."""
    return any(overlaps(actual, item) for item in expected)


def safe_rate(numerator: int, denominator: int) -> float | None:
    """Return a rate, or null when no examples define it."""
    return round(numerator / denominator, 6) if denominator else None


def percentile(values: list[float], fraction: float) -> float | None:
    """Compute a nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return round(ordered[rank - 1], 3)


def compute_metrics(
    questions: dict[str, dict[str, Any]],
    predictions: dict[str, dict[str, Any]],
    retrieval_k: int,
) -> dict[str, Any]:
    """Compute retrieval, citation, and abstention metrics."""
    answerable_total = 0
    unanswerable_total = 0
    retrieval_hits = 0
    answered_answerable = 0
    selective_hits = 0
    correct_answerability = 0
    correct_abstentions = 0
    matching_citations = 0
    citation_total = 0
    supported_answers = 0
    latencies: list[float] = []
    categories: dict[str, dict[str, int]] = {}

    for question_id, question in questions.items():
        prediction = predictions[question_id]
        answerable = question["answerable"]
        abstained = prediction["abstained"]
        expected = question["expected_evidence"]
        category = question["category"]
        bucket = categories.setdefault(category, {"total": 0, "correct_answerability": 0})
        bucket["total"] += 1
        decision_correct = answerable != abstained
        correct_answerability += int(decision_correct)
        bucket["correct_answerability"] += int(decision_correct)
        if prediction.get("latency_ms") is not None:
            latencies.append(float(prediction["latency_ms"]))

        retrieved = prediction["retrieved"][:retrieval_k]
        retrieval_hit = any(matches_any(span, expected) for span in retrieved)
        citations = prediction["citations"]
        citation_total += len(citations)
        matching_citations += sum(matches_any(span, expected) for span in citations)

        if answerable:
            answerable_total += 1
            retrieval_hits += int(retrieval_hit)
            if not abstained:
                answered_answerable += 1
                selective_hits += int(retrieval_hit)
                supported_answers += int(any(matches_any(span, expected) for span in citations))
        else:
            unanswerable_total += 1
            correct_abstentions += int(abstained)

    return {
        "question_count": len(questions),
        "retrieval_k": retrieval_k,
        "retrieval_hit_at_k": safe_rate(retrieval_hits, answerable_total),
        "citation_precision": safe_rate(matching_citations, citation_total),
        "citation_coverage": safe_rate(supported_answers, answered_answerable),
        "answerability_accuracy": safe_rate(correct_answerability, len(questions)),
        "unanswerable_abstention_rate": safe_rate(
            correct_abstentions, unanswerable_total
        ),
        "answerable_coverage": safe_rate(answered_answerable, answerable_total),
        "selective_retrieval_accuracy": safe_rate(
            selective_hits, answered_answerable
        ),
        "counts": {
            "answerable": answerable_total,
            "unanswerable": unanswerable_total,
            "answered_answerable": answered_answerable,
            "matching_citations": matching_citations,
            "citations": citation_total,
        },
        "latency_ms": {
            "samples": len(latencies),
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
        },
        "by_category": {
            name: {
                **counts,
                "answerability_accuracy": safe_rate(
                    counts["correct_answerability"], counts["total"]
                ),
            }
            for name, counts in sorted(categories.items())
        },
        "limitations": [
            "Span metrics do not judge prose factuality.",
            "Results apply only to the supplied question set and recorded configuration.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Score CodeCompass prediction JSONL.",
        epilog=(
            "Example: python3 evaluation/scripts/score_results.py "
            "--questions evaluation/datasets/dev_questions.json "
            "--predictions evaluation/reports/dev_predictions.jsonl "
            "--output evaluation/reports/dev_metrics.json "
            "--log-file evaluation/reports/score.log"
        ),
    )
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--retrieval-k", type=int, default=5)
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    """Score predictions and return a process exit code."""
    args = build_parser().parse_args()
    configure_logging(args.log_file, args.log_level)
    if args.retrieval_k < 1:
        LOGGER.error("--retrieval-k must be at least 1")
        return 2
    try:
        questions, dataset = load_questions(args.questions)
        predictions = load_predictions(args.predictions)
        validate_predictions(questions, predictions)
        metrics = compute_metrics(questions, predictions, args.retrieval_k)
        output = {
            "schema_version": 1,
            "dataset_version": dataset.get("dataset_version"),
            "split": dataset.get("split"),
            "source_questions": str(args.questions),
            "source_predictions": str(args.predictions),
            "metrics": metrics,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, ScoringError) as error:
        LOGGER.error("Scoring failed: %s", error)
        return 2
    LOGGER.info("Scored %d predictions; wrote %s", len(predictions), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
