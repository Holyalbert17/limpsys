from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

from app.core.db import transaccion
from app.core.deps import sesion_completa
from app.core.errors import ApiError
from app.services.storage import guardar_bytes, ruta_fisica

router = APIRouter(tags=["Archivos"])


@router.post("/archivos")
async def subir(
    archivo: UploadFile = File(...),
    carpeta: str = Form("general"),
    usuario: dict = Depends(sesion_completa),
):
    if carpeta not in {"ordenes", "documentos", "fachadas", "general"}:
        raise ApiError(422, "CARPETA", "La carpeta no está permitida.")
    data = await archivo.read()
    async with transaccion() as conn:
        archivo_id = await guardar_bytes(
            conn,
            data,
            archivo.filename,
            archivo.content_type,
            carpeta,
            usuario["id"],
            {},
        )
    return {"id": str(archivo_id)}


@router.get("/archivos/{archivo_id}")
async def descargar(archivo_id: UUID, usuario: dict = Depends(sesion_completa)):
    async with transaccion() as conn:
        fila = await conn.fetchrow("SELECT * FROM sistema.archivos WHERE id = $1", archivo_id)
        if fila is None:
            raise ApiError(404, "NO_ENCONTRADO", "No encontramos el archivo.")
        if usuario["rol"] not in {"admin", "superadmin"} and fila["dueno_id"] != usuario["id"]:
            raise ApiError(403, "PROHIBIDO", "No podés ver este archivo.")
    destino = ruta_fisica(fila["clave"])
    if not destino.exists():
        raise ApiError(404, "NO_ENCONTRADO", "No encontramos el archivo.")
    return FileResponse(destino, media_type=fila["content_type"], filename=fila["nombre"] or destino.name)
