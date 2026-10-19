from datetime import date, datetime, time, timedelta
from decimal import Decimal

from app.core.errors import ApiError
from app.core.util import ahora, decimal, rec
from app.domain.geo import elegir_zona
from app.domain.precios import DescuentoPedido, calcular_precio
from app.services.parametros import obtener_parametros, pdec, pent

INTERNOS = {"costo_mano_obra", "cargas", "insumos", "costo_total", "margen", "margen_pct", "costo_hora"}


def _pct(valor) -> Decimal:
    return decimal(valor) / Decimal(100)


def _num(valor: Decimal):
    integral = valor.to_integral_value()
    return int(integral) if valor == integral else float(valor)


def _cubre(franja, dia: date, hora: time) -> bool:
    if dia.isoweekday() not in list(franja["dias"]):
        return False
    desde, hasta = franja["hora_desde"], franja["hora_hasta"]
    if isinstance(hora, datetime):
        hora = hora.time()
    if desde <= hasta:
        return desde <= hora < hasta
    return hora >= desde or hora < hasta


async def _zonas(conn) -> list[dict]:
    return [rec(fila) for fila in await conn.fetch("SELECT * FROM catalogo.zonas WHERE activo")]


async def resolver_zona(conn, lat: float | None, lng: float | None, zona_id: int | None = None):
    zonas = await _zonas(conn)
    if lat is not None and lng is not None:
        return elegir_zona(lat, lng, zonas)
    if zona_id is not None:
        return next((zona for zona in zonas if zona["id"] == zona_id), None)
    return None


async def multiplicador_horario(conn, dia: date | None, hora: time | None, params: dict) -> tuple[Decimal, str | None]:
    if dia is None or hora is None:
        return Decimal(1), None
    feriado = await conn.fetchrow("SELECT nombre, recargo FROM catalogo.feriados WHERE fecha = $1", dia)
    if feriado:
        recargo = feriado["recargo"] if feriado["recargo"] is not None else pdec(params, "recargo_feriado")
        return decimal(recargo), "feriado"
    franjas = await conn.fetch("SELECT * FROM catalogo.franjas WHERE activo")
    for franja in franjas:
        if franja["tipo"] == "nocturno" and _cubre(franja, dia, hora):
            return decimal(franja["valor"]), "nocturno"
    if dia.isoweekday() >= 6:
        return pdec(params, "recargo_fin_semana"), "fin_semana"
    for franja in franjas:
        if franja["tipo"] == "pico" and _cubre(franja, dia, hora):
            return decimal(franja["valor"]), "pico"
    return Decimal(1), None


async def descuento_valle(conn, dia, hora, subtotal: Decimal) -> Decimal:
    if dia is None or hora is None or subtotal <= 0:
        return Decimal(0)
    franjas = await conn.fetch("SELECT * FROM catalogo.franjas WHERE activo AND tipo = 'valle'")
    for franja in franjas:
        if _cubre(franja, dia, hora):
            return subtotal * _pct(franja["valor"])
    return Decimal(0)


def _vigente(fila, momento) -> bool:
    if not fila["activo"]:
        return False
    if fila["vigente_desde"] and fila["vigente_desde"] > momento:
        return False
    if fila["vigente_hasta"] and fila["vigente_hasta"] < momento:
        return False
    if fila["cupo"] is not None and fila["usos"] >= fila["cupo"]:
        return False
    return True


