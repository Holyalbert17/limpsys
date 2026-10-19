import time
from decimal import Decimal

from app.core.errors import ApiError
from app.core.util import decimal

_cache: dict = {"datos": None, "vence": 0.0}

PARAMETROS_CRITICOS = {
    "margen_piso_absoluto",
    "iva_divisor",
    "password_inicial_horas",
    "intentos_maximos",
}


def invalidar_parametros() -> None:
    _cache["datos"] = None


async def obtener_parametros(conn, forzar: bool = False) -> dict:
    momento = time.monotonic()
    if not forzar and _cache["datos"] is not None and momento < _cache["vence"]:
        return _cache["datos"]
    filas = await conn.fetch("SELECT clave, valor FROM sistema.parametros")
    datos = {fila["clave"]: fila["valor"] for fila in filas}
    _cache["datos"] = datos
    _cache["vence"] = momento + 5
    return datos


def exigir(params: dict, clave: str):
    if clave not in params:
        raise ApiError(500, "PARAMETRO_FALTANTE", f"Falta el parámetro {clave}.")
    return params[clave]


def pdec(params: dict, clave: str) -> Decimal:
    return decimal(exigir(params, clave))


def pent(params: dict, clave: str) -> int:
    return int(pdec(params, clave))


def pbool(params: dict, clave: str) -> bool:
    return bool(exigir(params, clave))


def pstr(params: dict, clave: str) -> str:
    return str(exigir(params, clave))


async def guardar_parametro(conn, clave: str, valor, usuario_id, ip: str | None) -> None:
    anterior = await conn.fetchrow("SELECT valor FROM sistema.parametros WHERE clave = $1", clave)
    if anterior is None:
        await conn.execute(
            """
            INSERT INTO sistema.parametros (clave, valor, grupo, descripcion, modificado_por)
            VALUES ($1, $2::jsonb, 'custom', 'Parámetro agregado desde el panel', $3)
            """,
            clave,
            valor,
            usuario_id,
        )
    else:
        await conn.execute(
            """
            UPDATE sistema.parametros
            SET valor = $2::jsonb, modificado_por = $3, actualizado_en = now()
            WHERE clave = $1
            """,
            clave,
            valor,
            usuario_id,
        )
    await conn.execute(
        """
        INSERT INTO sistema.parametros_historial (clave, valor_anterior, valor_nuevo, usuario_id)
        VALUES ($1, $2::jsonb, $3::jsonb, $4)
        """,
        clave,
        anterior["valor"] if anterior else None,
        valor,
        usuario_id,
    )
    await conn.execute(
        """
        INSERT INTO sistema.auditoria (usuario_id, accion, entidad, entidad_id, antes, despues, ip)
        VALUES ($1, 'parametro.editar', 'parametro', $2, $3::jsonb, $4::jsonb, $5::inet)
        """,
        usuario_id,
        clave,
        {"valor": anterior["valor"]} if anterior else None,
        {"valor": valor},
        ip,
    )
    invalidar_parametros()
