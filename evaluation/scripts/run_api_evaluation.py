#!/usr/bin/env python3
"""Run CodeCompass evaluation questions through the live HTTP API."""

from __future__ import annotations

import argparse
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import closing
from io import BytesIO
import json
import logging
from pathlib import Path
from pathlib import PurePosixPath
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import uuid
import zipfile

VERSION = "1.0.0"
LOGGER = logging.getLogger("codecompass.run_api_evaluation")


class EvaluationError(RuntimeError):
    """Raised for actionable evaluation configuration or API failures."""


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


def validate_api_base_url(value: str) -> str:
    """Validate and normalize an API base URL."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EvaluationError(
            "--api-base-url must be an absolute http(s) URL, "
            "for example http://localhost:8000/api/v1"
        )
    if parsed.query or parsed.fragment:
        raise EvaluationError("--api-base-url must not contain a query or fragment")
    return value.rstrip("/") + "/"


def load_questions(path: Path) -> list[dict[str, Any]]:
    """Load the ordered question list with fields required by the runner."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise EvaluationError(f"questions file does not exist: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise EvaluationError(f"cannot read questions file {path}: {error}") from error
    questions = document.get("questions") if isinstance(document, dict) else None
    if not isinstance(questions, list) or not questions:
        raise EvaluationError(f"{path}: questions must be a non-empty list")
    seen: set[str] = set()
    for question in questions:
        if not isinstance(question, dict):
            raise EvaluationError(f"{path}: every question must be an object")
        question_id = question.get("id")
        fixture = question.get("fixture")
        text = question.get("question")
        if not isinstance(question_id, str) or not question_id:
            raise EvaluationError(f"{path}: every question needs a non-empty string id")
        if question_id in seen:
            raise EvaluationError(f"{path}: duplicate question id {question_id}")
        seen.add(question_id)
        if not isinstance(fixture, str) or not fixture:
            raise EvaluationError(f"{question_id}: fixture must be a non-empty string")
        fixture_path = PurePosixPath(fixture)
        if (
            fixture_path.is_absolute()
            or len(fixture_path.parts) != 1
            or fixture_path.name in {".", ".."}
        ):
            raise EvaluationError(f"{question_id}: fixture must be a safe directory name")
        if not isinstance(text, str) or not text.strip():
            raise EvaluationError(f"{question_id}: question must be non-empty")
    return questions