async def _promos_aplicables(conn, cliente, subtotal, zona_id, dia, es_plan: bool) -> list[dict]:
    filas = await conn.fetch("SELECT * FROM comercial.promociones WHERE activo ORDER BY prioridad DESC, valor DESC")
    momento = ahora()
    ordenes_previas = 0
    ultima = None
    cumple = False
    if cliente is not None:
        ordenes_previas = await conn.fetchval(
            """
            SELECT count(*) FROM operacion.ordenes
            WHERE cliente_id = $1 AND estado IN ('COMPLETADA', 'PAGADA', 'CALIFICADA')
            """,
            cliente["id"],
        )
        ultima = await conn.fetchval(
            """
            SELECT max(fecha) FROM operacion.ordenes
            WHERE cliente_id = $1 AND estado IN ('COMPLETADA', 'PAGADA', 'CALIFICADA')
            """,
            cliente["id"],
        )
        if cliente["fecha_nacimiento"] and dia is not None:
            nace = cliente["fecha_nacimiento"]
            try:
                aniversario = nace.replace(year=dia.year)
            except ValueError:
                aniversario = nace.replace(year=dia.year, day=28)
            cumple = abs((dia - aniversario).days) <= 3
    candidatos = []
    for promo in filas:
        if not _vigente(promo, momento):
            continue
        if promo["minimo"] is not None and subtotal < promo["minimo"]:
            continue
        if promo["zona_id"] is not None and promo["zona_id"] != zona_id:
            continue
        tipo = promo["tipo"]
        if tipo == "primera_compra" and not (cliente is None or ordenes_previas == 0):
            continue
        if tipo == "cuarta_consecutiva":
            if cliente is None or not await _es_cuarta(conn, cliente["id"]):
                continue
        if tipo == "recurrencia" and not es_plan:
            continue
        if tipo == "cumpleanos" and not cumple:
            continue
        if tipo == "reactivacion":
            dias = int((promo["metadata"] or {}).get("dias", 45))
            if ultima is None or (dia or ahora().date()) - ultima < timedelta(days=dias):
                continue
        if tipo == "empresas" and (cliente is None or cliente["tipo"] != "empresa"):
            continue
        if tipo == "campana_zona" and promo["zona_id"] is None:
            continue
        monto = subtotal * _pct(promo["valor"]) if promo["es_porcentaje"] else decimal(promo["valor"])
        if promo["tope"] is not None:
            monto = min(monto, decimal(promo["tope"]))
        candidatos.append(
            {
                "origen": tipo,
                "nombre": promo["nombre"],
                "monto": monto,
                "acumulable": promo["acumulable"],
                "estimado": cliente is None and tipo == "primera_compra",
                "piso_absoluto": bool((promo["metadata"] or {}).get("piso_absoluto")),
            }
        )
    return candidatos


async def _es_cuarta(conn, cliente_id) -> bool:
    fechas = await conn.fetch(
        """
        SELECT fecha FROM operacion.ordenes
        WHERE cliente_id = $1
          AND estado IN ('COMPLETADA', 'PAGADA', 'CALIFICADA')
          AND fecha IS NOT NULL
        ORDER BY fecha DESC
        LIMIT 12
        """,
        cliente_id,
    )
    if not fechas:
        return False
    racha = 1
    previa = fechas[0]["fecha"]
    for fila in fechas[1:]:
        if previa - fila["fecha"] <= timedelta(days=21):
            racha += 1
            previa = fila["fecha"]
        else:
            break
    return (racha + 1) % 4 == 0


async def evaluar_cupon(conn, codigo: str, cliente_id, subtotal: Decimal) -> dict:
    cupon = await conn.fetchrow(
        "SELECT * FROM comercial.cupones WHERE upper(codigo) = upper($1)",
        codigo.strip(),
    )
    if cupon is None or not cupon["activo"]:
        raise ApiError(422, "CUPON_INVALIDO", "El cupón no aplica.")
    if cupon["vence_en"] and cupon["vence_en"] < ahora():
        raise ApiError(422, "CUPON_VENCIDO", "El cupón está vencido.")
    if cupon["uso_maximo"] is not None and cupon["usos"] >= cupon["uso_maximo"]:
        raise ApiError(422, "CUPON_AGOTADO", "El cupón ya no tiene cupo.")
    if cupon["minimo"] is not None and subtotal < cupon["minimo"]:
        raise ApiError(422, "CUPON_MINIMO", "El cupón pide un monto mínimo mayor.")
    if cliente_id is not None:
        usos = await conn.fetchval(
            "SELECT count(*) FROM comercial.cupon_usos WHERE cupon_id = $1 AND cliente_id = $2",
            cupon["id"],
            cliente_id,
        )
        if usos >= cupon["uso_por_cliente"]:
            raise ApiError(422, "CUPON_USADO", "Ya usaste este cupón.")
    monto = subtotal * _pct(cupon["valor"]) if cupon["es_porcentaje"] else decimal(cupon["valor"])
    if cupon["tope"] is not None:
        monto = min(monto, decimal(cupon["tope"]))
    return {
        "id": cupon["id"],
        "codigo": cupon["codigo"],
        "monto": monto,
        "acumulable": cupon["acumulable"],
        "nombre": cupon["nombre"],
    }


