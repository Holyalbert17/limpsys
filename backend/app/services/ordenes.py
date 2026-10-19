from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.core.errors import ApiError
from app.core.util import ahora, decimal, rec
from app.domain.geo import distancia_metros
from app.realtime import hub
from app.services.asignacion import asignar_automatico, slots
from app.services.cotizador import cotizar
from app.services.estado_orden import cambiar_estado
from app.services.finanzas import (
    cargo_cancelacion,
    cerrar_cobro,
    crear_pago,
    planilla_cancelacion,
    reembolsar,
    registrar_planilla,
)
from app.services.notificaciones import notificar
from app.services.parametros import obtener_parametros, pbool, pdec, pent

TZ = ZoneInfo("America/Asuncion")


async def nuevo_numero(conn) -> str:
    n = await conn.fetchval("SELECT nextval('operacion.orden_seq')")
    return f"LS-{datetime.now(TZ).year}-{int(n):06d}"


async def _cliente(conn, cliente_id):
    cliente = await conn.fetchrow("SELECT * FROM crm.clientes WHERE id = $1", cliente_id)
    if cliente is None:
        raise ApiError(404, "CLIENTE", "No encontramos el cliente.")
    return cliente


async def guardar_borrador(conn, cliente_id, datos: dict):
    borrador = await conn.fetchrow(
        "SELECT * FROM operacion.ordenes WHERE cliente_id = $1 AND estado = 'BORRADOR'",
        cliente_id,
    )
    campos = {
        "tipo_hogar_id": datos.get("tipo_hogar_id"),
        "tipo_limpieza_id": datos.get("tipo_limpieza_id"),
        "direccion_id": datos.get("direccion_id"),
        "fecha": datos.get("fecha"),
        "hora_inicio": datos.get("hora_inicio"),
        "combo_id": datos.get("combo_id"),
        "notas": datos.get("notas"),
        "presupuesto_maximo": datos.get("presupuesto_maximo"),
        "preferencia_empleado_id": datos.get("preferencia_empleado_id"),
        "es_emergencia": bool(datos.get("es_emergencia")),
        "cupon_codigo": datos.get("cupon"),
        "puntos_canje": int(datos.get("puntos") or 0),
        "factura_a_nombre": datos.get("factura_a_nombre"),
        "origen": datos.get("origen") or "app",
    }
    if borrador is None:
        orden_id = await conn.fetchval(
            """
            INSERT INTO operacion.ordenes (
              numero, cliente_id, tipo_hogar_id, tipo_limpieza_id, direccion_id, fecha, hora_inicio,
              combo_id, notas, presupuesto_maximo, preferencia_empleado_id, es_emergencia,
              cupon_codigo, puntos_canje, factura_a_nombre, origen
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            RETURNING id
            """,
            await nuevo_numero(conn),
            cliente_id,
            campos["tipo_hogar_id"],
            campos["tipo_limpieza_id"],
            campos["direccion_id"],
            campos["fecha"],
            campos["hora_inicio"],
            campos["combo_id"],
            campos["notas"],
            campos["presupuesto_maximo"],
            campos["preferencia_empleado_id"],
            campos["es_emergencia"],
            campos["cupon_codigo"],
            campos["puntos_canje"],
            campos["factura_a_nombre"],
            campos["origen"],
        )
    else:
        orden_id = borrador["id"]
        await conn.execute(
            """
            UPDATE operacion.ordenes SET
              tipo_hogar_id = COALESCE($2, tipo_hogar_id),
              tipo_limpieza_id = COALESCE($3, tipo_limpieza_id),
              direccion_id = COALESCE($4, direccion_id),
              fecha = COALESCE($5, fecha),
              hora_inicio = COALESCE($6, hora_inicio),
              combo_id = $7,
              notas = COALESCE($8, notas),
              presupuesto_maximo = $9,
              preferencia_empleado_id = $10,
              es_emergencia = $11,
              cupon_codigo = $12,
              puntos_canje = $13,
              factura_a_nombre = COALESCE($14, factura_a_nombre),
              actualizado_en = now()
            WHERE id = $1
            """,
            orden_id,
            campos["tipo_hogar_id"],
            campos["tipo_limpieza_id"],
            campos["direccion_id"],
            campos["fecha"],
            campos["hora_inicio"],
            campos["combo_id"],
            campos["notas"],
            campos["presupuesto_maximo"],
            campos["preferencia_empleado_id"],
            campos["es_emergencia"],
            campos["cupon_codigo"],
            campos["puntos_canje"],
            campos["factura_a_nombre"],
        )
    if datos.get("elementos") is not None:
        await conn.execute("DELETE FROM operacion.orden_elementos WHERE orden_id = $1", orden_id)
        for item in datos["elementos"]:
            await conn.execute(
                """
                INSERT INTO operacion.orden_elementos (orden_id, elemento_id, cantidad, precio, minutos)
                SELECT $1, id, $3, adicional, minutos FROM catalogo.elementos WHERE id = $2
                """,
                orden_id,
                item["elemento_id"],
                int(item.get("cantidad") or 1),
            )
    return await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden_id)