def build_fixture_zip(fixture_dir: Path) -> bytes:
    """Build a deterministic in-memory ZIP for one fixture directory."""
    if not fixture_dir.is_dir():
        raise EvaluationError(f"fixture directory does not exist: {fixture_dir}")
    files = sorted(path for path in fixture_dir.rglob("*") if path.is_file())
    if not files:
        raise EvaluationError(f"fixture contains no files: {fixture_dir}")
    buffer = BytesIO()
    with zipfile.ZipFile(
        buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in files:
            if path.is_symlink():
                raise EvaluationError(f"fixture must not contain symlinks: {path}")
            relative = path.relative_to(fixture_dir).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            try:
                content = path.read_bytes()
            except OSError as error:
                raise EvaluationError(f"cannot read fixture file {path}: {error}") from error
            archive.writestr(info, content, compresslevel=9)
    return buffer.getvalue()


def _quoted_multipart_value(value: str) -> str:
    """Escape a value used in a multipart Content-Disposition parameter."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "").replace("\n", "")


def build_multipart(
    fields: dict[str, str],
    file_field: str,
    file_name: str,
    file_content: bytes,
    boundary: str | None = None,
) -> tuple[bytes, str]:
    """Build a multipart/form-data request body using only the standard library."""
    resolved_boundary = boundary or f"codecompass-{uuid.uuid4().hex}"
    if any(character in resolved_boundary for character in '\r\n"'):
        raise EvaluationError("multipart boundary contains invalid characters")
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{resolved_boundary}\r\n".encode(),
                (
                    "Content-Disposition: form-data; "
                    f'name="{_quoted_multipart_value(name)}"\r\n\r\n'
                ).encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{resolved_boundary}\r\n".encode(),
            (
                "Content-Disposition: form-data; "
                f'name="{_quoted_multipart_value(file_field)}"; '
                f'filename="{_quoted_multipart_value(file_name)}"\r\n'
            ).encode(),
            b"Content-Type: application/zip\r\n\r\n",
            file_content,
            b"\r\n",
            f"--{resolved_boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={resolved_boundary}"


Transport = Callable[[Request, float], Any]


class ApiClient:
    """Small JSON HTTP client with injectable transport for focused tests."""

    def __init__(
        self,
        api_base_url: str,
        request_timeout: float,
        transport: Transport | None = None,
    ) -> None:
        self.api_base_url = validate_api_base_url(api_base_url)
        self.request_timeout = request_timeout
        self.transport = transport or self._urlopen

    @staticmethod
    def _urlopen(request: Request, timeout: float) -> Any:
        return urlopen(request, timeout=timeout)  # noqa: S310 - URL is explicit operator input

    def request_json(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Send one request and require a JSON object response."""
        url = urljoin(self.api_base_url, path.lstrip("/"))
        request_headers = {"Accept": "application/json", **(headers or {})}
        request = Request(url, data=body, headers=request_headers, method=method)
        try:
            response = self.transport(request, self.request_timeout)
            with closing(response):
                payload = response.read()
        except HTTPError as error:
            try:
                detail_body = error.read().decode("utf-8", errors="replace")
                parsed = json.loads(detail_body)
                detail = parsed.get("detail", detail_body) if isinstance(parsed, dict) else detail_body
            except (OSError, json.JSONDecodeError):
                detail = error.reason
            raise EvaluationError(
                f"{method} {url} failed with HTTP {error.code}: {str(detail)[:300]}"
            ) from error
        except (TimeoutError, URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise EvaluationError(
                f"cannot reach CodeCompass at {url}: {reason}; "
                "check docker compose and --api-base-url"
            ) from error
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise EvaluationError(f"{method} {url} returned invalid JSON") from error
        if not isinstance(value, dict):
            raise EvaluationError(f"{method} {url} returned a non-object JSON response")
        return value

    def upload_fixture(self, fixture: str, archive: bytes) -> tuple[str, str]:
        """Upload one fixture and return repository and job IDs."""
        body, content_type = build_multipart(
            {"display_name": f"evaluation-{fixture}"},
            "file",
            f"{fixture}.zip",
            archive,
        )
        response = self.request_json(
            "POST",
            "repositories",
            body=body,
            headers={
                "Content-Type": content_type,
                "Idempotency-Key": f"evaluation-{fixture}-{uuid.uuid4()}",
            },
        )
        repository = response.get("repository")
        job = response.get("job")
        repository_id = repository.get("id") if isinstance(repository, dict) else None
        job_id = job.get("id") if isinstance(job, dict) else None
        if not isinstance(repository_id, str) or not isinstance(job_id, str):
            raise EvaluationError("upload response is missing repository.id or job.id")
        return repository_id, job_id


def poll_status(
    client: ApiClient,
    path: str,
    *,
    success: set[str],
    failure: set[str],
    timeout: float,
    interval: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll a status resource until success, failure, or timeout."""
    deadline = clock() + timeout
    last_status = "unknown"
    while True:
        response = client.request_json("GET", path)
        status = response.get("status")
        if not isinstance(status, str):
            raise EvaluationError(f"{path} response is missing string status")
        last_status = status
        if status in success:
            return response
        if status in failure:
            detail = response.get("error") or response.get("phase") or "no detail"
            raise EvaluationError(f"{path} ended in {status}: {detail}")
        if clock() >= deadline:
            raise EvaluationError(
                f"timed out after {timeout:g}s waiting for {path}; last status={last_status}"
            )
        sleep(interval)


def _required_span(item: dict[str, Any], label: str) -> dict[str, Any]:
    """Map and validate one API citation/retrieval span."""
    path = item.get("path")
    start = item.get("start_line")
    end = item.get("end_line")
    if not isinstance(path, str) or not isinstance(start, int) or not isinstance(end, int):
        raise EvaluationError(f"{label} is missing path/start_line/end_line")
    if start < 1 or end < start:
        raise EvaluationError(f"{label} has an invalid line span {start}-{end}")
    return {"path": path, "start_line": start, "end_line": end}


def map_prediction(
    question: dict[str, Any],
    answer: dict[str, Any],
    trace: dict[str, Any],
    request_latency_ms: int,
    model: dict[str, str],
    config_id: str,
    config_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Map API answer and trace responses to the score_results contract."""
    outcome = answer.get("outcome")
    if outcome not in {"answered", "insufficient_evidence"}:
        raise EvaluationError(f"{question['id']}: unsupported answer outcome {outcome!r}")
    citations_value = answer.get("citations")
    retrieved_value = trace.get("retrieved")
    if not isinstance(citations_value, list) or not isinstance(retrieved_value, list):
        raise EvaluationError(f"{question['id']}: answer/trace spans must be lists")
    citations: list[dict[str, Any]] = []
    for index, item in enumerate(citations_value, start=1):
        if not isinstance(item, dict):
            raise EvaluationError(f"{question['id']}: citation {index} must be an object")
        citations.append(_required_span(item, f"{question['id']} citation {index}"))
    retrieved: list[dict[str, Any]] = []
    for rank, item in enumerate(retrieved_value, start=1):
        if not isinstance(item, dict):
            raise EvaluationError(f"{question['id']}: retrieved item {rank} must be an object")
        mapped = _required_span(item, f"{question['id']} retrieved item {rank}")
        score = item.get("score")
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise EvaluationError(f"{question['id']}: retrieved item {rank} has no numeric score")
        retrieved.append({**mapped, "rank": rank, "score": float(score)})
    trace_latency = trace.get("duration_ms")
    latency_ms = request_latency_ms
    if isinstance(trace_latency, (int, float)) and not isinstance(trace_latency, bool):
        if trace_latency < 0:
            raise EvaluationError(f"{question['id']}: trace duration_ms must be non-negative")
        latency_ms = int(trace_latency)
    answer_text = answer.get("answer") if outcome == "answered" else answer.get("message")
    if not isinstance(answer_text, str):
        answer_text = ""
    return {
        "question_id": question["id"],
        "answer": answer_text,
        "abstained": outcome == "insufficient_evidence",
        "citations": citations,
        "retrieved": retrieved,
        "latency_ms": latency_ms,
        "model": model,
        "config_id": config_id,
        "config_metadata": config_metadata,
        "trace_id": answer.get("trace_id"),
    }


def delete_repositories(
    client: ApiClient,
    repository_ids: Iterable[str],
    timeout: float,
    interval: float,
) -> None:
    """Request and verify durable purge for uploaded repositories."""
    for repository_id in repository_ids:
        purge = client.request_json("DELETE", f"repositories/{repository_id}")
        purge_id = purge.get("id")
        if not isinstance(purge_id, str):
            raise EvaluationError(f"delete response for {repository_id} is missing purge id")
        poll_status(
            client,
            f"purges/{purge_id}",
            success={"completed"},
            failure={"failed"},
            timeout=timeout,
            interval=interval,
        )
        LOGGER.info("Purged evaluation repository fixture_repository_id=%s", repository_id)


def run_evaluation(
    client: ApiClient,
    questions: list[dict[str, Any]],
    fixtures_root: Path,
    poll_timeout: float,
    poll_interval: float,
    model: dict[str, str],
    config_id: str,
    config_metadata: dict[str, Any],
    delete_after_run: bool,
) -> list[dict[str, Any]]:
    """Upload each fixture once, ask all questions, and return predictions."""
    questions_by_fixture: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for question in questions:
        questions_by_fixture[str(question["fixture"])].append(question)

    repositories: dict[str, str] = {}
    predictions: list[dict[str, Any]] = []
    primary_error: Exception | None = None
    try:
        for fixture in questions_by_fixture:
            archive = build_fixture_zip(fixtures_root / fixture)
            LOGGER.info("Uploading fixture=%s bytes=%d", fixture, len(archive))
            repository_id, job_id = client.upload_fixture(fixture, archive)
            repositories[fixture] = repository_id
            poll_status(
                client,
                f"jobs/{job_id}",
                success={"ready"},
                failure={"failed", "cancelled"},
                timeout=poll_timeout,
                interval=poll_interval,
            )
            LOGGER.info("Fixture ready fixture=%s repository_id=%s", fixture, repository_id)

        for question in questions:
            repository_id = repositories[str(question["fixture"])]
            started = time.monotonic()
            answer = client.request_json(
                "POST",
                f"repositories/{repository_id}/questions",
                body=json.dumps({"question": question["question"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            request_latency_ms = int((time.monotonic() - started) * 1000)
            trace_id = answer.get("trace_id")
            if not isinstance(trace_id, str):
                raise EvaluationError(f"{question['id']}: answer response is missing trace_id")
            trace = client.request_json("GET", f"traces/{trace_id}")
            predictions.append(
                map_prediction(
                    question,
                    answer,
                    trace,
                    request_latency_ms,
                    model,
                    config_id,
                    config_metadata,
                )
            )
            LOGGER.info(
                "Evaluated question_id=%s outcome=%s",
                question["id"],
                answer.get("outcome"),
            )
    except Exception as error:
        primary_error = error
    finally:
        if delete_after_run and repositories:
            try:
                delete_repositories(
                    client, repositories.values(), poll_timeout, poll_interval
                )
            except Exception as cleanup_error:
                if primary_error:
                    LOGGER.error("Cleanup also failed: %s", cleanup_error)
                else:
                    primary_error = cleanup_error
    if primary_error:
        raise primary_error
    return predictions


def write_predictions(path: Path, predictions: list[dict[str, Any]]) -> None:
    """Atomically write prediction JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n" for item in predictions),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise EvaluationError(f"cannot write predictions to {path}: {error}") from error


def parse_config_metadata(value: str) -> dict[str, Any]:
    """Parse a JSON object supplied on the command line."""
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError(
            f"--config-metadata must be valid JSON: {error}"
        ) from error
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("--config-metadata must be a JSON object")
    return parsed


def positive_float(value: str) -> float:
    """Parse a strictly positive float for argparse."""
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def nonnegative_float(value: str) -> float:
    """Parse a non-negative float for argparse."""
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def nonempty_text(value: str) -> str:
    """Require a non-empty command-line metadata value."""
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be empty")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Run a CodeCompass question set through the live API.",
        epilog=(
            "Example: python3 evaluation/scripts/run_api_evaluation.py "
            "--api-base-url http://localhost:8000/api/v1 "
            "--questions evaluation/datasets/dev_questions.json "
            "--fixtures-root evaluation/fixtures "
            "--output evaluation/reports/dev_predictions.jsonl "
            "--log-file evaluation/reports/api-evaluation.log "
            "--model-name qwen2.5-coder:3b --model-digest <digest> "
            "--config-id mvp-hybrid-baseline"
        ),
    )
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--questions", type=Path, required=True)
    parser.add_argument("--fixtures-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log-file", type=Path, required=True)
    parser.add_argument("--poll-timeout", type=positive_float, default=300.0)
    parser.add_argument("--poll-interval", type=nonnegative_float, default=1.0)
    parser.add_argument("--request-timeout", type=positive_float, default=120.0)
    parser.add_argument("--model-name", type=nonempty_text, required=True)
    parser.add_argument("--model-digest", type=nonempty_text, required=True)
    parser.add_argument("--config-id", type=nonempty_text, required=True)
    parser.add_argument(
        "--config-metadata",
        type=parse_config_metadata,
        default={},
        help="JSON object recorded on every prediction.",
    )
    parser.add_argument(
        "--delete-after-run",
        action="store_true",
        help="Durably purge uploaded fixture repositories after evaluation.",
    )
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main() -> int:
    """Run the API evaluation and return a process exit code."""
    args = build_parser().parse_args()
    configure_logging(args.log_file, args.log_level)
    try:
        client = ApiClient(args.api_base_url, args.request_timeout)
        questions = load_questions(args.questions.resolve())
        predictions = run_evaluation(
            client,
            questions,
            args.fixtures_root.resolve(),
            args.poll_timeout,
            args.poll_interval,
            {"name": args.model_name, "digest": args.model_digest},
            args.config_id,
            args.config_metadata,
            args.delete_after_run,
        )
        write_predictions(args.output.resolve(), predictions)
    except (EvaluationError, OSError) as error:
        LOGGER.error("Evaluation failed: %s", error)
        return 2
    LOGGER.info("Wrote %d predictions to %s", len(predictions), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
