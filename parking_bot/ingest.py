"""Ingest static knowledge-base documents into Milvus Lite.

Static docs (general info, location, parking details, booking process, policies)
are chunked, embedded, and stored in the vector DB. Files whose name starts with
an underscore (e.g. ``_internal_contacts.md``) are tagged ``sensitivity=sensitive``
so the retriever can keep them away from end users.

We use pymilvus' ``MilvusClient`` directly (Milvus Lite / embedded mode) for a
stable, dependency-light integration.
"""
from __future__ import annotations

from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .config import settings


def load_documents(static_dir: str | None = None) -> list[Document]:
    """Load markdown files from the static data directory into Documents."""
    directory = Path(static_dir or settings.static_dir)
    docs: list[Document] = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        sensitivity = "sensitive" if path.name.startswith("_") else "public"
        docs.append(
            Document(
                page_content=text,
                metadata={
                    "source": path.name,
                    "title": path.stem.lstrip("_").replace("_", " ").title(),
                    "sensitivity": sensitivity,
                },
            )
        )
    return docs


def chunk_documents(docs: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n## ", "\n### ", "\n\n", "\n", " ", ""],
    )
    return splitter.split_documents(docs)


def build_index(drop_old: bool = True) -> int:
    """Embed all static chunks and (re)build the Milvus Lite collection.

    Returns the number of chunks indexed.
    """
    from pymilvus import MilvusClient

    from .embeddings import get_embeddings

    embeddings = get_embeddings()
    chunks = chunk_documents(load_documents())
    texts = [c.page_content for c in chunks]
    vectors = embeddings.embed_documents(texts)
    dim = len(vectors[0])

    Path(settings.milvus_uri).parent.mkdir(parents=True, exist_ok=True)
    client = MilvusClient(uri=settings.milvus_uri)

    name = settings.milvus_collection
    if drop_old and client.has_collection(name):
        client.drop_collection(name)
    if not client.has_collection(name):
        client.create_collection(
            collection_name=name,
            dimension=dim,
            metric_type="COSINE",
            auto_id=True,
            enable_dynamic_field=True,
        )

    rows = [
        {
            "vector": vectors[i],
            "text": chunks[i].page_content,
            "source": chunks[i].metadata["source"],
            "title": chunks[i].metadata["title"],
            "sensitivity": chunks[i].metadata["sensitivity"],
        }
        for i in range(len(chunks))
    ]
    client.insert(collection_name=name, data=rows)
    client.close()
    return len(rows)


if __name__ == "__main__":
    n = build_index(drop_old=True)
    print(
        f"Ingested {n} chunks into Milvus collection "
        f"'{settings.milvus_collection}' at {settings.milvus_uri}"
    )
