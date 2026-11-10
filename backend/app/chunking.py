import ast
import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.config import Settings


@dataclass(frozen=True)
class Chunk:
    id: str
    path: str
    start_line: int
    end_line: int
    content: str
    symbol: str | None
    content_sha256: str


def expand_search_text(value: str) -> str:
    """Append identifier parts so FTS can match snake_case and CamelCase terms."""
    expanded: list[str] = []
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", value):
        snake_parts = [part for part in token.split("_") if part]
        for part in snake_parts:
            camel_parts = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", part).split()
            expanded.extend(camel_parts)
    return f"{value}\n{' '.join(expanded)}"


def _ranges_for_python(content: str) -> list[tuple[int, int, str | None]]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    ranges: list[tuple[int, int, str | None]] = []
    cursor = 1
    for node in tree.body:
        start = min(
            [node.lineno, *[decorator.lineno for decorator in getattr(node, "decorator_list", [])]]
        )
        end = getattr(node, "end_lineno", node.lineno)
        if start > cursor:
            ranges.append((cursor, start - 1, None))
        symbol = getattr(node, "name", None)
        ranges.append((start, end, symbol))
        cursor = end + 1
    total = len(content.splitlines())
    if cursor <= total:
        ranges.append((cursor, total, None))
    return ranges


def _split_range(
    start: int,
    end: int,
    symbol: str | None,
    settings: Settings,
) -> list[tuple[int, int, str | None]]:
    result = []
    current = start
    step = max(1, settings.chunk_max_lines - settings.chunk_overlap_lines)
    while current <= end:
        chunk_end = min(end, current + settings.chunk_max_lines - 1)
        result.append((current, chunk_end, symbol))
        if chunk_end == end:
            break
        current += step
    return result


def chunk_file(
    repository_id: str,
    snapshot_id: str,
    path: str,
    content: str,
    settings: Settings,
) -> list[Chunk]:
    lines = content.splitlines()
    if not lines:
        return []
    ranges = _ranges_for_python(content) if PurePosixPath(path).suffix == ".py" else []
    if not ranges:
        ranges = [(1, len(lines), None)]

    chunks: list[Chunk] = []
    for start, end, symbol in ranges:
        if start > end:
            continue
        for chunk_start, chunk_end, chunk_symbol in _split_range(start, end, symbol, settings):
            body = "\n".join(lines[chunk_start - 1 : chunk_end])
            if not body.strip():
                continue
            digest = hashlib.sha256(body.encode()).hexdigest()
            identity = f"{repository_id}:{snapshot_id}:{path}:{chunk_start}:{chunk_end}:{digest}"
            chunk_id = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
            chunks.append(
                Chunk(
                    id=chunk_id,
                    path=path,
                    start_line=chunk_start,
                    end_line=chunk_end,
                    content=body,
                    symbol=chunk_symbol,
                    content_sha256=digest,
                )
            )
    return chunks
