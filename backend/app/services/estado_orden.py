from app.core.errors import ApiError
from app.domain.estados import MARCA_TIEMPO, TRANSICIONES

CAMPOS = {
    "motivo_cancelacion",
    "cancelada_por",
    "empleado_id",
    "horas_reales",
    "horas_extra_aprobadas",
    "saldo",
    "anticipo",
    "anticipo_pagado",
    "margen_calculado",
    "costo_empleado",
    "conformidad",
    "conformidad_en",
    "calificacion_promedio",
    "checkout_en",
    "total",
    "subtotal",
    "iva",
    "descuentos",
    "recargos",
    "detalle_precio",
    "detalle_descuentos",
    "horas_est",
    "traslado",
}


async def cambiar_estado(conn, orden_id, nuevo: str, actor_id=None, nota: str | None = None, campos: dict | None = None):
    orden = await conn.fetchrow(
        "SELECT * FROM operacion.ordenes WHERE id = $1 FOR UPDATE",
        orden_id,
    )
    if orden is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
    if nuevo not in TRANSICIONES.get(orden["estado"], set()):
        raise ApiError(
            409,
            "ESTADO_INVALIDO",
            f"No se puede pasar de {orden['estado']} a {nuevo}.",
        )
    asignaciones = ["estado = $2", "actualizado_en = now()"]
    valores: list = [orden_id, nuevo]
    marca = MARCA_TIEMPO.get(nuevo)
    if marca:
        asignaciones.append(f"{marca} = COALESCE({marca}, now())")
    for clave, valor in (campos or {}).items():
        if clave not in CAMPOS:
            raise RuntimeError(f"Campo no permitido: {clave}")
        valores.append(valor)
        asignaciones.append(f"{clave} = ${len(valores)}")
    await conn.execute(
        f"UPDATE operacion.ordenes SET {', '.join(asignaciones)} WHERE id = $1",
        *valores,
    )
    await conn.execute(
        """
        INSERT INTO operacion.orden_eventos (orden_id, estado, actor_id, nota)
        VALUES ($1, $2, $3, $4)
        """,
        orden_id,
        nuevo,
        actor_id,
        nota,
    )
    return await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden_id)
