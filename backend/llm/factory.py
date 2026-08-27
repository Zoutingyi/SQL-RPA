from config import settings
from .base import BaseLLM


PROVIDER_PRESETS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "stream_usage": True,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "stream_usage": True,
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "glm-4",
        "stream_usage": True,
    },
    "moonshot": {
        "base_url": "https://api.moonshot.cn/v1",
        "default_model": "moonshot-v1-8k",
        "stream_usage": True,
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "stream_usage": True,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.1-8b-instant",
        "stream_usage": True,
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "default_model": "llama3",
        "stream_usage": False,
    },
}


def create_llm() -> BaseLLM:
    provider = settings.llm_provider.lower()
    if provider in PROVIDER_PRESETS:
        preset = PROVIDER_PRESETS[provider]
        if not settings.llm_api_key and provider != "ollama":
            raise ValueError(
                "LLM_API_KEY 未设置。请在 backend/.env 中配置 LLM_API_KEY。\n"
                "LLM_API_KEY is not set. Please configure it in backend/.env."
            )
        from .openai_impl import OpenAILLM
        return OpenAILLM(
            base_url=settings.llm_base_url or preset["base_url"],
            model=settings.llm_model or preset["default_model"],
            stream_usage=preset.get("stream_usage", False),
        )
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
