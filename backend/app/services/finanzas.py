from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.errors import ApiError
from app.core.util import ahora, decimal
from app.services.estado_orden import cambiar_estado
from app.services.notificaciones import notificar
from app.services.parametros import obtener_parametros, pdec, pent, pstr

TZ = ZoneInfo("America/Asuncion")
MEDIOS_SIMULADOS = {"tarjeta", "transferencia", "billetera", "efectivo", "simulado"}


async def _numero_factura(conn, params) -> str:
    n = await conn.fetchval("SELECT nextval('finanzas.factura_seq')")
    return f"{pstr(params, 'establecimiento')}-{pstr(params, 'punto_expedicion')}-{int(n):07d}"


async def crear_pago(conn, orden, cliente, medio: str, tipo: str, monto: Decimal, referencia: str | None, actor_id):
    if tipo not in {"anticipo", "saldo", "total", "propina"}:
        raise ApiError(422, "TIPO_PAGO", "Ese tipo de pago no está habilitado.")
    medio_row = await conn.fetchrow("SELECT * FROM finanzas.medios_pago WHERE codigo = $1 AND activo", medio)
    if medio_row is None:
        raise ApiError(422, "MEDIO_PAGO", "Ese medio de pago no está habilitado.")
    if monto <= 0:
        raise ApiError(422, "MONTO", "El monto tiene que ser mayor a cero.")
    estado = "pendiente"
    if medio == "saldo":
        if decimal(cliente["credito_saldo"]) < monto:
            raise ApiError(422, "SALDO", "No tenés saldo suficiente.")
        await conn.execute(
            "UPDATE crm.clientes SET credito_saldo = credito_saldo - $2, actualizado_en = now() WHERE id = $1",
            cliente["id"],
            monto,
        )
        estado = "confirmado"
    elif settings.simular_pagos and medio in MEDIOS_SIMULADOS:
        estado = "confirmado"
    pago_id = await conn.fetchval(
        """
        INSERT INTO finanzas.pagos (orden_id, cliente_id, medio, tipo, monto, estado, referencia, confirmado_en)
        VALUES ($1, $2, $3, $4, $5, $6, $7, CASE WHEN $6 = 'confirmado' THEN now() ELSE NULL END)
        RETURNING id
        """,
        orden["id"],
        cliente["id"],
        medio,
        tipo,
        monto,
        estado,
        referencia,
    )
    if estado == "confirmado":
        await aplicar_pago(conn, pago_id, actor_id)
    return await conn.fetchrow("SELECT * FROM finanzas.pagos WHERE id = $1", pago_id)


async def aplicar_pago(conn, pago_id, actor_id=None):
    pago = await conn.fetchrow("SELECT * FROM finanzas.pagos WHERE id = $1 FOR UPDATE", pago_id)
    if pago is None:
        raise ApiError(404, "NO_ENCONTRADO", "No encontramos el pago.")
    if pago["estado"] == "confirmado" and pago["tipo"] != "reembolso":
        orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1 FOR UPDATE", pago["orden_id"])
        if orden is None:
            return pago
        ya = await conn.fetchval(
            """
            SELECT COALESCE(SUM(monto), 0) FROM finanzas.pagos
            WHERE orden_id = $1 AND estado = 'confirmado' AND tipo IN ('anticipo', 'saldo', 'total')
            """,
            orden["id"],
        )
        # Evita sumar dos veces si esta función se llama justo después del insert ya confirmado.
        anticipo_pagado = decimal(ya)
        saldo = decimal(orden["total"]) - anticipo_pagado
        if saldo < 0:
            saldo = Decimal(0)
        await conn.execute(
            """
            UPDATE operacion.ordenes
            SET anticipo_pagado = $2, saldo = $3, actualizado_en = now()
            WHERE id = $1
            """,
            orden["id"],
            anticipo_pagado,
            saldo,
        )
        cliente_usuario = await conn.fetchval(
            "SELECT usuario_id FROM crm.clientes WHERE id = $1",
            orden["cliente_id"],
        )
        await notificar(
            conn,
            "pago",
            cliente_usuario,
            {"monto": int(pago["monto"]), "numero": orden["numero"]},
        )
        orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden["id"])
        if orden["estado"] == "COMPLETADA" and decimal(orden["saldo"]) <= 0:
            await cerrar_cobro(conn, orden, actor_id)
    return pago


async def confirmar_pago_pendiente(conn, pago_id, actor_id=None):
    pago = await conn.fetchrow("SELECT * FROM finanzas.pagos WHERE id = $1 FOR UPDATE", pago_id)
    if pago is None:
        raise ApiError(404, "NO_ENCONTRADO", "No encontramos el pago.")
    if pago["estado"] == "confirmado":
        return pago
    await conn.execute(
        "UPDATE finanzas.pagos SET estado = 'confirmado', confirmado_en = now() WHERE id = $1",
        pago_id,
    )
    return await aplicar_pago(conn, pago_id, actor_id)