async def _combo(conn, combo_id, tipo_limpieza_id, elementos: list[dict], zona_id, tipo_hogar_id, subtotal: Decimal):
    combo = await conn.fetchrow("SELECT * FROM comercial.combos WHERE id = $1", combo_id)
    if combo is None or not _vigente(combo, ahora()):
        raise ApiError(422, "COMBO_INVALIDO", "El combo no está disponible.")
    if zona_id is not None:
        zonas = await conn.fetch("SELECT zona_id FROM comercial.combo_zonas WHERE combo_id = $1", combo_id)
        if zonas and zona_id not in {fila["zona_id"] for fila in zonas}:
            raise ApiError(422, "COMBO_ZONA", "El combo no aplica en esta zona.")
    hogares = await conn.fetch("SELECT tipo_hogar_id FROM comercial.combo_hogares WHERE combo_id = $1", combo_id)
    if hogares and tipo_hogar_id not in {fila["tipo_hogar_id"] for fila in hogares}:
        raise ApiError(422, "COMBO_HOGAR", "El combo no aplica a este tipo de hogar.")
    items = await conn.fetch("SELECT * FROM comercial.combo_items WHERE combo_id = $1", combo_id)
    tipos = {fila["tipo_limpieza_id"] for fila in items if fila["tipo_limpieza_id"]}
    requeridos = {fila["elemento_id"] for fila in items if fila["elemento_id"]}
    elegidos = {item["elemento_id"] for item in elementos}
    if tipos and tipo_limpieza_id not in tipos:
        raise ApiError(422, "COMBO_SERVICIO", "El combo es para otro tipo de limpieza.")
    if not requeridos.issubset(elegidos):
        raise ApiError(422, "COMBO_ELEMENTOS", "Faltan elementos incluidos en el combo.")
    if combo["tipo_precio"] == "porcentaje":
        monto = subtotal * _pct(combo["valor"])
    else:
        monto = max(Decimal(0), subtotal - decimal(combo["valor"]))
    return {"id": combo["id"], "nombre": combo["nombre"], "monto": monto, "acumulable": combo["acumulable"]}


async def _cliente(conn, cliente_id):
    if cliente_id is None:
        return None
    return await conn.fetchrow("SELECT * FROM crm.clientes WHERE id = $1", cliente_id)


async def _empresa(conn, cliente_id):
    if cliente_id is None:
        return None
    return await conn.fetchrow("SELECT * FROM crm.empresas WHERE cliente_id = $1 AND activo", cliente_id)


def _armar_resultado(resultado, extra: dict, con_margen: bool) -> dict:
    data = {
        "horas_est": _num(resultado.horas_est),
        "servicio": _num(resultado.subtotal - sum((decimal(e["adicional"]) * e["cantidad"] for e in extra["elementos"]), Decimal(0))),
        "elementos": extra["elementos"],
        "subtotal": _num(resultado.subtotal),
        "descuentos": _num(resultado.descuentos),
        "detalle_descuentos": [
            {**item, "monto": _num(decimal(item["monto"]))} for item in resultado.detalle_descuentos
        ],
        "recargos": _num(resultado.recargos),
        "multiplicador": _num(resultado.multiplicador),
        "motivo_recargo": extra["motivo_recargo"],
        "traslado": _num(resultado.traslado),
        "iva": _num(resultado.iva),
        "iva_incluido": True,
        "total": _num(resultado.total),
        "anticipo": _num(resultado.anticipo),
        "recortado_por_margen": resultado.recortado,
        "fuera_de_cobertura": extra["fuera_de_cobertura"],
        "zona": extra["zona"],
        "tipo_hogar": extra["tipo_hogar"],
        "tipo_limpieza": extra["tipo_limpieza"],
        "tarifa_hora": extra["tarifa_hora"],
    }
    if con_margen:
        data.update(
            {
                "costo_mano_obra": _num(resultado.costo_mano_obra),
                "cargas": _num(resultado.cargas),
                "insumos": _num(resultado.insumos),
                "costo_total": _num(resultado.costo_total),
                "margen": _num(resultado.margen),
                "margen_pct": float(resultado.margen_pct),
                "costo_hora": extra.get("costo_hora"),
            }
        )
    return data


