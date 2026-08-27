import asyncio
from openai import AsyncOpenAI
from .base import BaseEmbedding


class OpenAIEmbedding(BaseEmbedding):
    def __init__(self, model: str, api_key: str, base_url: str, batch_size: int = 20):
        self.model = model
        self.batch_size = batch_size
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        batches = [texts[i:i + self.batch_size] for i in range(0, len(texts), self.batch_size)]
        results = []
        for batch in batches:
            resp = await self.client.embeddings.create(model=self.model, input=batch)
            results.extend([d.embedding for d in resp.data])
        return results

    async def embed_query(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result[0]