def _datos_cotizacion(orden, elementos, direccion, extra: dict | None = None) -> dict:
    datos = {
        "tipo_hogar_id": orden["tipo_hogar_id"],
        "tipo_limpieza_id": orden["tipo_limpieza_id"],
        "elementos": elementos,
        "fecha": orden["fecha"],
        "hora_inicio": orden["hora_inicio"],
        "combo_id": orden["combo_id"],
        "cupon": orden["cupon_codigo"],
        "puntos": orden["puntos_canje"],
        "preferencia_empleado_id": orden["preferencia_empleado_id"],
        "es_emergencia": orden["es_emergencia"],
        "presupuesto_maximo": orden["presupuesto_maximo"],
        "direccion_id": orden["direccion_id"],
        "lat": None if direccion is None else direccion["lat"],
        "lng": None if direccion is None else direccion["lng"],
        "zona_id": None if direccion is None else direccion["zona_id"],
    }
    if extra:
        datos.update(extra)
    return datos


async def _aplicar_precio(conn, orden_id, cotizacion: dict):
    detalle = {clave: valor for clave, valor in cotizacion.items() if clave not in {"alternativas", "opciones_presupuesto"}}
    await conn.execute(
        """
        UPDATE operacion.ordenes SET
          horas_est = $2,
          subtotal = $3,
          descuentos = $4,
          recargos = $5,
          traslado = $6,
          iva = $7,
          total = $8,
          anticipo = $9,
          saldo = $8,
          detalle_precio = $10::jsonb,
          detalle_descuentos = $11::jsonb,
          margen_calculado = $12,
          costo_empleado = $13,
          actualizado_en = now()
        WHERE id = $1
        """,
        orden_id,
        cotizacion["horas_est"],
        cotizacion["subtotal"],
        cotizacion["descuentos"],
        cotizacion["recargos"],
        cotizacion["traslado"],
        cotizacion["iva"],
        cotizacion["total"],
        cotizacion["anticipo"],
        detalle,
        cotizacion["detalle_descuentos"],
        cotizacion.get("margen_pct"),
        cotizacion.get("costo_mano_obra") or 0,
    )


async def _copiar_tareas(conn, orden_id, tipo_limpieza_id):
    await conn.execute("DELETE FROM operacion.orden_tareas WHERE orden_id = $1 AND extra = false", orden_id)
    await conn.execute(
        """
        INSERT INTO operacion.orden_tareas (orden_id, tarea_catalogo_id, ambiente, nombre, obligatoria, orden)
        SELECT $1, id, ambiente, nombre, obligatoria, orden
        FROM catalogo.tareas
        WHERE tipo_limpieza_id = $2 AND activo
        """,
        orden_id,
        tipo_limpieza_id,
    )