async def _base(conn, datos: dict, cliente_id, con_margen: bool) -> dict:
    params = await obtener_parametros(conn)
    hogar = await conn.fetchrow(
        "SELECT * FROM catalogo.tipos_hogar WHERE id = $1 AND activo",
        datos["tipo_hogar_id"],
    )
    limpieza = await conn.fetchrow(
        "SELECT * FROM catalogo.tipos_limpieza WHERE id = $1 AND activo",
        datos["tipo_limpieza_id"],
    )
    if hogar is None or limpieza is None:
        raise ApiError(422, "CATALOGO", "El tipo de hogar o de limpieza no está disponible.")
    ids = [item["elemento_id"] for item in datos.get("elementos") or []]
    catalogo = {}
    if ids:
        filas = await conn.fetch(
            "SELECT * FROM catalogo.elementos WHERE id = ANY($1::smallint[]) AND activo",
            ids,
        )
        catalogo = {fila["id"]: fila for fila in filas}
        if len(catalogo) != len(set(ids)):
            raise ApiError(422, "ELEMENTO", "Hay un elemento que no está disponible.")
    elementos_in = []
    elementos_out = []
    for item in datos.get("elementos") or []:
        fila = catalogo[item["elemento_id"]]
        try:
            cantidad = int(item.get("cantidad") or 1)
        except (TypeError, ValueError) as exc:
            raise ApiError(422, "ELEMENTO", "La cantidad tiene que ser un número entero.") from exc
        if cantidad < 1 or cantidad > 30:
            raise ApiError(422, "ELEMENTO", "La cantidad de cada elemento va de 1 a 30.")
        elementos_in.append((fila["adicional"], fila["minutos"], cantidad))
        elementos_out.append(
            {
                "elemento_id": fila["id"],
                "codigo": fila["codigo"],
                "nombre": fila["nombre"],
                "cantidad": cantidad,
                "adicional": int(fila["adicional"]),
                "minutos": fila["minutos"],
            }
        )
    lat = datos.get("lat")
    lng = datos.get("lng")
    if datos.get("direccion_id") and (lat is None or lng is None):
        direccion = await conn.fetchrow("SELECT * FROM crm.direcciones WHERE id = $1", datos["direccion_id"])
        if direccion is None:
            raise ApiError(404, "DIRECCION", "No encontramos la dirección.")
        lat, lng = direccion["lat"], direccion["lng"]
    zona = await resolver_zona(conn, lat, lng, datos.get("zona_id"))
    fuera = zona is None and lat is not None and lng is not None
    traslado = decimal(zona["traslado"]) if zona else Decimal(0)
    cliente = await _cliente(conn, cliente_id)
    empresa = await _empresa(conn, cliente_id)
    tarifa = decimal(limpieza["tarifa_hora"])
    if empresa and empresa["tarifa_hora_preferencial"]:
        tarifa = decimal(empresa["tarifa_hora_preferencial"])
    dia = datos.get("fecha")
    hora = datos.get("hora_inicio")
    multi, motivo = await multiplicador_horario(conn, dia, hora, params)
    if datos.get("es_emergencia"):
        multi *= pdec(params, "recargo_emergencia")
        motivo = "emergencia"
    if datos.get("preferencia_empleado_id"):
        multi *= pdec(params, "recargo_preferencia")
        motivo = (motivo + "+preferencia") if motivo else "preferencia"
    minutos = sum(item[1] * item[2] for item in elementos_in)
    horas_previas = decimal(hogar["horas_base"]) * decimal(limpieza["factor_horas"]) + Decimal(minutos) / Decimal(60)
    if horas_previas < pdec(params, "minimo_horas"):
        horas_previas = pdec(params, "minimo_horas")
    subtotal = tarifa * horas_previas + sum((decimal(a) * c for a, _, c in elementos_in), Decimal(0))
    pedidos: list[DescuentoPedido] = []
    if datos.get("combo_id"):
        combo = await _combo(
            conn,
            datos["combo_id"],
            limpieza["id"],
            elementos_out,
            zona["id"] if zona else None,
            hogar["id"],
            subtotal,
        )
        pedidos.append(DescuentoPedido("combo", combo["monto"], combo["acumulable"], {"nombre": combo["nombre"], "combo_id": str(combo["id"])}))
    valle = await descuento_valle(conn, dia, hora, subtotal)
    automaticos = await _promos_aplicables(
        conn, cliente, subtotal, zona["id"] if zona else None, dia, bool(datos.get("es_plan"))
    )
    if valle > 0:
        automaticos.append({"origen": "horario_valle", "nombre": "Horario valle", "monto": valle, "acumulable": True, "estimado": False})
    if automaticos:
        mejor = max(automaticos, key=lambda item: item["monto"])
        pedidos.append(
            DescuentoPedido(
                mejor["origen"],
                mejor["monto"],
                mejor["acumulable"],
                {"nombre": mejor["nombre"], "estimado": mejor.get("estimado", False)},
            )
        )
    if datos.get("cupon"):
        cupon = await evaluar_cupon(conn, datos["cupon"], cliente_id, subtotal)
        pedidos.append(DescuentoPedido("cupon", cupon["monto"], cupon["acumulable"], {"codigo": cupon["codigo"], "cupon_id": str(cupon["id"])}))
    puntos = int(datos.get("puntos") or 0)
    if puntos and cliente is not None:
        if puntos > cliente["puntos_saldo"]:
            raise ApiError(422, "PUNTOS", "No tenés tantos puntos.")
        monto_puntos = Decimal(puntos) * pdec(params, "puntos_valor_gs")
        pedidos.append(DescuentoPedido("puntos", monto_puntos, True, {"puntos": puntos}))
    margen = pdec(params, "margen_minimo")
    if datos.get("usar_piso_absoluto"):
        margen = pdec(params, "margen_piso_absoluto")
    costo_hora = pdec(params, "pago_empleado_hora")
    if datos.get("preferencia_empleado_id"):
        propia = await conn.fetchval(
            "SELECT tarifa_hora FROM personal.empleados WHERE id = $1",
            datos["preferencia_empleado_id"],
        )
        if propia:
            costo_hora = decimal(propia)
    resultado = calcular_precio(
        horas_base=decimal(hogar["horas_base"]),
        factor=decimal(limpieza["factor_horas"]),
        tarifa_hora=tarifa,
        elementos=[(decimal(a), m, c) for a, m, c in elementos_in],
        traslado=traslado,
        multiplicador=multi,
        descuentos=pedidos,
        costo_hora=costo_hora,
        cargas_pct=pdec(params, "cargas_porcentaje"),
        insumos_hora=pdec(params, "insumos_por_hora"),
        margen_minimo=margen,
        tope_descuento=pdec(params, "tope_descuento_orden"),
        minimo_horas=pdec(params, "minimo_horas"),
        iva_divisor=pdec(params, "iva_divisor"),
        anticipo_pct=pdec(params, "anticipo_porcentaje"),
    )
    salida = _armar_resultado(
        resultado,
        {
            "elementos": elementos_out,
            "motivo_recargo": motivo,
            "fuera_de_cobertura": fuera,
            "zona": None if zona is None else {"id": zona["id"], "nombre": zona["nombre"], "traslado": int(decimal(zona["traslado"]))},
            "tipo_hogar": {"id": hogar["id"], "codigo": hogar["codigo"], "nombre": hogar["nombre"]},
            "tipo_limpieza": {"id": limpieza["id"], "codigo": limpieza["codigo"], "nombre": limpieza["nombre"]},
            "tarifa_hora": int(tarifa),
            "costo_hora": _num(costo_hora),
        },
        con_margen,
    )
    salida["_costo_hora"] = _num(costo_hora)
    return salida