async def cerrar_cobro(conn, orden, actor_id=None):
    if orden["estado"] != "COMPLETADA":
        return
    await cambiar_estado(conn, orden["id"], "PAGADA", actor_id)
    await emitir_factura(conn, orden["id"])
    await _acreditar_puntos(conn, orden)
    await _acreditar_referido(conn, orden)


async def emitir_factura(conn, orden_id):
    existe = await conn.fetchval(
        "SELECT id FROM finanzas.facturas WHERE orden_id = $1 AND tipo = 'factura'",
        orden_id,
    )
    if existe:
        return existe
    orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden_id)
    cliente = await conn.fetchrow("SELECT * FROM crm.clientes WHERE id = $1", orden["cliente_id"])
    params = await obtener_parametros(conn)
    numero = await _numero_factura(conn, params)
    receptor_tipo = "consumidor_final"
    receptor_doc = cliente["usuario_id"] and None
    usuario = await conn.fetchrow("SELECT cedula FROM seguridad.usuarios WHERE id = $1", cliente["usuario_id"])
    receptor_doc = usuario["cedula"]
    receptor_nombre = f"{cliente['nombre']} {cliente['apellido']}".strip()
    if cliente["ruc"]:
        receptor_tipo = "contribuyente"
        receptor_doc = cliente["ruc"]
        receptor_nombre = cliente["razon_social"] or receptor_nombre
    vencimiento = None
    if orden["empresa_id"]:
        empresa = await conn.fetchrow("SELECT plazo_dias FROM crm.empresas WHERE id = $1", orden["empresa_id"])
        dias = empresa["plazo_dias"] if empresa else pent(params, "plazo_empresa_dias")
        vencimiento = (ahora() + timedelta(days=dias)).date()
    payload = {
        "emisor": pstr(params, "razon_social_emisor"),
        "ruc_emisor": pstr(params, "ruc_emisor"),
        "timbrado": pstr(params, "timbrado"),
        "iva_divisor": str(pdec(params, "iva_divisor")),
        "orden": orden["numero"],
    }
    return await conn.fetchval(
        """
        INSERT INTO finanzas.facturas (
          orden_id, cliente_id, numero, receptor_tipo, receptor_doc, receptor_nombre,
          timbrado, establecimiento, punto_expedicion, subtotal, iva, total, vencimiento, payload
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb)
        RETURNING id
        """,
        orden["id"],
        cliente["id"],
        numero,
        receptor_tipo,
        receptor_doc,
        receptor_nombre,
        pstr(params, "timbrado"),
        pstr(params, "establecimiento"),
        pstr(params, "punto_expedicion"),
        orden["subtotal"],
        orden["iva"],
        orden["total"],
        vencimiento,
        payload,
    )


