"""Retrieval over the Milvus Lite vector store.

Exposes ``search_parking_info`` which returns the most relevant static-knowledge
chunks for a query. Sensitive documents are filtered out by default so private
data stored in the vector DB is never surfaced to end users (a guardrail).
"""
from __future__ import annotations

from functools import lru_cache

from langchain_core.documents import Document

from .config import settings

_OUTPUT_FIELDS = ["text", "source", "title", "sensitivity"]


@lru_cache(maxsize=1)
def _get_client():
    from pymilvus import MilvusClient

    return MilvusClient(uri=settings.milvus_uri)


@lru_cache(maxsize=1)
def _get_embeddings():
    from .embeddings import get_embeddings

    return get_embeddings()


def _raw_search(query: str, k: int) -> list[Document]:
    """Query Milvus and return the top-k hits as Documents (no filtering).

    Isolated so tests can mock retrieval without a live vector store.
    """
    client = _get_client()
    qvec = _get_embeddings().embed_query(query)
    results = client.search(
        collection_name=settings.milvus_collection,
        data=[qvec],
        limit=k,
        output_fields=_OUTPUT_FIELDS,
    )
    docs: list[Document] = []
    for hit in results[0]:
        entity = hit.get("entity", {})
        docs.append(
            Document(
                page_content=entity.get("text", ""),
                metadata={
                    "source": entity.get("source", ""),
                    "title": entity.get("title", ""),
                    "sensitivity": entity.get("sensitivity", "public"),
                    "score": hit.get("distance"),
                },
            )
        )
    return docs


def search_documents(
    query: str, k: int | None = None, include_sensitive: bool = False
) -> list[Document]:
    """Return the top-k relevant chunks, filtering out sensitive docs by default.

    We over-fetch and post-filter in Python so the sensitivity guardrail does not
    depend on vector-DB-specific filter-expression semantics.
    """
    k = k or settings.top_k
    docs = _raw_search(query, k * 3)
    if not include_sensitive:
        docs = [d for d in docs if d.metadata.get("sensitivity") != "sensitive"]
    return docs[:k]


def search_parking_info(query: str, k: int | None = None) -> str:
    """Agent-tool-friendly wrapper: returns concatenated context text."""
    docs = search_documents(query, k=k)
    if not docs:
        return "No relevant information found."
    parts = []
    for d in docs:
        title = d.metadata.get("title", d.metadata.get("source", "info"))
        parts.append(f"[{title}]\n{d.page_content.strip()}")
    return "\n\n---\n\n".join(parts)
