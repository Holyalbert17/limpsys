from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.core.errors import ApiError
from app.core.util import ahora, decimal
from app.domain.estados import ESTADOS_OCUPAN_AGENDA
from app.domain.geo import centroide
from app.realtime import hub
from app.services.estado_orden import cambiar_estado
from app.services.notificaciones import notificar
from app.services.parametros import obtener_parametros, pdec, pent

TZ = ZoneInfo("America/Asuncion")


def _sumar_horas(hora: time, horas: Decimal) -> time:
    base = datetime.combine(datetime.now(TZ).date(), hora)
    return (base + timedelta(hours=float(horas))).time()


async def _ocupado(conn, empleado_id, fecha, hora_inicio: time, horas: Decimal, buffer_min: int, orden_id=None) -> bool:
    fin = _sumar_horas(hora_inicio, horas)
    fila = await conn.fetchval(
        """
        SELECT 1
        FROM operacion.ordenes
        WHERE empleado_id = $1
          AND fecha = $2
          AND estado = ANY($3::text[])
          AND ($6::uuid IS NULL OR id <> $6)
          AND hora_inicio < ($5::time + make_interval(mins => $7))
          AND (hora_inicio + (horas_est * interval '1 hour')) > ($4::time - make_interval(mins => $7))
        LIMIT 1
        """,
        empleado_id,
        fecha,
        list(ESTADOS_OCUPAN_AGENDA),
        hora_inicio,
        fin,
        orden_id,
        buffer_min,
    )
    return fila is not None


async def _en_horario(conn, empleado_id, fecha, hora_inicio: time, horas: Decimal) -> bool:
    fin = _sumar_horas(hora_inicio, horas)
    return bool(
        await conn.fetchval(
            """
            SELECT 1 FROM personal.disponibilidad
            WHERE empleado_id = $1
              AND dia_semana = $2
              AND hora_desde <= $3
              AND hora_hasta >= $4
            LIMIT 1
            """,
            empleado_id,
            fecha.isoweekday(),
            hora_inicio,
            fin,
        )
    )


async def _bloqueado(conn, empleado_id, fecha, hora_inicio: time, horas: Decimal) -> bool:
    inicio = datetime.combine(fecha, hora_inicio, tzinfo=TZ)
    fin = inicio + timedelta(hours=float(horas))
    return bool(
        await conn.fetchval(
            """
            SELECT 1 FROM personal.bloqueos
            WHERE empleado_id = $1 AND desde < $3 AND hasta > $2
            LIMIT 1
            """,
            empleado_id,
            inicio,
            fin,
        )
    )


async def slots(conn, fecha, horas: Decimal, lat=None, lng=None, zona_id=None):
    from app.services.cotizador import descuento_valle, resolver_zona

    params = await obtener_parametros(conn)
    desde = time.fromisoformat(str(params["horario_atencion_desde"]))
    hasta = time.fromisoformat(str(params["horario_atencion_hasta"]))
    paso = pent(params, "slot_minutos")
    buffer_min = pent(params, "buffer_traslado_min")
    zona = await resolver_zona(conn, lat, lng, zona_id)
    if zona is None:
        return []
    empleados = await conn.fetch(
        """
        SELECT e.id
        FROM personal.empleados e
        JOIN personal.empleado_zonas ez ON ez.empleado_id = e.id
        WHERE e.estado = 'activo' AND ez.zona_id = $1
        """,
        zona["id"],
    )
    cursor = datetime.combine(fecha, desde)
    limite = datetime.combine(fecha, hasta)
    salida = []
    while cursor + timedelta(hours=float(horas)) <= limite:
        hora = cursor.time()
        libres = 0
        for empleado in empleados:
            if not await _en_horario(conn, empleado["id"], fecha, hora, horas):
                continue
            if await _bloqueado(conn, empleado["id"], fecha, hora, horas):
                continue
            if await _ocupado(conn, empleado["id"], fecha, hora, horas, buffer_min):
                continue
            libres += 1
        valle = await descuento_valle(conn, fecha, hora, Decimal(100))
        salida.append(
            {
                "hora": hora.strftime("%H:%M"),
                "disponible": libres > 0,
                "empleados": libres,
                "es_valle": valle > 0,
                "descuento_valle_pct": int(valle) if valle > 0 else 0,
            }
        )
        cursor += timedelta(minutes=paso)
    return salida