async def enviar(conn, cliente_id, datos: dict, actor_id):
    if datos:
        orden = await guardar_borrador(conn, cliente_id, datos)
    else:
        orden = await conn.fetchrow(
            "SELECT * FROM operacion.ordenes WHERE cliente_id = $1 AND estado = 'BORRADOR'",
            cliente_id,
        )
    if orden is None:
        raise ApiError(422, "BORRADOR", "No hay una solicitud para enviar.")
    faltan = [
        nombre
        for nombre, valor in {
            "tipo de hogar": orden["tipo_hogar_id"],
            "tipo de limpieza": orden["tipo_limpieza_id"],
            "dirección": orden["direccion_id"],
            "fecha": orden["fecha"],
            "hora": orden["hora_inicio"],
        }.items()
        if not valor
    ]
    if faltan:
        raise ApiError(422, "INCOMPLETA", "Falta: " + ", ".join(faltan) + ".")
    direccion = await conn.fetchrow("SELECT * FROM crm.direcciones WHERE id = $1 AND cliente_id = $2", orden["direccion_id"], cliente_id)
    if direccion is None:
        raise ApiError(404, "DIRECCION", "No encontramos la dirección.")
    elementos = [
        {"elemento_id": fila["elemento_id"], "cantidad": fila["cantidad"]}
        for fila in await conn.fetch("SELECT elemento_id, cantidad FROM operacion.orden_elementos WHERE orden_id = $1", orden["id"])
    ]
    cotizacion = await cotizar(conn, _datos_cotizacion(orden, elementos, direccion), cliente_id, con_margen=True)
    if cotizacion["fuera_de_cobertura"]:
        raise ApiError(422, "FUERA_DE_COBERTURA", "Esa ubicación está fuera de las zonas de servicio.")
    if orden["presupuesto_maximo"] and cotizacion["total"] > int(decimal(orden["presupuesto_maximo"])) and not datos.get("aceptar_precio"):
        raise ApiError(
            409,
            "PRESUPUESTO",
            "El precio supera tu presupuesto. Elegí una alternativa.",
            {"opciones_presupuesto": cotizacion["opciones_presupuesto"], "total": cotizacion["total"]},
        )
    disponibles = await slots(conn, orden["fecha"], decimal(cotizacion["horas_est"]), direccion["lat"], direccion["lng"])
    hora = orden["hora_inicio"].strftime("%H:%M")
    slot = next((item for item in disponibles if item["hora"] == hora), None)
    if slot is None or not slot["disponible"]:
        raise ApiError(422, "SIN_CUPO", "Ese horario ya no tiene disponibilidad.")
    await _aplicar_precio(conn, orden["id"], cotizacion)
    await _copiar_tareas(conn, orden["id"], orden["tipo_limpieza_id"])
    orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden["id"])
    cliente = await _cliente(conn, cliente_id)
    empresa = await conn.fetchrow("SELECT * FROM crm.empresas WHERE cliente_id = $1 AND activo", cliente_id)
    credito = bool(datos.get("credito_empresa") and empresa)
    if credito:
        await conn.execute(
            "UPDATE operacion.ordenes SET empresa_id = $2, anticipo = 0, saldo = total WHERE id = $1",
            orden["id"],
            empresa["id"],
        )
        orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden["id"])
    else:
        pago = datos.get("pago") or {}
        monto = decimal(pago.get("monto") or orden["anticipo"])
        if monto < decimal(orden["anticipo"]) and decimal(orden["total"]) > 0:
            raise ApiError(422, "ANTICIPO", "El pago no cubre el anticipo.")
        if decimal(orden["total"]) > 0:
            await crear_pago(
                conn,
                orden,
                cliente,
                pago.get("medio") or "transferencia",
                "total" if monto >= decimal(orden["total"]) else "anticipo",
                monto,
                pago.get("referencia"),
                actor_id,
            )
            orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden["id"])
            if decimal(orden["anticipo_pagado"]) < decimal(orden["anticipo"]):
                raise ApiError(402, "PAGO_PENDIENTE", "El anticipo todavía no está confirmado.")
    for descuento in orden["detalle_descuentos"] or []:
        if descuento.get("origen") == "puntos" and descuento.get("puntos"):
            await conn.execute(
                """
                INSERT INTO comercial.puntos_movimientos (cliente_id, orden_id, tipo, puntos, detalle)
                VALUES ($1, $2, 'canjeado', $3, $4)
                """,
                cliente_id,
                orden["id"],
                -int(descuento["puntos"]),
                orden["numero"],
            )
            await conn.execute(
                "UPDATE crm.clientes SET puntos_saldo = puntos_saldo - $2 WHERE id = $1",
                cliente_id,
                int(descuento["puntos"]),
            )
    for descuento in orden["detalle_descuentos"] or []:
        if descuento.get("cupon_id"):
            await conn.execute(
                "UPDATE comercial.cupones SET usos = usos + 1 WHERE id = $1",
                descuento["cupon_id"],
            )
            await conn.execute(
                """
                INSERT INTO comercial.cupon_usos (cupon_id, cliente_id, orden_id, monto)
                VALUES ($1, $2, $3, $4)
                """,
                descuento["cupon_id"],
                cliente_id,
                orden["id"],
                descuento["monto"],
            )
        if descuento.get("origen") == "combo" and orden["combo_id"]:
            await conn.execute("UPDATE comercial.combos SET usos = usos + 1 WHERE id = $1", orden["combo_id"])
    orden = await cambiar_estado(conn, orden["id"], "ENVIADA", actor_id)
    orden = await cambiar_estado(conn, orden["id"], "EN_REVISION", actor_id, nota="Ingresó a la cola")
    usuario = await conn.fetchval("SELECT usuario_id FROM crm.clientes WHERE id = $1", cliente_id)
    await notificar(conn, "solicitud_enviada", usuario, {"numero": orden["numero"]})
    await hub.emitir("admin", {"tipo": "cola", "orden_id": str(orden["id"]), "es_emergencia": orden["es_emergencia"]})
    params = await obtener_parametros(conn)
    if pbool(params, "asignacion_automatica") or orden["es_emergencia"]:
        await asignar_automatico(conn, orden["id"], actor_id)
    return await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden["id"])


async def cancelar_cliente(conn, orden_id, cliente_id, motivo: str, actor_id):
    orden = await conn.fetchrow(
        "SELECT * FROM operacion.ordenes WHERE id = $1 AND cliente_id = $2 FOR UPDATE",
        orden_id,
        cliente_id,
    )
    if orden is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
    if orden["estado"] in {"COMPLETADA", "PAGADA", "CALIFICADA", "CANCELADA"}:
        raise ApiError(409, "ESTADO_INVALIDO", "Esta solicitud ya no se puede cancelar.")
    params = await obtener_parametros(conn)
    if orden["estado"] == "BORRADOR":
        regla = {"cargo": 0, "compensacion": 0, "nombre": "borrador"}
    else:
        regla, _horas = cargo_cancelacion(params, orden["fecha"], orden["hora_inicio"])
    cargo_pct = decimal(regla.get("cargo") or 0)
    retenido = decimal(orden["total"]) * cargo_pct
    pagado = decimal(orden["anticipo_pagado"])
    reembolso = pagado - retenido
    if reembolso < 0:
        reembolso = Decimal(0)
    compensacion = Decimal(0)
    if orden["empleado_id"] and orden["estado"] in {"CONFIRMADA", "EN_CAMINO", "EN_CURSO", "ASIGNADA"}:
        if regla.get("hora_minima_empleado"):
            compensacion = pdec(params, "pago_empleado_hora") * decimal(regla["hora_minima_empleado"]) + decimal(orden["traslado"])
        else:
            compensacion = retenido * decimal(regla.get("compensacion") or 0)
    await conn.execute(
        "UPDATE operacion.ofertas SET estado = 'cancelada' WHERE orden_id = $1 AND estado = 'pendiente'",
        orden_id,
    )
    orden = await cambiar_estado(
        conn,
        orden_id,
        "CANCELADA",
        actor_id,
        nota=motivo,
        campos={"motivo_cancelacion": motivo, "cancelada_por": "cliente"},
    )
    if compensacion > 0:
        await planilla_cancelacion(conn, orden, compensacion, motivo)
    if reembolso > 0:
        await reembolsar(conn, orden, reembolso, actor_id)
    if orden["puntos_canje"]:
        await conn.execute(
            "UPDATE crm.clientes SET puntos_saldo = puntos_saldo + $2 WHERE id = $1",
            cliente_id,
            int(orden["puntos_canje"]),
        )
    usuario_emp = None
    if orden["empleado_id"]:
        usuario_emp = await conn.fetchval("SELECT usuario_id FROM personal.empleados WHERE id = $1", orden["empleado_id"])
    cliente_usuario = await conn.fetchval("SELECT usuario_id FROM crm.clientes WHERE id = $1", cliente_id)
    await notificar(conn, "cancelacion", cliente_usuario, {"numero": orden["numero"], "motivo": motivo})
    if usuario_emp:
        await notificar(conn, "cancelacion", usuario_emp, {"numero": orden["numero"], "motivo": motivo})
    await hub.emitir("admin", {"tipo": "cancelacion", "orden_id": str(orden_id)})
    return {"orden_id": str(orden_id), "cargo_pct": float(cargo_pct), "retenido": int(retenido), "reembolso": int(reembolso)}


