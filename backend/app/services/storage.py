import mimetypes
from pathlib import Path
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.errors import ApiError

TIPOS_PERMITIDOS = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
}
LIMITE = 8 * 1024 * 1024


async def guardar_bytes(
    conn,
    data: bytes,
    nombre: str | None,
    content_type: str | None,
    carpeta: str,
    dueno_id: UUID | None,
    metadata: dict | None = None,
) -> UUID:
    if len(data) > LIMITE:
        raise ApiError(413, "ARCHIVO_GRANDE", "El archivo supera los 8 MB.")
    tipo = content_type or mimetypes.guess_type(nombre or "")[0] or "application/octet-stream"
    if tipo not in TIPOS_PERMITIDOS:
        raise ApiError(415, "TIPO_NO_PERMITIDO", "Solo se aceptan fotos JPG, PNG, WebP o PDF.")
    extension = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
    }[tipo]
    archivo_id = uuid4()
    relativo = Path(carpeta) / f"{archivo_id}{extension}"
    destino = settings.carpeta_archivos / relativo
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(data)
    await conn.execute(
        """
        INSERT INTO sistema.archivos (id, clave, nombre, content_type, tamano, carpeta, dueno_id, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
        """,
        archivo_id,
        relativo.as_posix(),
        nombre,
        tipo,
        len(data),
        carpeta,
        dueno_id,
        metadata or {},
    )
    return archivo_id


def ruta_fisica(clave: str) -> Path:
    base = settings.carpeta_archivos.resolve()
    destino = (base / clave).resolve()
    if base not in destino.parents and destino != base:
        raise ApiError(404, "NO_ENCONTRADO", "No encontramos el archivo.")
    return destino
