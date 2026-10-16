"""Heading-aware ingestion and Postgres lexical/vector search with RRF fusion."""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import cast

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opspilot.storage import Incident, KnowledgeChunk

MODEL_NAME = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "BAAI/bge-reranker-base"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
DEFAULT_MODEL_CACHE = str(Path(__file__).resolve().parents[2] / ".model-cache")


@dataclass(frozen=True)
class Chunk:
    source_id: str
    source_type: str
    heading: str
    body: str


def chunk_markdown(path: Path, max_words: int = 300, overlap_words: int = 45) -> list[Chunk]:
    """Keep headings attached and split long sections near the plan's 400-token target."""
    source_id = path.stem
    content = path.read_text(encoding="utf-8")
    sections = re.split(r"(?=^##? )", content, flags=re.MULTILINE)
    chunks: list[Chunk] = []
    for section in sections:
        if not section.strip():
            continue
        lines = section.strip().splitlines()
        heading = lines[0].lstrip("# ").strip() if lines[0].startswith("#") else source_id
        words = " ".join(lines[1:] if lines[0].startswith("#") else lines).split()
        if not words:
            continue
        step = max_words - overlap_words
        for start in range(0, len(words), step):
            body = " ".join(words[start : start + max_words])
            if body:
                chunks.append(Chunk(source_id, "runbook", heading, body))
            if start + max_words >= len(words):
                break
    return chunks


def incident_chunk(row: Incident) -> Chunk:
    return Chunk(
        row.number,
        "incident",
        row.short_description,
        f"Service: {row.cmdb_ci}. Symptoms: {row.short_description}. "
        f"Work notes: {row.work_notes}. Resolution: {row.close_notes}.",
    )


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    return TextEmbedding(
        model_name=MODEL_NAME,
        cache_dir=os.getenv("OPSPILOT_MODEL_CACHE", DEFAULT_MODEL_CACHE),
    )


def encode(texts: list[str], *, query: bool = False) -> list[list[float]]:
    """Load the local BGE model lazily; there is no silent fake-vector fallback."""
    prepared = [QUERY_PREFIX + value if query else value for value in texts]
    return [vector.tolist() for vector in _model().embed(prepared, batch_size=32)]


def ingest(session: Session, runbook_dir: Path, *, embed: bool = True) -> int:
    runbooks = [
        chunk for path in sorted(runbook_dir.glob("RB-*.md")) for chunk in chunk_markdown(path)
    ]
    incidents = [incident_chunk(row) for row in session.scalars(select(Incident)).all()]
    all_chunks = runbooks + incidents
    vectors = (
        encode([c.heading + ". " + c.body for c in all_chunks])
        if embed
        else [None] * len(all_chunks)
    )
    session.query(KnowledgeChunk).delete()
    for chunk, vector in zip(all_chunks, vectors, strict=True):
        session.add(KnowledgeChunk(**chunk.__dict__, embedding=vector))
    session.commit()
    return len(all_chunks)


def lexical_search(
    session: Session, query: str, limit: int = 20, source_type: str | None = None
) -> list[KnowledgeChunk]:
    ids = cast(Sequence[int], (
        session.execute(
            text(
                "SELECT id FROM knowledge_chunks "
                "WHERE to_tsvector('english', heading || ' ' || body) "
                "@@ plainto_tsquery('english', :q) "
                "AND (:source_type IS NULL OR source_type = :source_type) "
                "ORDER BY ts_rank(to_tsvector('english', heading || ' ' || body), "
                "plainto_tsquery('english', :q)) DESC, id LIMIT :n"
            ),
            {"q": query, "n": limit, "source_type": source_type},
        )
        .scalars()
        .all()
    ))
    chunks = [session.get(KnowledgeChunk, identifier) for identifier in ids]
    return [chunk for chunk in chunks if chunk is not None]


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "can",
    "do",
    "for",
    "how",
    "i",
    "in",
    "is",
    "of",
    "on",
    "should",
    "the",
    "to",
    "what",
    "when",
    "which",
    "why",
    "with",
}


