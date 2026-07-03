from parking_bot import ingest


def test_load_documents_marks_sensitivity():
    docs = ingest.load_documents()
    assert docs, "expected static docs to be loaded"
    by_source = {d.metadata["source"]: d for d in docs}
    # The underscore-prefixed internal file must be tagged sensitive.
    assert by_source["_internal_contacts.md"].metadata["sensitivity"] == "sensitive"
    # A normal file must be public.
    assert by_source["general_info.md"].metadata["sensitivity"] == "public"


def test_chunking_produces_multiple_chunks():
    docs = ingest.load_documents()
    chunks = ingest.chunk_documents(docs)
    assert len(chunks) >= len(docs)
    # Metadata is preserved on chunks.
    assert all("sensitivity" in c.metadata for c in chunks)
