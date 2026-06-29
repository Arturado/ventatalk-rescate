import io
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import UploadFile
from PIL import Image

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB límite de entrada
MAX_OUTPUT_KB    = 500                # 500KB máximo del JPEG resultante
MAX_DIMENSION    = 2000               # px máximo por lado

_executor = ThreadPoolExecutor(max_workers=2)


async def read_limited(upload: UploadFile, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes | None:
    """
    Lee el upload en chunks. Retorna None si supera el límite
    sin haber cargado el archivo completo en memoria.
    """
    chunks = []
    total  = 0
    async for chunk in upload:
        total += len(chunk)
        if total > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)
