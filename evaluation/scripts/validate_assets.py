#!/usr/bin/env python3
"""Validate CodeCompass fixture manifests and evaluation question sets."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
from pathlib import Path, PurePosixPath
import sys
from typing import Any

VERSION = "1.0.0"
LOGGER = logging.getLogger("codecompass.validate_assets")


class ValidationError(ValueError):
    """Raised when an evaluation asset violates its contract."""


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


def load_json(path: Path) -> dict[str, Any]:
    """Load a JSON object with a concise validation error."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValidationError(f"missing required file: {path}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValidationError(f"expected a JSON object in {path}")
    return value


def safe_relative_path(value: str) -> PurePosixPath:
    """Return a safe repository-relative POSIX path."""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValidationError(f"unsafe relative path: {value!r}")
    return path


def sha256_file(path: Path) -> str:
    """Compute a file's SHA-256 digest."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def python_symbols(path: Path) -> dict[str, tuple[int, int]]:
    """Collect qualified class/function symbols and inclusive source spans."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as error:
        raise ValidationError(f"cannot parse Python evidence file {path}: {error}") from error
    symbols: dict[str, tuple[int, int]] = {}

    def visit(nodes: list[ast.stmt], parents: tuple[str, ...] = ()) -> None:
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = ".".join((*parents, node.name))
                symbols[qualified] = (node.lineno, node.end_lineno or node.lineno)
                visit(node.body, (*parents, node.name))

    visit(tree.body)
    return symbols


