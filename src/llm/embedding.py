"""Abstract embedding provider and LiteLLM-based implementation."""

import logging
import os
from abc import ABC, abstractmethod
from typing import Optional, List

import numpy as np

logger = logging.getLogger("EmbeddingProvider")


class EmbeddingProvider(ABC):
    """Abstract interface for text-to-vector embedding."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Dimensionality of the embedding vectors."""
        ...

    @abstractmethod
    async def embed(self, text: str) -> Optional[np.ndarray]:
        """Embed a single text string into a float32 numpy vector."""
        ...

    async def embed_batch(self, texts: List[str]) -> List[Optional[np.ndarray]]:
        """Embed multiple texts. Default implementation calls embed() per item."""
        return [await self.embed(t) for t in texts]


class LiteLLMEmbeddingProvider(EmbeddingProvider):
    """Embedding via LiteLLM -- supports OpenAI, Cohere, local, etc."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimensions: int = 1536,
    ):
        try:
            from litellm import aembedding
            self._aembedding = aembedding
        except ImportError:
            raise ImportError("litellm is required for LiteLLMEmbeddingProvider")

        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self._dim = dimensions

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, text: str) -> Optional[np.ndarray]:
        try:
            kwargs = {"model": self.model, "input": [text], "encoding_format": "float"}
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.api_base:
                kwargs["api_base"] = self.api_base
            resp = await self._aembedding(**kwargs)
            # Support both attribute and dict access for embedding data
            item = resp.data[0]
            vec = getattr(item, "embedding", None) or item["embedding"]
            return np.array(vec, dtype=np.float32)
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            return None


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Direct OpenAI embedding (fallback when litellm is unavailable)."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        dimensions: int = 1536,
    ):
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
            base_url=api_base,
        )
        self.model = model
        self._dim = dimensions

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, text: str) -> Optional[np.ndarray]:
        try:
            resp = await self._client.embeddings.create(input=[text], model=self.model)
            return np.array(resp.data[0].embedding, dtype=np.float32)
        except Exception as e:
            logger.error(f"OpenAI embedding failed: {e}")
            return None