async def cancelar_empleado(conn, orden_id, empleado_id, motivo: str):
    orden = await conn.fetchrow(
        "SELECT * FROM operacion.ordenes WHERE id = $1 AND empleado_id = $2 FOR UPDATE",
        orden_id,
        empleado_id,
    )
    if orden is None or orden["estado"] not in {"CONFIRMADA", "EN_CAMINO"}:
        raise ApiError(409, "ESTADO_INVALIDO", "No podés cancelar este servicio.")
    params = await obtener_parametros(conn)
    mes = await conn.fetchval(
        """
        SELECT count(*) FROM personal.sanciones
        WHERE empleado_id = $1 AND tipo = 'cancelacion'
          AND creado_en > date_trunc('month', now())
        """,
        empleado_id,
    )
    await conn.execute(
        """
        INSERT INTO personal.sanciones (empleado_id, tipo, motivo, puntos)
        VALUES ($1, 'cancelacion', $2, 1)
        """,
        empleado_id,
        motivo,
    )
    if int(mes) + 1 >= pent(params, "cancelaciones_suspension_mes"):
        await conn.execute("UPDATE personal.empleados SET estado = 'suspendido', disponible = false WHERE id = $1", empleado_id)
    await cambiar_estado(
        conn,
        orden_id,
        "EN_REVISION",
        None,
        nota=motivo,
        campos={"empleado_id": None, "motivo_cancelacion": motivo, "cancelada_por": None},
    )
    await conn.execute(
        """
        UPDATE operacion.ordenes
        SET cancelada_en = NULL, motivo_cancelacion = NULL
        WHERE id = $1
        """,
        orden_id,
    )
    await conn.execute(
        "UPDATE operacion.ofertas SET estado = 'cancelada' WHERE orden_id = $1 AND estado IN ('pendiente', 'aceptada')",
        orden_id,
    )
    # La penalización queda en sanciones; el contador mensual usa sanciones.
    await conn.execute(
        """
        INSERT INTO operacion.orden_eventos (orden_id, estado, nota, meta)
        VALUES ($1, 'CANCELADA_EMPLEADO', $2, '{"por": "empleado"}'::jsonb)
        """,
        orden_id,
        motivo,
    )
    await asignar_automatico(conn, orden_id)
    cliente_usuario = await conn.fetchval("SELECT usuario_id FROM crm.clientes WHERE id = $1", orden["cliente_id"])
    await notificar(conn, "cancelacion", cliente_usuario, {"numero": orden["numero"], "motivo": "Reasignamos tu servicio sin costo."})
    return {"reembolso": 0, "reasignada": True}


async def en_camino(conn, orden_id, empleado_id):
    orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1 AND empleado_id = $2", orden_id, empleado_id)
    if orden is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
    orden = await cambiar_estado(conn, orden_id, "EN_CAMINO", None)
    cliente_usuario = await conn.fetchval("SELECT usuario_id FROM crm.clientes WHERE id = $1", orden["cliente_id"])
    nombre = await conn.fetchval(
        """
        SELECT u.nombre FROM personal.empleados e
        JOIN seguridad.usuarios u ON u.id = e.usuario_id WHERE e.id = $1
        """,
        empleado_id,
    )
    await notificar(conn, "en_camino", cliente_usuario, {"empleado": nombre, "eta": "en camino", "numero": orden["numero"]})
    await hub.emitir(f"orden:{orden_id}", {"tipo": "estado", "estado": "EN_CAMINO"})
    return orden


async def _direccion_orden(conn, orden):
    return await conn.fetchrow("SELECT * FROM crm.direcciones WHERE id = $1", orden["direccion_id"])


