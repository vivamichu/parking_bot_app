"""Retrieval tests. The Milvus search is mocked so these run offline and
verify the sensitivity-filtering guardrail logic."""
from langchain_core.documents import Document

from parking_bot import retrieval


def _install_fake_store(monkeypatch, docs):
    monkeypatch.setattr(retrieval, "_raw_search", lambda query, k: list(docs)[:k])


def test_sensitive_docs_are_filtered_out(monkeypatch):
    docs = [
        Document(page_content="secret staff phone", metadata={"source": "_internal_contacts.md", "sensitivity": "sensitive"}),
        Document(page_content="we are open 24/7", metadata={"source": "general_info.md", "sensitivity": "public"}),
    ]
    _install_fake_store(monkeypatch, docs)
    results = retrieval.search_documents("staff phone number", k=5)
    sources = {d.metadata["source"] for d in results}
    assert "_internal_contacts.md" not in sources
    assert "general_info.md" in sources


def test_include_sensitive_flag_returns_all(monkeypatch):
    docs = [
        Document(page_content="secret", metadata={"source": "_internal_contacts.md", "sensitivity": "sensitive"}),
    ]
    _install_fake_store(monkeypatch, docs)
    assert retrieval.search_documents("x", k=5, include_sensitive=True)
    assert retrieval.search_documents("x", k=5, include_sensitive=False) == []


def test_search_parking_info_returns_text(monkeypatch):
    docs = [
        Document(page_content="Prices: 400 KZT/hour", metadata={"source": "policies.md", "sensitivity": "public", "title": "Policies"}),
    ]
    _install_fake_store(monkeypatch, docs)
    out = retrieval.search_parking_info("price")
    assert "400 KZT" in out