def _tokens(value: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", value.lower())
    return [
        word[:-1] if word.endswith("s") and len(word) > 4 else word
        for word in words
        if word not in STOPWORDS
    ]


def bm25_search(
    session: Session, query: str, limit: int = 20, source_type: str | None = None
) -> list[KnowledgeChunk]:
    """Small-corpus Okapi BM25 baseline; heading tokens count three times."""
    statement = select(KnowledgeChunk)
    if source_type:
        statement = statement.where(KnowledgeChunk.source_type == source_type)
    documents = list(session.scalars(statement).all())
    if not documents:
        return []
    terms = [_tokens(row.heading) * 3 + _tokens(row.body) for row in documents]
    query_terms = set(_tokens(query))
    document_frequency = Counter(term for tokens in terms for term in set(tokens))
    average_length = sum(map(len, terms)) / len(terms)
    scores = []
    for row, tokens in zip(documents, terms, strict=True):
        counts = Counter(tokens)
        score = 0.0
        for term in query_terms:
            frequency = counts[term]
            if frequency:
                inverse_frequency = math.log(
                    1
                    + (len(documents) - document_frequency[term] + 0.5)
                    / (document_frequency[term] + 0.5)
                )
                score += (
                    inverse_frequency
                    * frequency
                    * 2.5
                    / (frequency + 1.5 * (0.25 + 0.75 * len(tokens) / average_length))
                )
        if score > 0:
            scores.append((score, row))
    return [row for _, row in sorted(scores, key=lambda pair: (-pair[0], pair[1].id))[:limit]]


def vector_search(
    session: Session, query: str, limit: int = 20, source_type: str | None = None
) -> list[KnowledgeChunk]:
    vector = encode([query], query=True)[0]
    statement = select(KnowledgeChunk).where(KnowledgeChunk.embedding.is_not(None))
    if source_type:
        statement = statement.where(KnowledgeChunk.source_type == source_type)
    return list(
        session.scalars(
            statement.order_by(KnowledgeChunk.embedding.cosine_distance(vector)).limit(limit)
        ).all()
    )


def reciprocal_rank_fusion(
    first: list[KnowledgeChunk], second: list[KnowledgeChunk], *, k: int = 60
) -> list[KnowledgeChunk]:
    by_id: dict[int, KnowledgeChunk] = {}
    scores: dict[int, float] = {}
    for results in (first, second):
        for rank, chunk in enumerate(results, 1):
            by_id[chunk.id] = chunk
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1 / (k + rank)
    return [
        by_id[identifier]
        for identifier in sorted(scores, key=lambda value: (-scores[value], value))
    ]


@lru_cache(maxsize=1)
def _reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(
        model_name=RERANK_MODEL,
        cache_dir=os.getenv("OPSPILOT_MODEL_CACHE", DEFAULT_MODEL_CACHE),
    )


def search(
    session: Session,
    query: str,
    mode: str = "hybrid",
    limit: int = 5,
    source_type: str | None = None,
) -> list[dict]:
    lexical = (
        bm25_search(session, query, source_type=source_type)
        if mode != "lexical"
        else lexical_search(session, query, source_type=source_type)
    )
    dense = (
        vector_search(session, query, source_type=source_type)
        if mode not in {"lexical", "bm25"}
        else []
    )
    if mode == "lexical":
        ranked = lexical
    elif mode == "bm25":
        ranked = lexical
    elif mode == "vector":
        ranked = dense
    elif mode in {"hybrid", "rerank"}:
        ranked = reciprocal_rank_fusion(lexical, dense)
    else:
        raise ValueError(f"Unknown retrieval mode: {mode}")
    if mode == "rerank" and ranked:
        documents = [row.heading + ". " + row.body for row in ranked]
        scores = list(_reranker().rerank(query, documents))
        ranked = [
            row for _, row in sorted(zip(scores, ranked, strict=True), key=lambda pair: -pair[0])
        ]
    return [
        {
            "source_id": row.source_id,
            "source_type": row.source_type,
            "heading": row.heading,
            "body": row.body,
            "chunk_id": row.id,
        }
        for row in ranked[:limit]
    ]