async def registrar_llegada(conn, orden_id, empleado_id, tipo: str, lat: float, lng: float, manual: bool, nota: str | None, foto_id):
    orden = await conn.fetchrow(
        "SELECT * FROM operacion.ordenes WHERE id = $1 AND empleado_id = $2 FOR UPDATE",
        orden_id,
        empleado_id,
    )
    if orden is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
    esperado = "EN_CAMINO" if tipo == "check-in" else "EN_CURSO"
    if orden["estado"] != esperado:
        raise ApiError(409, "ESTADO_INVALIDO", "El servicio no está en el paso correcto.")
    direccion = await _direccion_orden(conn, orden)
    dist = int(distancia_metros(lat, lng, float(direccion["lat"]), float(direccion["lng"])))
    params = await obtener_parametros(conn)
    radio = pent(params, "radio_checkin_m")
    if dist > radio and not manual:
        raise ApiError(422, "FUERA_DE_RADIO", f"Estás a {dist} m. El radio permitido es {radio} m.", {"distancia_m": dist, "radio_m": radio})
    if manual and (not nota or not foto_id):
        raise ApiError(422, "LLEGADA_MANUAL", "Fuera del radio hace falta una nota y una foto.")
    llegada_id = await conn.fetchval(
        """
        INSERT INTO operacion.registro_llegadas (
          orden_id, empleado_id, tipo, lat, lng, distancia_m, manual, nota, foto_archivo_id, revisado
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        RETURNING id
        """,
        orden_id,
        empleado_id,
        tipo,
        lat,
        lng,
        dist,
        manual,
        nota,
        foto_id,
        False if manual else True,
    )
    if manual:
        await hub.emitir("admin", {"tipo": "llegada_manual", "orden_id": str(orden_id), "llegada_id": str(llegada_id)})
        return {"id": str(llegada_id), "manual": True, "distancia_m": dist, "estado": orden["estado"]}
    if tipo == "check-in":
        await cambiar_estado(conn, orden_id, "EN_CURSO", None)
        cliente_usuario = await conn.fetchval("SELECT usuario_id FROM crm.clientes WHERE id = $1", orden["cliente_id"])
        await notificar(conn, "checkin", cliente_usuario, {"empleado": "Tu empleado", "numero": orden["numero"]})
        limite = None
        if orden["fecha"] and orden["hora_inicio"]:
            limite = datetime.combine(orden["fecha"], orden["hora_inicio"], tzinfo=TZ) + timedelta(minutes=pent(params, "alerta_demora_min"))
        if limite and ahora().astimezone(TZ) > limite:
            await notificar(conn, "demora", None, {"numero": orden["numero"], "minutos": pent(params, "alerta_demora_min")})
            await hub.emitir("admin", {"tipo": "demora", "orden_id": str(orden_id)})
    else:
        await _cerrar_servicio(conn, orden, empleado_id, lat, lng)
    fresco = await conn.fetchrow("SELECT estado FROM operacion.ordenes WHERE id = $1", orden_id)
    await hub.emitir(f"orden:{orden_id}", {"tipo": "estado", "estado": fresco["estado"]})
    return {"id": str(llegada_id), "manual": False, "distancia_m": dist, "estado": fresco["estado"]}


async def _cerrar_servicio(conn, orden, empleado_id, lat, lng):
    pendientes = await conn.fetchval(
        """
        SELECT count(*) FROM operacion.orden_tareas
        WHERE orden_id = $1 AND obligatoria AND NOT completada
        """,
        orden["id"],
    )
    if pendientes:
        raise ApiError(422, "CHECKLIST", "Todavía hay tareas obligatorias sin completar.")
    tipo = await conn.fetchrow("SELECT foto_antes_obligatoria, codigo FROM catalogo.tipos_limpieza WHERE id = $1", orden["tipo_limpieza_id"])
    if tipo and tipo["foto_antes_obligatoria"]:
        antes = await conn.fetchval(
            "SELECT count(*) FROM operacion.orden_fotos WHERE orden_id = $1 AND tipo = 'antes'",
            orden["id"],
        )
        if not antes:
            raise ApiError(422, "FOTO_ANTES", "Este servicio pide fotos del antes.")
    despues = await conn.fetchval(
        "SELECT count(*) FROM operacion.orden_fotos WHERE orden_id = $1 AND tipo = 'despues'",
        orden["id"],
    )
    if not despues:
        raise ApiError(422, "FOTO_DESPUES", "Falta la foto del después.")
    llegada = await conn.fetchrow(
        """
        SELECT hora FROM operacion.registro_llegadas
        WHERE orden_id = $1 AND tipo = 'check-in' AND revisado IS TRUE
        ORDER BY hora LIMIT 1
        """,
        orden["id"],
    )
    horas_reales = decimal(orden["horas_est"])
    if llegada:
        delta = ahora() - llegada["hora"]
        horas_reales = decimal(round(delta.total_seconds() / 3600, 2))
    horas_pago = decimal(orden["horas_est"]) + decimal(orden["horas_extra_aprobadas"])
    orden = await cambiar_estado(
        conn,
        orden["id"],
        "COMPLETADA",
        None,
        campos={"horas_reales": horas_reales, "checkout_en": ahora()},
    )
    puntual = True
    if orden["fecha"] and orden["hora_inicio"] and orden["checkin_en"]:
        limite = datetime.combine(orden["fecha"], orden["hora_inicio"], tzinfo=TZ) + timedelta(minutes=15)
        puntual = orden["checkin_en"].astimezone(TZ) <= limite
    await registrar_planilla(conn, orden, horas_pago, puntual)
    await _actualizar_nivel(conn, empleado_id)
    if decimal(orden["saldo"]) <= 0 and not orden["es_garantia"]:
        fresco = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden["id"])
        await cerrar_cobro(conn, fresco, None)
    elif orden["es_garantia"]:
        await cambiar_estado(conn, orden["id"], "PAGADA", None)
    cliente_usuario = await conn.fetchval("SELECT usuario_id FROM crm.clientes WHERE id = $1", orden["cliente_id"])
    await notificar(conn, "checkout", cliente_usuario, {"numero": orden["numero"]})


