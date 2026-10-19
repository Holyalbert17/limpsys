import logging

from app.core.config import settings
from app.core.db import conexion
from app.core.security import hash_password

logger = logging.getLogger("limsys.bootstrap")


async def _usuario_id(conn, cedula: str):
    return await conn.fetchval("SELECT id FROM seguridad.usuarios WHERE cedula = $1", cedula)


async def sembrar() -> None:
    async with conexion() as conn:
        async with conn.transaction():
            if await _usuario_id(conn, settings.admin_cedula) is None:
                rol_id = await conn.fetchval("SELECT id FROM seguridad.roles WHERE codigo = 'superadmin'")
                await conn.execute(
                    """
                    INSERT INTO seguridad.usuarios (
                      rol_id, cedula, password_hash, debe_cambiar_password, nombre, apellido, estado
                    )
                    VALUES ($1, $2, $3, false, 'Super', 'Admin', 'activo')
                    """,
                    rol_id,
                    settings.admin_cedula,
                    hash_password(settings.admin_password),
                )
                logger.info("Superadmin listo: cédula %s", settings.admin_cedula)
