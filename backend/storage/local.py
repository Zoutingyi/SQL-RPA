import os
import uuid
import aiofiles
from fastapi import UploadFile
from config import settings


class LocalStorage:
    def __init__(self, base_dir: str | None = None):
        self.base_dir = base_dir or settings.upload_dir
        os.makedirs(self.base_dir, exist_ok=True)

    async def save(self, upload_file: UploadFile, prefix: str = "") -> str:
        safe_prefix = prefix or uuid.uuid4().hex
        safe_name = f"{safe_prefix}_{upload_file.filename}"
        file_path = os.path.join(self.base_dir, safe_name)
        async with aiofiles.open(file_path, "wb") as f:
            content = await upload_file.read()
            await f.write(content)
        return file_path

    async def delete(self, file_path: str) -> None:
        if os.path.exists(file_path):
            os.remove(file_path)

    def get_path(self, filename: str) -> str:
        return os.path.join(self.base_dir, filename)