async def _actualizar_nivel(conn, empleado_id):
    params = await obtener_parametros(conn)
    stats = await conn.fetchrow(
        """
        SELECT count(*) AS servicios,
             COALESCE(AVG(c.puntaje), 0) AS nota
        FROM operacion.ordenes o
        LEFT JOIN operacion.calificaciones c ON c.orden_id = o.id AND c.de_rol = 'cliente'
        WHERE o.empleado_id = $1 AND o.estado IN ('COMPLETADA', 'PAGADA', 'CALIFICADA')
        """,
        empleado_id,
    )
    nota = decimal(stats["nota"] or 0)
    servicios = int(stats["servicios"] or 0)
    nivel = "bronce"
    if servicios >= pent(params, "nivel_oro_servicios") and nota >= pdec(params, "nivel_oro_nota"):
        nivel = "oro"
    elif servicios >= pent(params, "nivel_plata_servicios") and nota >= pdec(params, "nivel_plata_nota"):
        nivel = "plata"
    calificacion = nota if servicios else pdec(params, "calificacion_inicial")
    await conn.execute(
        """
        UPDATE personal.empleados
        SET servicios_completados = $2, calificacion = $3, nivel = $4, actualizado_en = now()
        WHERE id = $1
        """,
        empleado_id,
        servicios,
        calificacion,
        nivel,
    )


async def completar_tarea(conn, orden_id, tarea_id, empleado_id, completada: bool, nota: str | None):
    orden = await conn.fetchrow(
        "SELECT id FROM operacion.ordenes WHERE id = $1 AND empleado_id = $2 AND estado = 'EN_CURSO'",
        orden_id,
        empleado_id,
    )
    if orden is None:
        raise ApiError(409, "ESTADO_INVALIDO", "El checklist se completa durante el servicio.")
    tarea = await conn.fetchrow(
        """
        UPDATE operacion.orden_tareas
        SET completada = $3, nota = COALESCE($4, nota), hora = CASE WHEN $3 THEN now() ELSE NULL END
        WHERE id = $1 AND orden_id = $2
        RETURNING *
        """,
        tarea_id,
        orden_id,
        completada,
        nota,
    )
    if tarea is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la tarea.")
    await hub.emitir(f"orden:{orden_id}", {"tipo": "tarea", "tarea_id": str(tarea_id), "completada": completada})
    return tarea


