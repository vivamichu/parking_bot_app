"""Embeddings factory.

Default backend is OpenAI (``text-embedding-3-small``). Set
``EMBEDDING_PROVIDER=huggingface`` to use a local sentence-transformers model
instead — handy for running the pipeline, the evaluation, and CI without any API
key or network access.
"""
from __future__ import annotations

import os

from .config import settings


def get_embeddings():
    provider = os.getenv("EMBEDDING_PROVIDER", "openai").lower()

    if provider in ("huggingface", "hf", "local"):
        from langchain_huggingface import HuggingFaceEmbeddings

        model = os.getenv(
            "HF_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
        return HuggingFaceEmbeddings(model_name=model)

    # Default: OpenAI
    settings.require_openai()
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=settings.embedding_model, api_key=settings.openai_api_key
    )
