"""Embedding providers and vector indexes."""

from cross_agent.embedding.client import OpenAICompatibleEmbeddingClient
from cross_agent.embedding.sqlite_index import SQLiteVectorIndex

__all__ = ["OpenAICompatibleEmbeddingClient", "SQLiteVectorIndex"]