async def _punto_empleado(conn, empleado_id, zona_id):
    ultima = await conn.fetchrow(
        """
        SELECT lat, lng FROM operacion.ubicaciones_empleado
        WHERE empleado_id = $1
        ORDER BY fecha_hora DESC
        LIMIT 1
        """,
        empleado_id,
    )
    if ultima:
        return float(ultima["lat"]), float(ultima["lng"])
    poligono = await conn.fetchval("SELECT poligono FROM catalogo.zonas WHERE id = $1", zona_id)
    if poligono:
        return centroide(poligono)
    return None


async def rankear(conn, orden) -> list[dict]:
    params = await obtener_parametros(conn)
    if orden["direccion_id"] is None or orden["fecha"] is None or orden["hora_inicio"] is None:
        return []
    direccion = await conn.fetchrow("SELECT * FROM crm.direcciones WHERE id = $1", orden["direccion_id"])
    if direccion is None or direccion["zona_id"] is None:
        return []
    empleados = await conn.fetch(
        """
        SELECT e.*, u.nombre, u.apellido
        FROM personal.empleados e
        JOIN seguridad.usuarios u ON u.id = e.usuario_id
        JOIN personal.empleado_zonas ez ON ez.empleado_id = e.id
        WHERE e.estado = 'activo'
          AND u.estado = 'activo'
          AND ez.zona_id = $1
        """,
        direccion["zona_id"],
    )
    buffer_min = pent(params, "buffer_traslado_min")
    distancia_max = pdec(params, "distancia_max_m")
    cal_inicial = pdec(params, "calificacion_inicial")
    umbral_rechazo = pdec(params, "umbral_rechazo")
    pesos = (
        pdec(params, "peso_cercania"),
        pdec(params, "peso_calificacion"),
        pdec(params, "peso_disponibilidad"),
        pdec(params, "peso_carga"),
    )
    horas = decimal(orden["horas_est"] or 2)
    ya_ofrecidos = {
        fila["empleado_id"]
        for fila in await conn.fetch(
            "SELECT empleado_id FROM operacion.ofertas WHERE orden_id = $1 AND estado IN ('rechazada', 'expirada')",
            orden["id"],
        )
    }
    ranked = []
    for empleado in empleados:
        if empleado["id"] in ya_ofrecidos:
            continue
        if empleado["tipos_preferidos"] and orden["tipo_limpieza_id"] not in list(empleado["tipos_preferidos"]):
            continue
        if not await _en_horario(conn, empleado["id"], orden["fecha"], orden["hora_inicio"], horas):
            continue
        if await _bloqueado(conn, empleado["id"], orden["fecha"], orden["hora_inicio"], horas):
            continue
        if await _ocupado(conn, empleado["id"], orden["fecha"], orden["hora_inicio"], horas, buffer_min, orden["id"]):
            continue
        punto = await _punto_empleado(conn, empleado["id"], direccion["zona_id"])
        dist = 0
        if punto:
            from app.services.mapas import estimar_viaje

            viaje = await estimar_viaje(conn, punto[0], punto[1], float(direccion["lat"]), float(direccion["lng"]))
            dist = viaje["metros"]
        cercania = max(Decimal(0), Decimal(1) - Decimal(str(dist)) / distancia_max)
        calificacion = decimal(empleado["calificacion"] or 0)
        if calificacion <= 0:
            calificacion = cal_inicial
        nota = min(calificacion / Decimal(5), Decimal(1))
        carga_n = await conn.fetchval(
            """
            SELECT count(*) FROM operacion.ordenes
            WHERE empleado_id = $1 AND fecha = $2 AND estado = ANY($3::text[])
            """,
            empleado["id"],
            orden["fecha"],
            list(ESTADOS_OCUPAN_AGENDA),
        )
        carga = max(Decimal(0), Decimal(1) - Decimal(int(carga_n)) / Decimal(6))
        score = pesos[0] * cercania + pesos[1] * nota + pesos[2] * Decimal(1) + pesos[3] * carga
        rechazos = await conn.fetchrow(
            """
            SELECT
              count(*) FILTER (WHERE estado = 'rechazada') AS rechazos,
              count(*) AS total
            FROM operacion.ofertas
            WHERE empleado_id = $1 AND creado_en > now() - interval '30 days'
            """,
            empleado["id"],
        )
        if rechazos["total"] and Decimal(rechazos["rechazos"]) / Decimal(rechazos["total"]) > umbral_rechazo:
            score *= Decimal("0.5")
        if empleado["en_guardia"] and orden["es_emergencia"]:
            score += Decimal("0.05")
        tarifa = decimal(empleado["tarifa_hora"] or pdec(params, "pago_empleado_hora"))
        ranked.append(
            {
                "empleado_id": empleado["id"],
                "usuario_id": empleado["usuario_id"],
                "nombre": f"{empleado['nombre']} {empleado['apellido']}".strip(),
                "puntaje": float(score.quantize(Decimal("0.0001"))),
                "detalle": {
                    "cercania": float(cercania),
                    "calificacion": float(nota),
                    "disponibilidad": 1,
                    "carga": float(carga),
                },
                "distancia_m": int(dist),
                "pago_estimado": int(tarifa * horas),
                "zona_id": direccion["zona_id"],
            }
        )
    ranked.sort(key=lambda item: item["puntaje"], reverse=True)
    return ranked