def validate_manifest(fixture_dir: Path) -> set[str]:
    """Validate one fixture manifest and return its listed paths."""
    manifest_path = fixture_dir / "manifest.json"
    manifest = load_json(manifest_path)
    if manifest.get("fixture_id") != fixture_dir.name:
        raise ValidationError(f"{manifest_path}: fixture_id must match directory name")
    provenance = manifest.get("provenance", "")
    if "Synthetic local fixture" not in provenance or "not a public benchmark" not in provenance:
        raise ValidationError(f"{manifest_path}: provenance disclaimer is required")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValidationError(f"{manifest_path}: files must be a non-empty list")

    listed: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ValidationError(f"{manifest_path}: every file entry must be an object")
        relative = safe_relative_path(str(entry.get("path", "")))
        relative_text = relative.as_posix()
        if relative_text in listed:
            raise ValidationError(f"{manifest_path}: duplicate file {relative_text}")
        listed.add(relative_text)
        source_path = fixture_dir.joinpath(*relative.parts)
        if not source_path.is_file():
            raise ValidationError(f"{manifest_path}: listed file missing: {relative_text}")
        expected = entry.get("sha256")
        actual = sha256_file(source_path)
        if expected != actual:
            raise ValidationError(
                f"{manifest_path}: hash mismatch for {relative_text}; "
                f"expected {expected}, got {actual}"
            )

    actual_files = {
        path.relative_to(fixture_dir).as_posix()
        for path in fixture_dir.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if actual_files != listed:
        missing = sorted(actual_files - listed)
        stale = sorted(listed - actual_files)
        raise ValidationError(
            f"{manifest_path}: manifest coverage mismatch; unlisted={missing}, stale={stale}"
        )
    return listed


def validate_evidence(
    fixture_dir: Path, listed_paths: set[str], evidence: dict[str, Any], question_id: str
) -> None:
    """Validate one expected evidence reference."""
    relative = safe_relative_path(str(evidence.get("path", "")))
    relative_text = relative.as_posix()
    if relative_text not in listed_paths:
        raise ValidationError(f"{question_id}: evidence path is not manifested: {relative_text}")
    span = evidence.get("line_span")
    if (
        not isinstance(span, list)
        or len(span) != 2
        or not all(isinstance(value, int) for value in span)
        or span[0] < 1
        or span[1] < span[0]
    ):
        raise ValidationError(f"{question_id}: invalid inclusive line_span: {span!r}")
    source_path = fixture_dir.joinpath(*relative.parts)
    line_count = len(source_path.read_text(encoding="utf-8").splitlines())
    if span[1] > line_count:
        raise ValidationError(
            f"{question_id}: line_span ends at {span[1]}, but {relative_text} has {line_count} lines"
        )
    symbol = evidence.get("symbol")
    if symbol is not None:
        if source_path.suffix != ".py" or not isinstance(symbol, str):
            raise ValidationError(f"{question_id}: symbols require a Python evidence path")
        symbols = python_symbols(source_path)
        if symbol not in symbols:
            raise ValidationError(f"{question_id}: symbol {symbol!r} not found in {relative_text}")
        symbol_start, symbol_end = symbols[symbol]
        if span[1] < symbol_start or span[0] > symbol_end:
            raise ValidationError(
                f"{question_id}: evidence span does not overlap symbol {symbol}"
            )


def validate_dataset(
    dataset_path: Path, fixture_paths: dict[str, set[str]], fixtures_dir: Path
) -> set[str]:
    """Validate a question dataset and return question IDs."""
    dataset = load_json(dataset_path)
    split = dataset.get("split")
    questions = dataset.get("questions")
    if split not in {"development", "heldout"}:
        raise ValidationError(f"{dataset_path}: unsupported split {split!r}")
    if not isinstance(questions, list) or not questions:
        raise ValidationError(f"{dataset_path}: questions must be a non-empty list")
    identifiers: set[str] = set()
    allowed_categories = {
        "architecture", "behavior", "dependencies", "symbol_location", "unanswerable"
    }
    for question in questions:
        if not isinstance(question, dict):
            raise ValidationError(f"{dataset_path}: each question must be an object")
        question_id = question.get("id")
        if not isinstance(question_id, str) or not question_id:
            raise ValidationError(f"{dataset_path}: question id must be a non-empty string")
        if question_id in identifiers:
            raise ValidationError(f"{dataset_path}: duplicate question id {question_id}")
        identifiers.add(question_id)
        if question.get("split") != split:
            raise ValidationError(f"{question_id}: split must be {split}")
        fixture = question.get("fixture")
        if fixture not in fixture_paths:
            raise ValidationError(f"{question_id}: unknown fixture {fixture!r}")
        if question.get("category") not in allowed_categories:
            raise ValidationError(f"{question_id}: unsupported category")
        if not isinstance(question.get("question"), str) or not question["question"].strip():
            raise ValidationError(f"{question_id}: question text is required")
        if not isinstance(question.get("answerable"), bool):
            raise ValidationError(f"{question_id}: answerable must be boolean")
        evidence = question.get("expected_evidence")
        if not isinstance(evidence, list):
            raise ValidationError(f"{question_id}: expected_evidence must be a list")
        if question["answerable"] != bool(evidence):
            raise ValidationError(
                f"{question_id}: answerable must agree with presence of expected evidence"
            )
        for item in evidence:
            if not isinstance(item, dict):
                raise ValidationError(f"{question_id}: evidence entries must be objects")
            validate_evidence(fixtures_dir / fixture, fixture_paths[fixture], item, question_id)
    return identifiers


def validate_assets(evaluation_root: Path) -> tuple[int, int]:
    """Validate all fixture and dataset assets."""
    fixtures_dir = evaluation_root / "fixtures"
    fixture_dirs = sorted(
        path for path in fixtures_dir.iterdir() if path.is_dir()
    ) if fixtures_dir.is_dir() else []
    if len(fixture_dirs) < 2:
        raise ValidationError(f"expected at least two fixtures under {fixtures_dir}")
    fixture_paths = {path.name: validate_manifest(path) for path in fixture_dirs}
    development_ids = validate_dataset(
        evaluation_root / "datasets" / "dev_questions.json", fixture_paths, fixtures_dir
    )
    heldout_ids = validate_dataset(
        evaluation_root / "datasets" / "heldout_questions.json", fixture_paths, fixtures_dir
    )
    overlap = development_ids & heldout_ids
    if overlap:
        raise ValidationError(f"question IDs overlap between splits: {sorted(overlap)}")
    return len(fixture_paths), len(development_ids) + len(heldout_ids)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Validate CodeCompass evaluation fixtures and question datasets.",
        epilog=(
            "Example: python3 evaluation/scripts/validate_assets.py "
            "--evaluation-root evaluation --log-file evaluation/reports/validate.log"
        ),
    )
    parser.add_argument("--evaluation-root", type=Path, required=True)
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    """Run validation and return a process exit code."""
    args = build_parser().parse_args()
    configure_logging(args.log_file, args.log_level)
    try:
        fixture_count, question_count = validate_assets(args.evaluation_root.resolve())
    except (OSError, ValidationError) as error:
        LOGGER.error("Validation failed: %s", error)
        return 2
    LOGGER.info(
        "Validation passed for %d fixtures and %d questions",
        fixture_count,
        question_count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