async def cotizar(conn, datos: dict, cliente_id=None, con_margen: bool = False) -> dict:
    principal = await _base(conn, datos, cliente_id, con_margen)
    alternativas = await _alternativas(conn, datos, cliente_id, con_margen, principal)
    opciones = await _ajustar(conn, datos, cliente_id, con_margen, principal)
    principal.pop("_costo_hora", None)
    return {**principal, "alternativas": alternativas, "opciones_presupuesto": opciones}


async def _alternativas(conn, datos, cliente_id, con_margen, principal) -> list[dict]:
    tipos = await conn.fetch("SELECT id, codigo, nombre FROM catalogo.tipos_limpieza WHERE activo ORDER BY tarifa_hora")
    if not tipos:
        return []
    barato = tipos[0]
    profundo = next((tipo for tipo in tipos if tipo["codigo"] == "profunda"), tipos[-1])
    variantes = [
        ("economica", "Más económica", {**datos, "elementos": [], "combo_id": None, "cupon": None, "puntos": 0, "tipo_limpieza_id": barato["id"]}),
        ("recomendada", "Recomendada", datos),
        ("completa", "Completa", {**datos, "tipo_limpieza_id": profundo["id"], "combo_id": None}),
    ]
    salida = []
    for codigo, nombre, variante in variantes:
        try:
            cotizacion = await _base(conn, variante, cliente_id, con_margen)
        except ApiError:
            continue
        cotizacion.pop("_costo_hora", None)
        salida.append({"codigo": codigo, "nombre": nombre, "total": cotizacion["total"], "horas_est": cotizacion["horas_est"], "detalle": cotizacion})
    return salida