async def ofrecer_a(conn, orden, candidato: dict, actor_id=None):
    params = await obtener_parametros(conn)
    minutos = pent(params, "tiempo_aceptacion_min")
    zona = await conn.fetchval("SELECT nombre FROM catalogo.zonas WHERE id = $1", candidato["zona_id"])
    oferta_id = await conn.fetchval(
        """
        INSERT INTO operacion.ofertas (
          orden_id, empleado_id, puntaje, detalle_puntaje, pago_estimado, distancia_m, zona_nombre, expira_en
        )
        VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7, now() + make_interval(mins => $8))
        RETURNING id
        """,
        orden["id"],
        candidato["empleado_id"],
        candidato["puntaje"],
        candidato["detalle"],
        candidato["pago_estimado"],
        candidato["distancia_m"],
        zona,
        minutos,
    )
    if orden["estado"] == "EN_REVISION":
        await cambiar_estado(
            conn,
            orden["id"],
            "ASIGNADA",
            actor_id,
            campos={"empleado_id": candidato["empleado_id"]},
        )
    elif orden["estado"] == "ASIGNADA":
        await conn.execute(
            "UPDATE operacion.ordenes SET empleado_id = $2, asignada_en = now(), actualizado_en = now() WHERE id = $1",
            orden["id"],
            candidato["empleado_id"],
        )
    else:
        raise ApiError(409, "ESTADO_INVALIDO", "La solicitud no está lista para asignar.")
    tipo = await conn.fetchval(
        "SELECT nombre FROM catalogo.tipos_limpieza WHERE id = $1",
        orden["tipo_limpieza_id"],
    )
    await notificar(
        conn,
        "asignacion",
        candidato["usuario_id"],
        {
            "tipo": tipo or "limpieza",
            "fecha": str(orden["fecha"]),
            "hora": orden["hora_inicio"].strftime("%H:%M") if orden["hora_inicio"] else "",
            "minutos": minutos,
        },
    )
    await hub.emitir(f"empleado:{candidato['empleado_id']}", {"tipo": "oferta", "oferta_id": str(oferta_id)})
    await hub.emitir("admin", {"tipo": "orden", "orden_id": str(orden["id"]), "estado": "ASIGNADA"})
    return oferta_id


