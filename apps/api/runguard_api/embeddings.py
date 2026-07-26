from __future__ import annotations

import asyncio
from typing import Any

from .config import Settings
from .store import Store


class EvidenceIndexer:
    def __init__(self, settings: Settings, store: Store) -> None:
        self.settings = settings
        self.store = store
        self._model: Any = None
        if settings.embeddings_enabled:
            from langchain_openai import OpenAIEmbeddings

            kwargs: dict[str, Any] = {
                "model": settings.embedding_model,
                "dimensions": settings.vector_dimensions,
            }
            if settings.llm_base_url:
                kwargs["base_url"] = settings.llm_base_url
            self._model = OpenAIEmbeddings(**kwargs)

    @property
    def enabled(self) -> bool:
        return self._model is not None and self.store.backend == "postgresql"

    async def index(self, evidence_ids: list[str], documents: list[str]) -> None:
        if not self.enabled or not documents:
            return
        vectors = await self._model.aembed_documents(documents)
        await asyncio.gather(
            *(
                asyncio.to_thread(
                    self.store.upsert_evidence_embedding,
                    evidence_id,
                    vector,
                )
                for evidence_id, vector in zip(evidence_ids, vectors, strict=True)
            )
        )

    async def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        vector = await self._model.aembed_query(query)
        return await asyncio.to_thread(self.store.semantic_evidence, vector, limit)