async def _ajustar(conn, datos, cliente_id, con_margen, principal) -> list[dict]:
    maximo = datos.get("presupuesto_maximo")
    if not maximo or principal["total"] <= int(maximo):
        return []
    opciones = []
    elementos = list(datos.get("elementos") or [])
    actual = {**datos, "elementos": elementos}
    while actual["elementos"]:
        ordenados = sorted(actual["elementos"], key=lambda item: item.get("cantidad", 1), reverse=True)
        # Quita el adicional más caro según el último detalle.
        nombres = {item["elemento_id"]: item for item in principal["elementos"]}
        ordenados = sorted(
            actual["elementos"],
            key=lambda item: nombres.get(item["elemento_id"], {}).get("adicional", 0),
            reverse=True,
        )
        quitado = ordenados[0]
        resto = [item for item in actual["elementos"] if item["elemento_id"] != quitado["elemento_id"]]
        actual = {**actual, "elementos": resto, "combo_id": None}
        cotizacion = await _base(conn, actual, cliente_id, con_margen)
        nombre = nombres.get(quitado["elemento_id"], {}).get("nombre", "un elemento")
        opciones.append(
            {
                "se_pierde": f"Sin {nombre}",
                "total": cotizacion["total"],
                "dentro_del_presupuesto": cotizacion["total"] <= int(maximo),
                "elementos": resto,
                "tipo_limpieza_id": actual["tipo_limpieza_id"],
            }
        )
        if cotizacion["total"] <= int(maximo):
            break
    regular = await conn.fetchval("SELECT id FROM catalogo.tipos_limpieza WHERE codigo = 'regular' AND activo")
    if regular and actual.get("tipo_limpieza_id") != regular:
        reducido = {**datos, "tipo_limpieza_id": regular, "elementos": [], "combo_id": None, "cupon": None, "puntos": 0}
        cotizacion = await _base(conn, reducido, cliente_id, con_margen)
        opciones.append(
            {
                "se_pierde": "Pasa a limpieza regular, sin elementos extra",
                "total": cotizacion["total"],
                "dentro_del_presupuesto": cotizacion["total"] <= int(maximo),
                "elementos": [],
                "tipo_limpieza_id": regular,
            }
        )
    return opciones


async def simular_margen(conn, combo_id) -> list[dict]:
    combo = await conn.fetchrow("SELECT * FROM comercial.combos WHERE id = $1", combo_id)
    if combo is None:
        raise ApiError(404, "NO_ENCONTRADO", "No encontramos el combo.")
    items = await conn.fetch("SELECT * FROM comercial.combo_items WHERE combo_id = $1", combo_id)
    tipo_id = next((fila["tipo_limpieza_id"] for fila in items if fila["tipo_limpieza_id"]), None)
    elementos = [
        {"elemento_id": fila["elemento_id"], "cantidad": fila["cantidad"]}
        for fila in items
        if fila["elemento_id"]
    ]
    if tipo_id is None:
        tipo_id = await conn.fetchval("SELECT id FROM catalogo.tipos_limpieza WHERE codigo = 'regular'")
    hogares = await conn.fetch("SELECT id, nombre FROM catalogo.tipos_hogar WHERE activo ORDER BY orden")
    zona = await conn.fetchrow("SELECT id FROM catalogo.zonas WHERE codigo = 'gran_asuncion'")
    filas = []
    for hogar in hogares:
        cotizacion = await cotizar(
            conn,
            {
                "tipo_hogar_id": hogar["id"],
                "tipo_limpieza_id": tipo_id,
                "elementos": elementos,
                "combo_id": combo_id,
                "zona_id": zona["id"] if zona else None,
                "lat": -25.28,
                "lng": -57.57,
            },
            con_margen=True,
        )
        filas.append(
            {
                "hogar": hogar["nombre"],
                "total": cotizacion["total"],
                "margen": cotizacion.get("margen"),
                "margen_pct": cotizacion.get("margen_pct"),
                "descuentos": cotizacion["descuentos"],
            }
        )
    return filas