async def asignar_automatico(conn, orden_id, actor_id=None):
    orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1 FOR UPDATE", orden_id)
    if orden is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
    if orden["estado"] not in {"EN_REVISION", "ASIGNADA"}:
        raise ApiError(409, "ESTADO_INVALIDO", "La solicitud no está en la cola.")
    candidatos = await rankear(conn, orden)
    if not candidatos:
        return None
    return await ofrecer_a(conn, orden, candidatos[0], actor_id)


async def asignar_manual(conn, orden_id, empleado_id, actor_id, motivo: str | None):
    orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1 FOR UPDATE", orden_id)
    if orden is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
    if orden["estado"] not in {"ENVIADA", "EN_REVISION", "ASIGNADA", "CONFIRMADA"}:
        raise ApiError(409, "ESTADO_INVALIDO", "Esta solicitud ya no se puede asignar.")
    if orden["estado"] == "ENVIADA":
        orden = await cambiar_estado(conn, orden_id, "EN_REVISION", actor_id)
    if orden["estado"] == "CONFIRMADA":
        await conn.execute(
            "UPDATE operacion.ofertas SET estado = 'cancelada' WHERE orden_id = $1 AND estado = 'pendiente'",
            orden_id,
        )
        orden = await cambiar_estado(conn, orden_id, "EN_REVISION", actor_id, nota=motivo or "Reasignación")
    empleado = await conn.fetchrow(
        """
        SELECT e.*, u.nombre, u.apellido, u.id AS usuario_id
        FROM personal.empleados e
        JOIN seguridad.usuarios u ON u.id = e.usuario_id
        WHERE e.id = $1 AND e.estado = 'activo'
        """,
        empleado_id,
    )
    if empleado is None:
        raise ApiError(422, "EMPLEADO", "El empleado no está activo.")
    candidatos = await rankear(conn, orden)
    elegido = next((item for item in candidatos if item["empleado_id"] == empleado_id), None)
    if elegido is None:
        if not motivo:
            raise ApiError(422, "OVERRIDE", "Ese empleado no cumple las reglas. Indicá un motivo para asignarlo igual.")
        direccion = await conn.fetchrow("SELECT zona_id, lat, lng FROM crm.direcciones WHERE id = $1", orden["direccion_id"])
        params = await obtener_parametros(conn)
        tarifa = decimal(empleado["tarifa_hora"] or pdec(params, "pago_empleado_hora"))
        elegido = {
            "empleado_id": empleado["id"],
            "usuario_id": empleado["usuario_id"],
            "puntaje": 0,
            "detalle": {"override": True, "motivo": motivo},
            "distancia_m": 0,
            "pago_estimado": int(tarifa * decimal(orden["horas_est"] or 0)),
            "zona_id": direccion["zona_id"] if direccion else None,
        }
    await conn.execute(
        "UPDATE operacion.ofertas SET estado = 'cancelada' WHERE orden_id = $1 AND estado = 'pendiente'",
        orden_id,
    )
    return await ofrecer_a(conn, orden, elegido, actor_id)


async def _capacitacion_ok(conn, empleado_id, tipo_limpieza_id) -> bool:
    falta = await conn.fetchval(
        """
        SELECT c.nombre
        FROM personal.capacitaciones c
        LEFT JOIN personal.empleado_capacitacion ec
          ON ec.capacitacion_id = c.id AND ec.empleado_id = $1 AND ec.completada
        WHERE c.activo AND c.obligatoria
          AND (c.tipo_limpieza_id IS NULL OR c.tipo_limpieza_id = $2)
          AND ec.empleado_id IS NULL
        LIMIT 1
        """,
        empleado_id,
        tipo_limpieza_id,
    )
    return falta is None