async def pedir_extra(conn, orden_id, empleado_id, minutos: int, motivo: str):
    orden = await conn.fetchrow(
        "SELECT * FROM operacion.ordenes WHERE id = $1 AND empleado_id = $2 AND estado = 'EN_CURSO'",
        orden_id,
        empleado_id,
    )
    if orden is None:
        raise ApiError(409, "ESTADO_INVALIDO", "Solo se puede pedir tiempo extra con el servicio en curso.")
    tarifa = decimal((orden["detalle_precio"] or {}).get("tarifa_hora") or 0)
    monto = tarifa * Decimal(minutos) / Decimal(60)
    extra = await conn.fetchrow(
        """
        INSERT INTO operacion.extras_tiempo (orden_id, minutos, motivo, monto)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        orden_id,
        minutos,
        motivo,
        monto,
    )
    await conn.execute(
        """
        INSERT INTO operacion.imprevistos (orden_id, empleado_id, tipo, detalle, minutos_extra)
        VALUES ($1, $2, 'necesito_mas_tiempo', $3, $4)
        """,
        orden_id,
        empleado_id,
        motivo,
        minutos,
    )
    cliente_usuario = await conn.fetchval("SELECT usuario_id FROM crm.clientes WHERE id = $1", orden["cliente_id"])
    await hub.emitir(f"usuario:{cliente_usuario}", {"tipo": "extra_tiempo", "extra_id": str(extra["id"]), "minutos": minutos, "monto": int(monto)})
    return extra


async def responder_extra(conn, orden_id, extra_id, cliente_id, aprobar: bool):
    orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1 AND cliente_id = $2", orden_id, cliente_id)
    extra = await conn.fetchrow(
        "SELECT * FROM operacion.extras_tiempo WHERE id = $1 AND orden_id = $2 AND estado = 'pendiente'",
        extra_id,
        orden_id,
    )
    if orden is None or extra is None:
        raise ApiError(404, "NO_ENCONTRADO", "No encontramos el pedido de tiempo extra.")
    estado = "aprobado" if aprobar else "rechazado"
    await conn.execute(
        "UPDATE operacion.extras_tiempo SET estado = $2, respondido_en = now() WHERE id = $1",
        extra_id,
        estado,
    )
    if aprobar:
        horas = decimal(extra["minutos"]) / Decimal(60)
        nuevo_total = decimal(orden["total"]) + decimal(extra["monto"])
        await conn.execute(
            """
            UPDATE operacion.ordenes
            SET horas_extra_aprobadas = horas_extra_aprobadas + $2,
                total = $3,
                saldo = saldo + $4,
                actualizado_en = now()
            WHERE id = $1
            """,
            orden_id,
            horas,
            nuevo_total,
            extra["monto"],
        )
    return {"estado": estado}


async def calificar(conn, orden_id, usuario_id, rol: str, puntaje: Decimal, categorias: dict, comentario: str | None, propina: Decimal):
    orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden_id)
    if orden is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
    if rol == "cliente" and orden["estado"] not in {"COMPLETADA", "PAGADA"}:
        raise ApiError(409, "ESTADO_INVALIDO", "Podés calificar cuando el servicio terminó.")
    if rol == "empleado" and orden["estado"] not in {"COMPLETADA", "PAGADA", "CALIFICADA"}:
        raise ApiError(409, "ESTADO_INVALIDO", "Todavía no podés calificar este servicio.")
    if puntaje < 1 or puntaje > 5:
        raise ApiError(422, "PUNTAJE", "La nota va de 1 a 5.")
    destino = None
    if rol == "cliente" and orden["empleado_id"]:
        destino = await conn.fetchval("SELECT usuario_id FROM personal.empleados WHERE id = $1", orden["empleado_id"])
    if rol == "empleado":
        destino = await conn.fetchval("SELECT usuario_id FROM crm.clientes WHERE id = $1", orden["cliente_id"])
    await conn.execute(
        """
        INSERT INTO operacion.calificaciones (orden_id, de_usuario_id, de_rol, a_usuario_id, puntaje, categorias, comentario, propina)
        VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8)
        """,
        orden_id,
        usuario_id,
        rol,
        destino,
        puntaje,
        categorias,
        comentario,
        propina,
    )
    if rol == "cliente":
        await conn.execute(
            "UPDATE operacion.ordenes SET calificacion_promedio = $2 WHERE id = $1",
            orden_id,
            puntaje,
        )
        if orden["estado"] == "COMPLETADA" and decimal(orden["saldo"]) <= 0:
            await cerrar_cobro(conn, orden, usuario_id)
            orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden_id)
        if orden["estado"] == "PAGADA":
            await cambiar_estado(conn, orden_id, "CALIFICADA", usuario_id)
        if orden["empleado_id"]:
            await _actualizar_nivel(conn, orden["empleado_id"])
        if propina > 0 and orden["empleado_id"]:
            cliente = await conn.fetchrow("SELECT * FROM crm.clientes WHERE id = $1", orden["cliente_id"])
            await crear_pago(conn, orden, cliente, "efectivo", "propina", propina, "propina", usuario_id)
            await conn.execute(
                "UPDATE finanzas.planilla_ingresos SET propina = propina + $2 WHERE orden_id = $1",
                orden_id,
                propina,
            )
    params = await obtener_parametros(conn)
    garantia = rol == "cliente" and puntaje <= pdec(params, "garantia_nota_max")
    return {"garantia_disponible": garantia}


async def pedir_garantia(conn, orden_id, cliente_id):
    orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1 AND cliente_id = $2", orden_id, cliente_id)
    if orden is None or orden["calificacion_promedio"] is None:
        raise ApiError(422, "GARANTIA", "Primero tenés que calificar el servicio.")
    ya = await conn.fetchval("SELECT 1 FROM operacion.ordenes WHERE garantia_de_orden_id = $1", orden_id)
    if ya:
        raise ApiError(422, "GARANTIA", "Esta solicitud ya tiene una re-limpieza.")
    params = await obtener_parametros(conn)
    if decimal(orden["calificacion_promedio"]) > pdec(params, "garantia_nota_max"):
        raise ApiError(422, "GARANTIA", "La garantía aplica cuando la nota es baja.")
    if orden["completada_en"] and ahora() - orden["completada_en"] > timedelta(hours=pent(params, "garantia_horas")):
        raise ApiError(422, "GARANTIA_VENCIDA", "Pasó el plazo de la garantía.")
    usadas = await conn.fetchval(
        "SELECT count(*) FROM operacion.ordenes WHERE cliente_id = $1 AND es_garantia",
        cliente_id,
    )
    if int(usadas) >= pent(params, "garantia_tope_cliente"):
        raise ApiError(422, "GARANTIA_TOPE", "Ya usaste las re-limpiezas incluidas.")
    nueva = await conn.fetchval(
        """
        INSERT INTO operacion.ordenes (
          numero, cliente_id, direccion_id, tipo_hogar_id, tipo_limpieza_id, estado,
          fecha, hora_inicio, horas_est, es_garantia, garantia_de_orden_id, notas, origen
        )
        VALUES ($1,$2,$3,$4,$5,'EN_REVISION',$6,$7,$8,true,$9,'Re-limpieza por garantía','garantia')
        RETURNING id
        """,
        await nuevo_numero(conn),
        cliente_id,
        orden["direccion_id"],
        orden["tipo_hogar_id"],
        orden["tipo_limpieza_id"],
        orden["fecha"],
        orden["hora_inicio"],
        orden["horas_est"],
        orden["id"],
    )
    await _copiar_tareas(conn, nueva, orden["tipo_limpieza_id"])
    await hub.emitir("admin", {"tipo": "cola", "orden_id": str(nueva), "es_emergencia": False})
    return nueva


async def seguimiento(conn, orden_id, rol: str):
    orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden_id)
    if orden is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
    eventos = await conn.fetch(
        "SELECT estado, nota, fecha FROM operacion.orden_eventos WHERE orden_id = $1 ORDER BY fecha",
        orden_id,
    )
    empleado = None
    if orden["empleado_id"] and orden["estado"] in {"CONFIRMADA", "EN_CAMINO", "EN_CURSO", "COMPLETADA", "PAGADA", "CALIFICADA"}:
        empleado = await conn.fetchrow(
            """
            SELECT e.id, u.nombre, e.calificacion, e.servicios_completados, e.nivel, e.foto_archivo_id
            FROM personal.empleados e
            JOIN seguridad.usuarios u ON u.id = e.usuario_id
            WHERE e.id = $1
            """,
            orden["empleado_id"],
        )
    ubicacion = None
    if orden["estado"] in {"EN_CAMINO", "EN_CURSO"}:
        ubicacion = await conn.fetchrow(
            """
            SELECT lat, lng, fecha_hora FROM operacion.ubicaciones_empleado
            WHERE orden_id = $1 ORDER BY fecha_hora DESC LIMIT 1
            """,
            orden_id,
        )
    tareas = await conn.fetch(
        "SELECT id, ambiente, nombre, completada, obligatoria FROM operacion.orden_tareas WHERE orden_id = $1 ORDER BY orden",
        orden_id,
    )
    eta = None
    if ubicacion and orden["direccion_id"]:
        from app.services.mapas import estimar_viaje

        casa = await conn.fetchrow("SELECT lat, lng FROM crm.direcciones WHERE id = $1", orden["direccion_id"])
        if casa:
            eta = await estimar_viaje(conn, float(ubicacion["lat"]), float(ubicacion["lng"]), float(casa["lat"]), float(casa["lng"]))
    return {
        "orden_id": str(orden["id"]),
        "numero": orden["numero"],
        "estado": orden["estado"],
        "eta": eta,
        "fecha": orden["fecha"],
        "hora_inicio": orden["hora_inicio"].strftime("%H:%M") if orden["hora_inicio"] else None,
        "eventos": [rec(fila) for fila in eventos],
        "empleado": None if empleado is None else rec(empleado),
        "ubicacion": None if ubicacion is None else rec(ubicacion),
        "tareas": [rec(fila) for fila in tareas],
        "total": int(decimal(orden["total"])),
    }


async def registrar_ubicacion(conn, empleado_id, lat: float, lng: float, orden_id=None):
    orden = await conn.fetchrow(
        """
        SELECT id, estado FROM operacion.ordenes
        WHERE empleado_id = $1 AND estado IN ('EN_CAMINO', 'EN_CURSO')
        ORDER BY actualizado_en DESC LIMIT 1
        """,
        empleado_id,
    )
    if orden is None:
        return None
    if orden_id and orden["id"] != orden_id:
        return None
    ultima = await conn.fetchrow(
        """
        SELECT lat, lng, fecha_hora FROM operacion.ubicaciones_empleado
        WHERE empleado_id = $1 ORDER BY fecha_hora DESC LIMIT 1
        """,
        empleado_id,
    )
    if ultima and (ahora() - ultima["fecha_hora"]).total_seconds() < 8:
        movido = distancia_metros(float(ultima["lat"]), float(ultima["lng"]), lat, lng)
        if movido < 30:
            return None
    await conn.execute(
        """
        INSERT INTO operacion.ubicaciones_empleado (orden_id, empleado_id, lat, lng)
        VALUES ($1, $2, $3, $4)
        """,
        orden["id"],
        empleado_id,
        lat,
        lng,
    )
    mensaje = {"tipo": "ubicacion", "orden_id": str(orden["id"]), "lat": lat, "lng": lng, "fecha": ahora().isoformat()}
    await hub.emitir(f"orden:{orden['id']}", mensaje)
    await hub.emitir("admin", mensaje)
    return mensaje


async def repetir(conn, orden_id, cliente_id):
    origen = await conn.fetchrow(
        """
        SELECT * FROM operacion.ordenes
        WHERE id = $1 AND cliente_id = $2 AND estado IN ('COMPLETADA', 'PAGADA', 'CALIFICADA')
        """,
        orden_id,
        cliente_id,
    )
    if origen is None:
        raise ApiError(404, "NO_ENCONTRADA", "No hay un servicio anterior para repetir.")
    elementos = [
        {"elemento_id": fila["elemento_id"], "cantidad": fila["cantidad"]}
        for fila in await conn.fetch("SELECT * FROM operacion.orden_elementos WHERE orden_id = $1", orden_id)
    ]
    return await guardar_borrador(
        conn,
        cliente_id,
        {
            "tipo_hogar_id": origen["tipo_hogar_id"],
            "tipo_limpieza_id": origen["tipo_limpieza_id"],
            "direccion_id": origen["direccion_id"],
            "combo_id": origen["combo_id"],
            "notas": origen["notas"],
            "preferencia_empleado_id": origen["empleado_id"],
            "elementos": elementos,
            "origen": "repetir",
        },
    )


async def crear_enlace(conn, orden_id) -> str:
    token = uuid4().hex
    await conn.execute(
        """
        INSERT INTO operacion.enlaces_seguimiento (token, orden_id, expira_en)
        VALUES ($1, $2, now() + interval '2 days')
        """,
        token,
        orden_id,
    )
    return token
