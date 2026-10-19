import hashlib
import json

import asyncpg

from app.core.errors import ApiError
from app.core.util import json_default


def _hash(cuerpo) -> str:
    return hashlib.sha256(
        json.dumps(cuerpo, default=json_default, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


async def reservar(conn, clave: str | None, usuario_id, ruta: str, cuerpo) -> dict | None:
    if not clave:
        return None
    digest = _hash(cuerpo)
    try:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO sistema.idempotencia (clave, usuario_id, ruta, body_hash)
                VALUES ($1, $2, $3, $4)
                """,
                clave,
                usuario_id,
                ruta,
                digest,
            )
    except asyncpg.UniqueViolationError:
        fila = await conn.fetchrow(
            "SELECT usuario_id, body_hash, respuesta FROM sistema.idempotencia WHERE clave = $1",
            clave,
        )
        if fila is None or str(fila["usuario_id"]) != str(usuario_id) or fila["body_hash"] != digest:
            raise ApiError(409, "IDEMPOTENCIA", "La clave ya se usó con otros datos.")
        if fila["respuesta"] is None:
            raise ApiError(409, "IDEMPOTENCIA_EN_CURSO", "Esa operación ya se está procesando.")
        return fila["respuesta"]
    return None


async def guardar(conn, clave: str | None, respuesta: dict) -> None:
    if not clave:
        return
    limpia = json.loads(json.dumps(respuesta, default=json_default))
    await conn.execute(
        "UPDATE sistema.idempotencia SET respuesta = $2::jsonb, estado_http = 200 WHERE clave = $1",
        clave,
        limpia,
    )
