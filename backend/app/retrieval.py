import re
from dataclasses import dataclass

from sqlalchemy import bindparam, text

from app.adapters import Embedder, VectorStore
from app.config import Settings
from app.database import Database


@dataclass(frozen=True)
class RetrievedChunk:
    id: str
    path: str
    start_line: int
    end_line: int
    content: str
    symbol: str | None
    score: float


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    mode: str
    top_score: float
    degradation: str | None = None


class Retriever:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        embedder: Embedder,
        vectors: VectorStore,
    ) -> None:
        self.database = database
        self.settings = settings
        self.embedder = embedder
        self.vectors = vectors

    async def _lexical(self, repository_id: str, generation_id: str, question: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", question)[:20]
        if not tokens:
            return []
        query = " OR ".join(f'"{token}"' for token in tokens)
        sql = text(
            """
            SELECT chunk_id FROM chunks_fts
            WHERE chunks_fts MATCH :query
              AND repository_id = :repository_id
              AND generation_id = :generation_id
            ORDER BY bm25(chunks_fts)
            LIMIT :limit
            """
        )
        async with self.database.session() as session:
            rows = await session.execute(
                sql,
                {
                    "query": query,
                    "repository_id": repository_id,
                    "generation_id": generation_id,
                    "limit": self.settings.lexical_candidates,
                },
            )
            return [str(row[0]) for row in rows]

    async def _exact(
        self,
        repository_id: str,
        generation_id: str,
        question: str,
    ) -> list[str]:
        identifiers = {
            token.lower()
            for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}", question)
        }
        paths = {
            value.strip("`'\"").lower()
            for value in re.findall(r"[A-Za-z0-9_./-]+\.[A-Za-z0-9]+", question)
        }
        if not identifiers and not paths:
            return []
        clauses = []
        params: dict[str, object] = {
            "repository_id": repository_id,
            "generation_id": generation_id,
        }
        statement = """
            SELECT id FROM chunks
            WHERE repository_id=:repository_id AND generation_id=:generation_id
        """
        if identifiers:
            clauses.append("lower(symbol) IN :identifiers")
            params["identifiers"] = sorted(identifiers)
        if paths:
            clauses.append("lower(path) IN :paths")
            params["paths"] = sorted(paths)
        query = text(f"{statement} AND ({' OR '.join(clauses)}) LIMIT 20").bindparams(
            *(
                [bindparam("identifiers", expanding=True)]
                if identifiers
                else []
            ),
            *([bindparam("paths", expanding=True)] if paths else []),
        )
        async with self.database.session() as session:
            rows = await session.execute(query, params)
            return [str(row[0]) for row in rows]

    async def retrieve(
        self,
        repository_id: str,
        generation_id: str,
        question: str,
        allow_dense: bool = True,
    ) -> RetrievalResult:
        exact_ids = await self._exact(repository_id, generation_id, question)
        lexical_ids = await self._lexical(repository_id, generation_id, question)
        lexical_ids = list(dict.fromkeys([*exact_ids, *lexical_ids]))
        dense_ids: list[str] = []
        degradation = None if allow_dense else "dense index unavailable for active generation"
        if allow_dense:
            try:
                vector = (
                    await self.embedder.embed(
                        [f"{self.settings.embedding_query_prefix}{question}"]
                    )
                )[0]
                dense = await self.vectors.search(
                    repository_id,
                    generation_id,
                    vector,
                    self.settings.dense_candidates,
                )
                dense_ids = [hit.chunk_id for hit in dense]
            except Exception as exc:
                degradation = f"dense retrieval unavailable: {type(exc).__name__}"

        scores: dict[str, float] = {}
        for rank, chunk_id in enumerate(lexical_ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (self.settings.rrf_k + rank)
        for rank, chunk_id in enumerate(dense_ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (self.settings.rrf_k + rank)
        ranked = sorted(scores, key=lambda key: (-scores[key], key))[
            : self.settings.retrieval_limit
        ]
        if not ranked:
            return RetrievalResult([], "lexical_only" if degradation else "hybrid", 0, degradation)

        sql = text(
            """
            SELECT id, path, start_line, end_line, content, symbol
            FROM chunks
            WHERE repository_id = :repository_id
              AND generation_id = :generation_id
              AND id IN :ids
            """
        ).bindparams(bindparam("ids", expanding=True))
        async with self.database.session() as session:
            rows = await session.execute(
                sql,
                {
                    "repository_id": repository_id,
                    "generation_id": generation_id,
                    "ids": ranked,
                },
            )
            by_id = {str(row.id): row for row in rows}
        chunks = [
            RetrievedChunk(
                id=chunk_id,
                path=by_id[chunk_id].path,
                start_line=by_id[chunk_id].start_line,
                end_line=by_id[chunk_id].end_line,
                content=by_id[chunk_id].content,
                symbol=by_id[chunk_id].symbol,
                score=scores[chunk_id],
            )
            for chunk_id in ranked
            if chunk_id in by_id
        ]
        mode = (
            "hybrid"
            if dense_ids and lexical_ids
            else ("dense_only" if dense_ids else "lexical_only")
        )
        return RetrievalResult(chunks, mode, max(scores.values()), degradation)
