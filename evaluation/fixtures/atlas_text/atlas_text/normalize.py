"""Text normalization helpers for the synthetic Atlas fixture."""


def normalize_title(value: str) -> str:
    """Trim a title and collapse whitespace while preserving case."""
    if not isinstance(value, str):
        raise TypeError("value must be a string")
    return " ".join(value.strip().split())


def slugify(value: str) -> str:
    """Create a lowercase, hyphen-separated slug."""
    return normalize_title(value).lower().replace(" ", "-")
