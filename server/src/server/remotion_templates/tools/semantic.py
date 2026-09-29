"""ChromaDB-backed semantic index for immutable PR76 Preset records."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class SemanticIndexError(RuntimeError):
    """Raised when ChromaDB or its embedding runtime is unavailable."""


class PresetSemanticIndex:
    """Persist Preset descriptions in a local Chroma collection keyed by preset ID."""

    def __init__(self, root: Path):
        """Keep construction lazy so normal imports do not load the embedding runtime."""
        self.root = root / "chroma"
        self._client = None
        self._collection = None

    def _collection_or_raise(self):
        """Create the persistent collection with Chroma's real embedding function."""
        if self._collection is not None:
            return self._collection
        try:
            import chromadb
            from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        except Exception as exc:  # pragma: no cover - dependency/runtime specific
            raise SemanticIndexError(f"ChromaDB unavailable: {exc}") from exc
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.root))
            self._collection = self._client.get_or_create_collection(
                name="pr76_presets",
                metadata={"hnsw:space": "cosine", "imv_schema": "pr76-v1"},
                embedding_function=DefaultEmbeddingFunction(),
            )
            return self._collection
        except Exception as exc:
            raise SemanticIndexError(f"ChromaDB collection unavailable: {exc}") from exc

    def upsert(self, preset_id: str, description: str) -> None:
        """Index one immutable record description after its catalog transaction commits."""
        collection = self._collection_or_raise()
        collection.upsert(
            ids=[preset_id],
            documents=[description],
            metadatas=[{"preset_id": preset_id}],
        )

    def delete(self, preset_id: str) -> None:
        """Remove a failed post-write index entry during rollback."""
        collection = self._collection_or_raise()
        collection.delete(ids=[preset_id])

    def query(self, query: str, limit: int) -> list[str]:
        """Return Chroma's nearest IDs in its deterministic distance ordering."""
        collection = self._collection_or_raise()
        if collection.count() == 0:
            return []
        result: dict[str, Any] = collection.query(
            query_texts=[query], n_results=min(limit, collection.count()), include=["metadatas", "distances"]
        )
        ids = result.get("ids") or [[]]
        distances = result.get("distances") or [[]]
        pairs = list(zip(ids[0], distances[0] if distances else []))
        # Chroma already orders by distance; tie-break by ID keeps output stable.
        pairs.sort(key=lambda item: (float(item[1]), str(item[0])))
        return [str(item[0]) for item in pairs[:limit]]