async def aceptar_oferta(conn, oferta_id, empleado_id):
    oferta = await conn.fetchrow("SELECT * FROM operacion.ofertas WHERE id = $1 FOR UPDATE", oferta_id)
    if oferta is None or oferta["empleado_id"] != empleado_id:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la oferta.")
    if oferta["estado"] != "pendiente":
        raise ApiError(409, "OFERTA", "La oferta ya no está pendiente.")
    if oferta["expira_en"] < ahora():
        await conn.execute("UPDATE operacion.ofertas SET estado = 'expirada', respondida_en = now() WHERE id = $1", oferta_id)
        raise ApiError(409, "OFERTA_VENCIDA", "La oferta venció.")
    orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", oferta["orden_id"])
    if not await _capacitacion_ok(conn, empleado_id, orden["tipo_limpieza_id"]):
        raise ApiError(422, "CAPACITACION", "Te falta una capacitación obligatoria para este servicio.")
    await conn.execute(
        "UPDATE operacion.ofertas SET estado = 'aceptada', respondida_en = now() WHERE id = $1",
        oferta_id,
    )
    await conn.execute(
        "UPDATE operacion.ofertas SET estado = 'cancelada' WHERE orden_id = $1 AND id <> $2 AND estado = 'pendiente'",
        oferta["orden_id"],
        oferta_id,
    )
    await cambiar_estado(conn, oferta["orden_id"], "CONFIRMADA", None, campos={"empleado_id": empleado_id})
    cliente_usuario = await conn.fetchval(
        "SELECT usuario_id FROM crm.clientes WHERE id = $1",
        orden["cliente_id"],
    )
    empleado = await conn.fetchrow(
        """
        SELECT u.nombre FROM personal.empleados e
        JOIN seguridad.usuarios u ON u.id = e.usuario_id
        WHERE e.id = $1
        """,
        empleado_id,
    )
    await notificar(conn, "confirmacion", cliente_usuario, {"empleado": empleado["nombre"], "numero": orden["numero"]})
    await hub.emitir(f"orden:{orden['id']}", {"tipo": "estado", "estado": "CONFIRMADA"})
    return oferta["orden_id"]


async def rechazar_oferta(conn, oferta_id, empleado_id, motivo: str):
    oferta = await conn.fetchrow("SELECT * FROM operacion.ofertas WHERE id = $1 FOR UPDATE", oferta_id)
    if oferta is None or oferta["empleado_id"] != empleado_id or oferta["estado"] != "pendiente":
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la oferta.")
    await conn.execute(
        """
        UPDATE operacion.ofertas
        SET estado = 'rechazada', motivo_rechazo = $2, respondida_en = now()
        WHERE id = $1
        """,
        oferta_id,
        motivo,
    )
    orden = await cambiar_estado(conn, oferta["orden_id"], "EN_REVISION", None, nota=motivo, campos={"empleado_id": None})
    await asignar_automatico(conn, orden["id"])
    return orden["id"]


async def expirar_ofertas(conn) -> int:
    vencidas = await conn.fetch(
        """
        SELECT id, orden_id FROM operacion.ofertas
        WHERE estado = 'pendiente' AND expira_en < now()
        FOR UPDATE SKIP LOCKED
        """
    )
    for oferta in vencidas:
        await conn.execute(
            "UPDATE operacion.ofertas SET estado = 'expirada', respondida_en = now() WHERE id = $1",
            oferta["id"],
        )
        orden = await conn.fetchrow("SELECT estado FROM operacion.ordenes WHERE id = $1", oferta["orden_id"])
        if orden and orden["estado"] == "ASIGNADA":
            await cambiar_estado(conn, oferta["orden_id"], "EN_REVISION", None, nota="Oferta vencida", campos={"empleado_id": None})
            await asignar_automatico(conn, oferta["orden_id"])
    return len(vencidas)
