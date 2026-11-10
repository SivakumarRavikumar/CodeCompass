import ast
import re
import stat
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

from app.config import Settings

SECRET_PATTERNS = [
    re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
        r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        re.DOTALL,
    ),
    re.compile(
        r"(?im)^(\s*(?:api[_-]?key|secret|token|password|passwd|client_secret)"
        r"\s*[:=]\s*)([^\s#]+)"
    ),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
]

REDACTION = "[REDACTED]"
SENSITIVE_NAMES = {
    "api_key",
    "apikey",
    "client_secret",
    "password",
    "passwd",
    "secret",
    "token",
}


@dataclass(frozen=True)
class SafeFile:
    path: str
    content: str


class UnsafeArchive(ValueError):
    pass


def _python_redaction_spans(content: str) -> list[tuple[int, int]]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    lines = content.splitlines(keepends=True)
    starts: list[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)

    def absolute_position(line_number: int, byte_column: int) -> int:
        line = lines[line_number - 1]
        character_column = len(line.encode()[:byte_column].decode())
        return starts[line_number - 1] + character_column

    def span(node: ast.AST) -> tuple[int, int] | None:
        if not all(
            hasattr(node, attribute)
            for attribute in ("lineno", "col_offset", "end_lineno", "end_col_offset")
        ):
            return None
        return (
            absolute_position(node.lineno, node.col_offset),  # type: ignore[attr-defined]
            absolute_position(node.end_lineno, node.end_col_offset),  # type: ignore[attr-defined]
        )

    def target_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id.lower()
        if isinstance(node, ast.Attribute):
            return node.attr.lower()
        return None

    spans: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        value: ast.AST | None = None
        sensitive = False
        if isinstance(node, ast.Assign):
            sensitive = any(target_name(target) in SENSITIVE_NAMES for target in node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            sensitive = target_name(node.target) in SENSITIVE_NAMES
            value = node.value
        elif isinstance(node, ast.keyword):
            sensitive = bool(node.arg and node.arg.lower() in SENSITIVE_NAMES)
            value = node.value
        if sensitive and value is not None:
            value_span = span(value)
            if value_span:
                spans.add(value_span)
        if isinstance(node, ast.Dict):
            for key, value_node in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value.lower() in SENSITIVE_NAMES
                ):
                    value_span = span(value_node)
                    if value_span:
                        spans.add(value_span)
    outermost: list[tuple[int, int]] = []
    for candidate in sorted(spans, key=lambda item: (item[0], -item[1])):
        if any(
            candidate[0] >= existing[0] and candidate[1] <= existing[1]
            for existing in outermost
        ):
            continue
        outermost.append(candidate)
    return sorted(outermost, reverse=True)


def redact_secrets(content: str, *, preserve_python_syntax: bool = False) -> str:
    if preserve_python_syntax:
        spans = _python_redaction_spans(content)
        if spans:
            redacted = content
            for start, end in spans:
                line_breaks = redacted[start:end].count("\n")
                replacement = f'"{REDACTION}"' + ("\n" * line_breaks)
                redacted = redacted[:start] + replacement + redacted[end:]
            for pattern in [SECRET_PATTERNS[0], *SECRET_PATTERNS[2:]]:
                redacted = pattern.sub(
                    lambda match: REDACTION + ("\n" * match.group(0).count("\n")),
                    redacted,
                )
            return redacted

    redacted = SECRET_PATTERNS[0].sub(
        lambda match: REDACTION + ("\n" * match.group(0).count("\n")),
        content,
    )

    def redact_assignment(match: re.Match[str]) -> str:
        value = match.group(2)
        suffix_match = re.search(r"[,;)\]}]+$", value)
        suffix = suffix_match.group(0) if suffix_match else ""
        return f'{match.group(1)}"{REDACTION}"{suffix}'

    for pattern in SECRET_PATTERNS[1:2]:
        redacted = pattern.sub(redact_assignment, redacted)
    for pattern in SECRET_PATTERNS[2:]:
        redacted = pattern.sub(REDACTION, redacted)
    return redacted


def _validated_path(name: str) -> str:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise UnsafeArchive(f"unsafe archive path: {name!r}")
    return path.as_posix()


def read_safe_zip(payload: bytes, settings: Settings) -> list[SafeFile]:
    if len(payload) > settings.upload_max_bytes:
        raise UnsafeArchive("upload exceeds configured limit")
    if not zipfile.is_zipfile(BytesIO(payload)):
        raise UnsafeArchive("only ZIP archives are accepted")

    files: list[SafeFile] = []
    expanded = 0
    seen: set[str] = set()
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            infos = archive.infolist()
            if len(infos) > settings.zip_max_entries:
                raise UnsafeArchive("archive contains too many entries")
            for info in infos:
                path = _validated_path(info.filename)
                unix_mode = info.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    raise UnsafeArchive("symbolic links are not accepted")
                if info.flag_bits & 0x1:
                    raise UnsafeArchive("encrypted entries are not accepted")
                if info.is_dir():
                    continue
                if path in seen:
                    raise UnsafeArchive("duplicate archive paths are not accepted")
                seen.add(path)
                if info.file_size > settings.zip_entry_max_bytes:
                    raise UnsafeArchive(f"entry exceeds configured limit: {path}")
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > settings.zip_max_compression_ratio:
                    raise UnsafeArchive(f"suspicious compression ratio: {path}")
                expanded += info.file_size
                if expanded > settings.expanded_max_bytes:
                    raise UnsafeArchive("expanded archive exceeds configured limit")
                parsed_path = PurePosixPath(path)
                if any(part in settings.excluded_directories for part in parsed_path.parts):
                    continue
                if (
                    parsed_path.name in {".env", ".npmrc", ".pypirc"}
                    or parsed_path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
                    or parsed_path.name.startswith("id_rsa")
                ):
                    continue
                if (
                    parsed_path.suffix.lower() not in settings.allowed_extensions
                    and parsed_path.name not in settings.allowed_extensionless_files
                ):
                    continue
                raw = archive.read(info)
                if len(raw) != info.file_size:
                    raise UnsafeArchive(f"entry size mismatch: {path}")
                if b"\x00" in raw:
                    continue
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                files.append(
                    SafeFile(
                        path=path,
                        content=redact_secrets(
                            text, preserve_python_syntax=parsed_path.suffix.lower() == ".py"
                        ),
                    )
                )
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise UnsafeArchive("invalid ZIP archive") from exc
    return files
