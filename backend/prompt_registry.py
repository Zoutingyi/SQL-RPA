import hashlib
import uuid
from sqlalchemy import select, update
from agent.context import ContextManager
from config import settings
from models.database import async_session
from models.schemas import PromptVersion


async def register_active_prompt() -> None:
    content = ContextManager().build_system_prompt()
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    async with async_session() as session:
        found = await session.scalar(select(PromptVersion).where(PromptVersion.version == settings.prompt_version))
        if found:
            if found.content_hash != digest:
                raise RuntimeError("Prompt version content changed; publish a new PROMPT_VERSION")
            found.active = True
        else:
            await session.execute(update(PromptVersion).values(active=False))
            session.add(PromptVersion(id=str(uuid.uuid4()), version=settings.prompt_version, content_hash=digest))
        await session.commit()
