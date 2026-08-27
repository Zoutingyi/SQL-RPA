from config import settings
from .base import BaseEmbedding


def create_embedding() -> BaseEmbedding:
    if settings.embedding_provider == "openai":
        from .openai_impl import OpenAIEmbedding

        if settings.embedding_api_key:
            api_key = settings.embedding_api_key
            base_url = settings.embedding_base_url
        else:
            api_key = settings.llm_api_key
            base_url = settings.llm_base_url

        return OpenAIEmbedding(model=settings.embedding_model, api_key=api_key, base_url=base_url)
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")
