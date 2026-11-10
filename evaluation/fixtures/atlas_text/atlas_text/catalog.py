"""In-memory document catalog for the synthetic Atlas fixture."""

from dataclasses import dataclass

from atlas_text.normalize import normalize_title


@dataclass(frozen=True)
class Document:
    identifier: str
    title: str


class Catalog:
    """Store documents by identifier and reject duplicates."""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}

    def add(self, document: Document) -> None:
        if document.identifier in self._documents:
            raise ValueError(f"duplicate document: {document.identifier}")
        cleaned = Document(document.identifier, normalize_title(document.title))
        self._documents[cleaned.identifier] = cleaned

    def get(self, identifier: str) -> Document | None:
        return self._documents.get(identifier)
