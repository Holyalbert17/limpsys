import logging
from datetime import timedelta

from app.core.db import transaccion
from app.core.util import ahora
from app.services.asignacion import asignar_automatico, expirar_ofertas
from app.services.estado_orden import cambiar_estado
from app.services.notificaciones import notificar
from app.services.parametros import guardar_parametro, obtener_parametros, pent

logger = logging.getLogger("limsys.tareas")


async def _correr_si_toca(conn, nombre: str, horas: int) -> bool:
    fila = await conn.fetchrow("SELECT ultima_vez FROM sistema.tareas_control WHERE nombre = $1", nombre)
    if fila and fila["ultima_vez"] and ahora() - fila["ultima_vez"] < timedelta(hours=horas):
        return False
    await conn.execute(
        """
        INSERT INTO sistema.tareas_control (nombre, ultima_vez) VALUES ($1, now())
        ON CONFLICT (nombre) DO UPDATE SET ultima_vez = now()
        """,
        nombre,
    )
    return True


async def ciclo() -> None:
    async with transaccion() as conn:
        await expirar_ofertas(conn)
        programados = await conn.fetch(
            """
            SELECT * FROM sistema.parametros_programados
            WHERE aplicado = false AND vigente_desde <= now()
            """
        )
        for item in programados:
            await guardar_parametro(conn, item["clave"], item["valor"], item["creado_por"], None)
            await conn.execute("UPDATE sistema.parametros_programados SET aplicado = true WHERE id = $1", item["id"])
        if await _correr_si_toca(conn, "purga_ubicaciones", 6):
            params = await obtener_parametros(conn, forzar=True)
            dias = pent(params, "retencion_ubicacion_dias")
            await conn.execute(
                "DELETE FROM operacion.ubicaciones_empleado WHERE fecha_hora < now() - make_interval(days => $1)",
                dias,
            )
        if await _correr_si_toca(conn, "cerrar_calificaciones", 12):
            params = await obtener_parametros(conn, forzar=True)
            plazo = pent(params, "plazo_calificacion_dias")
            vencidas = await conn.fetch(
                """
                SELECT id FROM operacion.ordenes
                WHERE estado = 'PAGADA' AND pagada_en < now() - make_interval(days => $1)
                """,
                plazo,
            )
            for orden in vencidas:
                await cambiar_estado(conn, orden["id"], "CALIFICADA", None, nota="Plazo de calificación vencido")
        if await _correr_si_toca(conn, "planes", 12):
            planes = await conn.fetch(
                """
                SELECT * FROM operacion.planes_recurrentes
                WHERE estado = 'activo' AND (proxima_fecha IS NULL OR proxima_fecha <= CURRENT_DATE + 7)
                """
            )
            for plan in planes:
                existe = await conn.fetchval(
                    """
                    SELECT 1 FROM operacion.ordenes
                    WHERE plan_id = $1 AND fecha = $2 AND estado <> 'CANCELADA'
                    """,
                    plan["id"],
                    plan["proxima_fecha"],
                )
                if existe or plan["proxima_fecha"] is None:
                    continue
                from app.services.ordenes import _copiar_tareas, nuevo_numero

                orden_id = await conn.fetchval(
                    """
                    INSERT INTO operacion.ordenes (
                      numero, cliente_id, direccion_id, tipo_hogar_id, tipo_limpieza_id,
                      plan_id, estado, fecha, hora_inicio, es_recurrente, empleado_id, origen
                    )
                    VALUES ($1,$2,$3,$4,$5,$6,'EN_REVISION',$7,$8,true,$9,'plan')
                    RETURNING id
                    """,
                    await nuevo_numero(conn),
                    plan["cliente_id"],
                    plan["direccion_id"],
                    plan["tipo_hogar_id"],
                    plan["tipo_limpieza_id"],
                    plan["id"],
                    plan["proxima_fecha"],
                    plan["hora_inicio"],
                    plan["empleado_fijo_id"],
                )
                if plan["tipo_limpieza_id"]:
                    await _copiar_tareas(conn, orden_id, plan["tipo_limpieza_id"])
                salto = 7 if plan["frecuencia"] == "semanal" else 14
                await conn.execute(
                    "UPDATE operacion.planes_recurrentes SET proxima_fecha = proxima_fecha + $2 WHERE id = $1",
                    plan["id"],
                    salto,
                )
                await notificar(
                    conn,
                    "solicitud_enviada",
                    await conn.fetchval("SELECT usuario_id FROM crm.clientes WHERE id = $1", plan["cliente_id"]),
                    {"numero": "plan"},
                )
                await asignar_automatico(conn, orden_id)
    logger.info("Ciclo de mantenimiento listo")
