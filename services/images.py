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
    Lee el upload en chunks de 64KB.
    Retorna None si supera el límite sin cargar el archivo completo en memoria.
    """
    chunks = []
    total = 0
    chunk_size = 64 * 1024  # 64KB por chunk
    while True:
        chunk = await upload.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def compress_image(file_bytes: bytes, max_kb: int = MAX_OUTPUT_KB) -> bytes | None:
    """
    Convierte cualquier imagen a JPEG optimizado bajo el límite de tamaño.
    Retorna None si el archivo no es una imagen válida.
    """
    try:
        img = Image.open(io.BytesIO(file_bytes))

        if img.mode in ("RGBA", "P", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

        quality = 85
        output  = io.BytesIO()
        while quality >= 20:
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=quality, optimize=True)
            if output.tell() <= max_kb * 1024:
                break
            quality -= 10

        return output.getvalue()
    except Exception:
        return None


async def compress_image_async(file_bytes: bytes) -> bytes | None:
    """Versión async — ejecuta PIL en thread pool para no bloquear el event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, compress_image, file_bytes)