async def _acreditar_puntos(conn, orden):
    params = await obtener_parametros(conn)
    cada = pent(params, "puntos_cada_gs")
    if cada <= 0 or orden["es_garantia"]:
        return
    puntos = int(decimal(orden["total"]) // Decimal(cada))
    if puntos <= 0:
        return
    vence = ahora() + timedelta(days=pent(params, "puntos_vencen_dias"))
    await conn.execute(
        """
        INSERT INTO comercial.puntos_movimientos (cliente_id, orden_id, tipo, puntos, detalle, vence_en)
        VALUES ($1, $2, 'ganado', $3, $4, $5)
        """,
        orden["cliente_id"],
        orden["id"],
        puntos,
        f"Servicio {orden['numero']}",
        vence,
    )
    await conn.execute(
        "UPDATE crm.clientes SET puntos_saldo = puntos_saldo + $2, actualizado_en = now() WHERE id = $1",
        orden["cliente_id"],
        puntos,
    )


async def _acreditar_referido(conn, orden):
    referido = await conn.fetchrow(
        "SELECT * FROM comercial.referidos WHERE referido_id = $1 AND estado = 'pendiente'",
        orden["cliente_id"],
    )
    if referido is None:
        return
    previas = await conn.fetchval(
        """
        SELECT count(*) FROM operacion.ordenes
        WHERE cliente_id = $1 AND estado IN ('PAGADA', 'CALIFICADA') AND id <> $2
        """,
        orden["cliente_id"],
        orden["id"],
    )
    if previas:
        return
    params = await obtener_parametros(conn)
    credito = pdec(params, "referido_credito")
    for cliente_id in (referido["referidor_id"], referido["referido_id"]):
        await conn.execute(
            "UPDATE crm.clientes SET credito_saldo = credito_saldo + $2, actualizado_en = now() WHERE id = $1",
            cliente_id,
            credito,
        )
    await conn.execute(
        "UPDATE comercial.referidos SET estado = 'acreditado', credito_otorgado = $2 WHERE id = $1",
        referido["id"],
        credito,
    )


async def registrar_planilla(conn, orden, horas_pago: Decimal, puntual: bool):
    params = await obtener_parametros(conn)
    costo_hora = decimal(orden["detalle_precio"].get("costo_hora") or pdec(params, "pago_empleado_hora"))
    costo = costo_hora * horas_pago
    cargas = costo * pdec(params, "cargas_porcentaje")
    insumos = pdec(params, "insumos_por_hora") * decimal(orden["horas_est"])
    bonos = Decimal(0)
    if puntual:
        bonos += pdec(params, "bono_puntualidad")
    if orden["calificacion_promedio"] and decimal(orden["calificacion_promedio"]) >= pdec(params, "umbral_calificacion_bono"):
        bonos += pdec(params, "bono_calificacion")
    costo_total = costo + bonos + cargas + decimal(orden["traslado"]) + insumos
    margen = decimal(orden["total"]) - costo_total
    margen_pct = (margen / decimal(orden["total"])) if decimal(orden["total"]) else Decimal(0)
    estado_pago = "pagado" if decimal(orden["saldo"]) <= 0 else "pendiente"
    await conn.execute(
        """
        INSERT INTO finanzas.planilla_ingresos (
          orden_id, empleado_id, precio, costo_mano_obra, cargas, traslado, insumos, margen, margen_pct, estado_pago
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        ON CONFLICT (orden_id) DO UPDATE SET
          costo_mano_obra = EXCLUDED.costo_mano_obra,
          cargas = EXCLUDED.cargas,
          margen = EXCLUDED.margen,
          margen_pct = EXCLUDED.margen_pct,
          estado_pago = EXCLUDED.estado_pago
        """,
        orden["id"],
        orden["empleado_id"],
        orden["total"],
        costo + bonos,
        cargas,
        orden["traslado"],
        insumos,
        margen,
        margen_pct,
        estado_pago,
    )
    await conn.execute(
        "UPDATE operacion.ordenes SET costo_empleado = $2, margen_calculado = $3 WHERE id = $1",
        orden["id"],
        costo + bonos,
        margen_pct,
    )


async def planilla_cancelacion(conn, orden, costo_empleado: Decimal, motivo: str):
    await conn.execute(
        """
        INSERT INTO finanzas.planilla_ingresos (
          orden_id, empleado_id, precio, costo_mano_obra, traslado, margen, estado_pago, motivo_cancelacion
        )
        VALUES ($1,$2,$3,$4,$5,$6,'cancelada',$7)
        ON CONFLICT (orden_id) DO UPDATE SET
          motivo_cancelacion = EXCLUDED.motivo_cancelacion,
          costo_mano_obra = EXCLUDED.costo_mano_obra,
          estado_pago = 'cancelada'
        """,
        orden["id"],
        orden["empleado_id"],
        orden["total"],
        costo_empleado,
        orden["traslado"],
        decimal(orden["total"]) - costo_empleado,
        motivo,
    )


def cargo_cancelacion(params, fecha, hora) -> tuple[dict, float]:
    reglas = list(exigir_reglas(params))
    if fecha is None or hora is None:
        return reglas[0], 999
    inicio = datetime.combine(fecha, hora, tzinfo=TZ)
    horas = (inicio - ahora().astimezone(TZ)).total_seconds() / 3600
    for regla in sorted(reglas, key=lambda item: item["horas_min"], reverse=True):
        if horas >= float(regla["horas_min"]):
            return regla, horas
    return sorted(reglas, key=lambda item: item["horas_min"])[0], horas


def exigir_reglas(params):
    reglas = params.get("cancelacion")
    if not isinstance(reglas, list) or not reglas:
        raise ApiError(500, "PARAMETRO_FALTANTE", "Falta el parámetro cancelacion.")
    return reglas


async def reembolsar(conn, orden, monto: Decimal, actor_id):
    if monto <= 0:
        return None
    cliente = await conn.fetchrow("SELECT * FROM crm.clientes WHERE id = $1", orden["cliente_id"])
    await conn.execute(
        "UPDATE crm.clientes SET credito_saldo = credito_saldo + $2, actualizado_en = now() WHERE id = $1",
        cliente["id"],
        monto,
    )
    pago_id = await conn.fetchval(
        """
        INSERT INTO finanzas.pagos (orden_id, cliente_id, medio, tipo, monto, estado, referencia, confirmado_en)
        VALUES ($1, $2, 'saldo', 'reembolso', $3, 'confirmado', 'reembolso-cancelacion', now())
        RETURNING id
        """,
        orden["id"],
        cliente["id"],
        monto,
    )
    factura = await conn.fetchrow(
        "SELECT * FROM finanzas.facturas WHERE orden_id = $1 AND tipo = 'factura'",
        orden["id"],
    )
    if factura:
        params = await obtener_parametros(conn)
        numero = await _numero_factura(conn, params)
        iva = monto / pdec(params, "iva_divisor")
        await conn.execute(
            """
            INSERT INTO finanzas.facturas (
              orden_id, cliente_id, numero, tipo, receptor_tipo, receptor_doc, receptor_nombre,
              subtotal, iva, total, estado, payload
            )
            VALUES ($1,$2,$3,'nota_credito',$4,$5,$6,$7,$8,$9,'pendiente_sifen',$10::jsonb)
            """,
            orden["id"],
            orden["cliente_id"],
            numero,
            factura["receptor_tipo"],
            factura["receptor_doc"],
            factura["receptor_nombre"],
            monto - iva,
            iva,
            monto,
            {"factura_origen": factura["numero"]},
        )
    return pago_id


async def generar_liquidacion(conn, empleado_id: UUID, desde, hasta):
    filas = await conn.fetch(
        """
        SELECT p.*
        FROM finanzas.planilla_ingresos p
        JOIN operacion.ordenes o ON o.id = p.orden_id
        WHERE p.empleado_id = $1
          AND p.liquidacion_id IS NULL
          AND p.estado_pago <> 'cancelada'
          AND o.fecha BETWEEN $2 AND $3
        """,
        empleado_id,
        desde,
        hasta,
    )
    if not filas:
        raise ApiError(422, "SIN_MOVIMIENTOS", "No hay servicios para liquidar en ese período.")
    horas = await conn.fetchval(
        """
        SELECT COALESCE(SUM(horas_est + horas_extra_aprobadas), 0)
        FROM operacion.ordenes
        WHERE id = ANY($1::uuid[])
        """,
        [fila["orden_id"] for fila in filas],
    )
    mano = sum((decimal(fila["costo_mano_obra"]) for fila in filas), Decimal(0))
    propinas = sum((decimal(fila["propina"]) for fila in filas), Decimal(0))
    adelantos = await conn.fetch(
        """
        SELECT * FROM finanzas.adelantos
        WHERE empleado_id = $1 AND estado = 'aprobado' AND liquidacion_id IS NULL
        """,
        empleado_id,
    )
    total_adelantos = sum((decimal(fila["monto"]) for fila in adelantos), Decimal(0))
    total = mano + propinas - total_adelantos
    if total < 0:
        total = Decimal(0)
    liquidacion_id = await conn.fetchval(
        """
        INSERT INTO finanzas.liquidaciones (
          empleado_id, periodo_desde, periodo_hasta, horas, bonos, propinas, adelantos, total
        )
        VALUES ($1,$2,$3,$4,0,$5,$6,$7)
        RETURNING id
        """,
        empleado_id,
        desde,
        hasta,
        horas,
        propinas,
        total_adelantos,
        total,
    )
    await conn.execute(
        "UPDATE finanzas.planilla_ingresos SET liquidacion_id = $2 WHERE id = ANY($1::uuid[])",
        [fila["id"] for fila in filas],
        liquidacion_id,
    )
    if adelantos:
        await conn.execute(
            """
            UPDATE finanzas.adelantos
            SET liquidacion_id = $2, estado = 'descontado'
            WHERE id = ANY($1::uuid[])
            """,
            [fila["id"] for fila in adelantos],
            liquidacion_id,
        )
    return await conn.fetchrow("SELECT * FROM finanzas.liquidaciones WHERE id = $1", liquidacion_id)


async def pedir_adelanto(conn, empleado_id: UUID, monto: Decimal):
    params = await obtener_parametros(conn)
    ganado = await conn.fetchval(
        """
        SELECT COALESCE(SUM(costo_mano_obra + propina), 0)
        FROM finanzas.planilla_ingresos
        WHERE empleado_id = $1 AND liquidacion_id IS NULL AND estado_pago <> 'cancelada'
        """,
        empleado_id,
    )
    ya = await conn.fetchval(
        """
        SELECT COALESCE(SUM(monto), 0) FROM finanzas.adelantos
        WHERE empleado_id = $1 AND estado = 'aprobado' AND liquidacion_id IS NULL
        """,
        empleado_id,
    )
    disponible = decimal(ganado) - decimal(ya)
    tope = disponible * pdec(params, "adelanto_tope_porcentaje")
    if monto <= 0 or monto > tope:
        raise ApiError(422, "ADELANTO", "El adelanto supera el tope de lo ya ganado.")
    comision = monto * pdec(params, "adelanto_comision")
    return await conn.fetchrow(
        """
        INSERT INTO finanzas.adelantos (empleado_id, monto, comision, neto)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        empleado_id,
        monto,
        comision,
        monto - comision,
    )
