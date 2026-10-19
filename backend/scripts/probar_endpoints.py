"""Recorre los endpoints de /api/v1 y deja datos en cada flujo de los requerimientos.

Usa las cuentas que ya cargó scripts/cargar_flujo.py y suma las que faltan
para cancelar, disputar, garantizar, cambiar turno y liquidar sin tocar
la orden confirmada de Ana ni la calificada de Luis.
"""

from datetime import date

import httpx

BASE = "http://127.0.0.1:8001/api/v1"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x00\x03\x00\x01\x00\x05\xfe\xd4\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)
HECHOS: list[str] = []


def pedir(cliente, metodo, path, token=None, aceptar=(), **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    respuesta = cliente.request(metodo, f"{BASE}{path}", headers=headers, **kwargs)
    if respuesta.status_code >= 400 and respuesta.status_code not in aceptar:
        raise SystemExit(f"{metodo} {path} -> {respuesta.status_code}\n{respuesta.text}")
    HECHOS.append(f"{metodo} {path} {respuesta.status_code}")
    tipo = respuesta.headers.get("content-type", "")
    if "application/json" in tipo:
        cuerpo = respuesta.json()
    else:
        cuerpo = respuesta.content
    if aceptar:
        return respuesta.status_code, cuerpo
    return cuerpo


def entrar(cliente, cedula, password, movil=False):
    headers = {"X-Client": "mobile"} if movil else {}
    return pedir(cliente, "POST", "/auth/login", headers=headers, json={"cedula": cedula, "password": password})


def sesion_propia(cliente, cedula, inicial, nueva):
    prueba = cliente.post(f"{BASE}/auth/login", json={"cedula": cedula, "password": nueva})
    if prueba.status_code == 200 and not prueba.json().get("debe_cambiar_password"):
        HECHOS.append("POST /auth/login 200")
        return prueba.json()["access_token"]
    primera = entrar(cliente, cedula, inicial)
    sesion = pedir(
        cliente,
        "POST",
        "/auth/cambiar-password",
        primera["access_token"],
        json={"password_actual": inicial, "password_nueva": nueva, "confirmar": nueva},
    )
    return sesion["access_token"]


def asignar(cliente, token_admin, token_empleado, orden, empleado_id, fecha):
    if orden["estado"] == "EN_REVISION":
        pedir(
            cliente,
            "POST",
            f"/admin/ordenes/{orden['id']}/asignar",
            token_admin,
            json={"empleado_id": empleado_id},
        )
    ofertas = pedir(cliente, "GET", "/empleado/ofertas", token_empleado)
    oferta = next(item for item in ofertas if str(item["fecha"]).startswith(fecha))
    pedir(cliente, "POST", f"/empleado/ofertas/{oferta['id']}/aceptar", token_empleado)
    return oferta["id"]


def crear_orden(cliente, token, hogar, limpieza, direccion_id, fecha, hora, tipo_pago):
    presupuesto = pedir(
        cliente,
        "POST",
        "/presupuesto",
        token,
        json={
            "tipo_hogar_id": hogar,
            "tipo_limpieza_id": limpieza,
            "direccion_id": direccion_id,
            "fecha": fecha,
            "hora_inicio": hora,
        },
    )
    monto = presupuesto["total"] if tipo_pago == "total" else presupuesto["anticipo"]
    orden = pedir(
        cliente,
        "POST",
        "/ordenes",
        token,
        headers={"Idempotency-Key": f"cobertura-{fecha}-{hora}-{tipo_pago}"},
        json={
            "tipo_hogar_id": hogar,
            "tipo_limpieza_id": limpieza,
            "direccion_id": direccion_id,
            "fecha": fecha,
            "hora_inicio": hora,
            "pago": {"medio": "transferencia", "monto": monto, "tipo": tipo_pago},
        },
    )
    return orden, presupuesto


def dejar_en_curso(cliente, token_empleado, orden_id, manual=False):
    pedir(cliente, "POST", f"/ordenes/{orden_id}/en-camino", token_empleado)
    pedir(
        cliente,
        "POST",
        "/empleado/ubicacion",
        token_empleado,
        json={"lat": -25.285, "lng": -57.62, "orden_id": orden_id},
    )
    if manual:
        archivo = pedir(
            cliente,
            "POST",
            "/archivos",
            token_empleado,
            files={"archivo": ("llegada.png", PNG, "image/png")},
            data={"carpeta": "ordenes"},
        )
        return pedir(
            cliente,
            "POST",
            f"/ordenes/{orden_id}/check-in",
            token_empleado,
            json={"lat": -25.40, "lng": -57.40, "manual": True, "nota": "El pin del mapa quedó corrido.", "foto_archivo_id": archivo["id"]},
        )
    return pedir(
        cliente,
        "POST",
        f"/ordenes/{orden_id}/check-in",
        token_empleado,
        headers={"Idempotency-Key": f"checkin-{orden_id}"},
        json={"lat": -25.28, "lng": -57.63},
    )


def cerrar(cliente, token_cliente, token_empleado, orden_id):
    detalle = pedir(cliente, "GET", f"/ordenes/{orden_id}", token_cliente)
    for tarea in detalle["tareas"]:
        pedir(
            cliente,
            "PATCH",
            f"/ordenes/{orden_id}/tareas/{tarea['id']}",
            token_empleado,
            json={"completada": True, "nota": "Listo"},
        )
    archivo = pedir(
        cliente,
        "POST",
        "/archivos",
        token_empleado,
        files={"archivo": ("despues.png", PNG, "image/png")},
        data={"carpeta": "ordenes"},
    )
    pedir(cliente, "GET", f"/archivos/{archivo['id']}", token_empleado)
    pedir(
        cliente,
        "POST",
        f"/ordenes/{orden_id}/fotos",
        token_empleado,
        params={"tipo": "despues", "archivo_id": archivo["id"]},
    )
    antes = pedir(
        cliente,
        "POST",
        "/archivos",
        token_empleado,
        files={"archivo": ("antes.png", PNG, "image/png")},
        data={"carpeta": "ordenes"},
    )
    pedir(
        cliente,
        "POST",
        f"/ordenes/{orden_id}/fotos",
        token_empleado,
        params={"tipo": "antes", "archivo_id": antes["id"]},
    )
    return pedir(cliente, "POST", f"/ordenes/{orden_id}/check-out", token_empleado, json={"lat": -25.28, "lng": -57.63})


def main() -> None:
    with httpx.Client(trust_env=False, timeout=60) as cliente:
        admin = entrar(cliente, "80000001", "Admin#2026", movil=True)
        token_admin = admin["access_token"]
        token_ana = sesion_propia(cliente, "40000001", "40000001", "Cliente#2026")
        token_luis = sesion_propia(cliente, "40000002", "Cliente#2026", "Cliente#2026")
        token_rosa = sesion_propia(cliente, "50000001", "50000001", "Empleado#2026")

        pedir(cliente, "GET", "/auth/yo", token_admin)
        pedir(cliente, "POST", "/auth/refresh", json={"refresh_token": admin["refresh_token"]}, headers={"X-Client": "mobile"})
        sesiones = pedir(cliente, "GET", "/auth/sesiones", token_admin)
        if len(sesiones) > 1:
            pedir(cliente, "DELETE", f"/auth/sesiones/{sesiones[-1]['id']}", token_admin)

        servicios = pedir(cliente, "GET", "/publico/servicios")
        hogar = next(item["id"] for item in servicios["hogares"] if item["codigo"] == "casa_mediana")
        limpieza = next(item["id"] for item in servicios["limpiezas"] if item["codigo"] == "regular")
        elemento = servicios["elementos"][0]["id"]
        zonas = pedir(cliente, "GET", "/publico/zonas")
        zona_ids = [zona["id"] for zona in zonas]
        rosa_id = next(fila["id"] for fila in pedir(cliente, "GET", "/admin/empleados", token_admin) if fila["cedula"] == "50000001")
        dir_ana = pedir(cliente, "GET", "/direcciones", token_ana)[0]["id"]
        dir_luis = pedir(cliente, "GET", "/direcciones", token_luis)[0]["id"]

        for path in (
            "/publico/tema",
            "/publico/contenido",
            "/publico/combos",
            "/publico/ofertas",
            "/publico/faq",
            "/publico/testimonios",
            "/publico/fecha",
            "/publico/legal/terminos",
            "/publico/legal/privacidad",
            "/publico/legal/cancelacion_texto",
        ):
            pedir(cliente, "GET", path)
        pedir(cliente, "GET", "/publico/cobertura", params={"lat": -25.28, "lng": -57.63})
        pedir(
            cliente,
            "POST",
            "/publico/cotizar",
            json={"tipo_hogar_id": hogar, "tipo_limpieza_id": limpieza, "lat": -25.28, "lng": -57.63, "fecha": "2026-12-02", "hora_inicio": "10:00:00"},
        )
        pedir(cliente, "POST", "/publico/coordenadas", params={"url": "https://maps.google.com/?q=-25.28,-57.63"})

        pedir(cliente, "GET", "/cliente/inicio", token_ana)
        pedir(cliente, "GET", "/cliente/perfil", token_ana)
        pedir(cliente, "PUT", "/cliente/perfil", token_ana, json={"idioma": "es", "correo": "ana@example.com"})
        pedir(cliente, "GET", "/cliente/fiscal", token_ana)
        pedir(cliente, "PUT", "/cliente/fiscal", token_ana, json={"ruc": "40000001-0", "razon_social": "Ana Benítez"})
        pedir(cliente, "GET", "/disponibilidad", token_ana, params={"fecha": "2026-12-16", "horas": 4, "lat": -25.28, "lng": -57.63})
        otra = pedir(
            cliente,
            "POST",
            "/direcciones",
            token_luis,
            json={"apodo": "Trabajo", "direccion": "Av. Mariscal López 100", "lat": -25.29, "lng": -57.60, "es_principal": False},
        )
        pedir(
            cliente,
            "PATCH",
            f"/direcciones/{otra['id']}",
            token_luis,
            json={"apodo": "Oficina", "direccion": "Av. Mariscal López 100", "lat": -25.29, "lng": -57.60, "piso": "3", "instrucciones": "Portería"},
        )
        pedir(cliente, "DELETE", f"/direcciones/{otra['id']}", token_luis)

        codigo_tel = pedir(cliente, "POST", "/auth/telefono/enviar", token_ana)
        pedir(cliente, "POST", "/auth/telefono/verificar", token_ana, json={"codigo": codigo_tel["debug_codigo"]})
        pedir(cliente, "POST", "/auth/webauthn/registro/opciones", token_ana)
        pedir(cliente, "POST", "/auth/webauthn/login/opciones", aceptar=(422,), json={"cedula": "40000001"})
        pedir(cliente, "POST", "/auth/webauthn/registro", token_ana, aceptar=(422,), json={"credential": {}, "dispositivo": "prueba"})
        pedir(cliente, "POST", "/auth/webauthn/login", aceptar=(401, 422), json={"cedula": "40000001", "credential": {}})

        pedir(
            cliente,
            "PUT",
            "/ordenes/borrador",
            token_luis,
            json={"tipo_hogar_id": hogar, "tipo_limpieza_id": limpieza, "direccion_id": dir_luis, "notas": "Borrador para retomar", "elementos": [{"elemento_id": elemento, "cantidad": 1}]},
        )
        luis_ordenes = pedir(cliente, "GET", "/ordenes", token_luis)
        calificada = next(item for item in luis_ordenes["items"] if item["estado"] == "CALIFICADA")
        pedir(cliente, "POST", f"/ordenes/{calificada['id']}/repetir", token_luis)
        pedir(cliente, "POST", f"/ordenes/{calificada['id']}/conformidad", token_luis)
        pedir(cliente, "POST", f"/ordenes/{calificada['id']}/mensajes", token_luis, json={"texto": "Gracias, quedó muy bien."})
        pedir(cliente, "GET", f"/ordenes/{calificada['id']}/mensajes", token_rosa)
        pedir(
            cliente,
            "POST",
            f"/empleado/ordenes/{calificada['id']}/calificar-cliente",
            token_rosa,
            json={"puntaje": 5, "comentario": "La casa estaba despejada.", "categorias": {"acceso": 5}},
        )
        enlace = pedir(cliente, "POST", f"/ordenes/{calificada['id']}/compartir", token_luis)
        pedir(cliente, "GET", f"/publico/seguimiento/{enlace['token']}")
        pedir(cliente, "GET", f"/ordenes/{calificada['id']}/seguimiento", token_luis)
        facturas = pedir(cliente, "GET", "/cliente/facturas", token_luis)
        pedir(cliente, "GET", f"/cliente/facturas/{facturas[0]['id']}", token_luis)
        pedir(cliente, "GET", f"/cliente/facturas/{facturas[0]['id']}/pdf", token_luis)
        pedir(cliente, "POST", f"/admin/facturas/{facturas[0]['id']}/emitir", token_admin, params={"cdc": "01800000000000000000000000000000000000000000"})
        pedir(cliente, "GET", "/cliente/puntos", token_luis)
        pedir(cliente, "GET", "/cliente/referidos", token_luis)
        pedir(cliente, "GET", "/cliente/notificaciones", token_ana)
        pedir(cliente, "POST", "/cliente/notificaciones/leer-todas", token_ana)
        pedir(cliente, "PUT", "/cliente/notificaciones/preferencias", token_ana, json={"push": True, "whatsapp": True, "sms": False, "correo": True})
        pedir(
            cliente,
            "POST",
            "/notificaciones/suscripcion",
            token_ana,
            json={"endpoint": "https://push.example.com/ana", "p256dh": "clave-p256", "auth": "auth-ana"},
        )
        ticket = pedir(cliente, "POST", "/soporte/tickets", token_ana, json={"asunto": "Consulta de horario", "texto": "¿Puedo pasar el servicio a la tarde?"})
        pedir(cliente, "GET", "/soporte/tickets", token_ana)
        pedir(cliente, "POST", f"/soporte/tickets/{ticket['id']}/mensajes", token_ana, json={"texto": "Si se puede el jueves, mejor."})
        plan = pedir(
            cliente,
            "POST",
            "/cliente/planes",
            token_ana,
            json={
                "direccion_id": dir_ana,
                "tipo_hogar_id": hogar,
                "tipo_limpieza_id": limpieza,
                "frecuencia": "semanal",
                "dia_semana": 3,
                "hora_inicio": "09:00:00",
                "empleado_fijo_id": rosa_id,
                "elementos": [{"elemento_id": elemento, "cantidad": 1}],
            },
        )
        pedir(cliente, "GET", "/cliente/planes", token_ana)
        pedir(cliente, "PATCH", f"/cliente/planes/{plan['id']}", token_ana, params={"estado": "pausado"})

        estado_mario, _ = pedir(
            cliente,
            "POST",
            "/admin/empleados",
            token_admin,
            aceptar=(409,),
            json={"nombre": "Mario", "apellido": "Acosta", "cedula": "50000003", "telefono": "595981000004", "correo": "mario@example.com", "zonas": zona_ids},
        )
        token_mario = sesion_propia(cliente, "50000003", "50000003", "Empleado#2026")
        if estado_mario != 409:
            pedir(
                cliente,
                "PUT",
                "/empleado/horarios",
                token_mario,
                json=[{"dia_semana": dia, "hora_desde": "07:00:00", "hora_hasta": "18:00:00"} for dia in range(1, 7)],
            )
        else:
            horarios = pedir(cliente, "GET", "/empleado/inicio", token_mario)
            if not horarios.get("ofertas") and True:
                pedir(
                    cliente,
                    "PUT",
                    "/empleado/horarios",
                    token_mario,
                    json=[{"dia_semana": dia, "hora_desde": "07:00:00", "hora_hasta": "18:00:00"} for dia in range(1, 7)],
                )
        for curso in pedir(cliente, "GET", "/empleado/capacitaciones", token_mario):
            if curso["obligatoria"] and not curso["completada"]:
                pedir(cliente, "POST", f"/empleado/capacitaciones/{curso['id']}/completar", token_mario)
        pedir(cliente, "PATCH", "/empleado/disponibilidad", token_mario, params={"disponible": True})
        mario_id = next(fila["id"] for fila in pedir(cliente, "GET", "/admin/empleados", token_admin) if fila["cedula"] == "50000003")
        tipos = [item["id"] for item in servicios["limpiezas"][:2]]
        pedir(cliente, "PUT", "/empleado/preferencias", token_mario, json={"distancia_max_km": 15, "tipos_preferidos": tipos, "disponible": True})

        garantia, _ = crear_orden(cliente, token_luis, hogar, limpieza, dir_luis, "2026-12-01", "08:00:00", "total")
        asignar(cliente, token_admin, token_rosa, garantia, rosa_id, "2026-12-01")
        dejar_en_curso(cliente, token_rosa, garantia["id"])
        extra = pedir(cliente, "POST", f"/ordenes/{garantia['id']}/extra-tiempo", token_rosa, json={"minutos": 30, "motivo": "El horno pidió más trabajo."})
        pedir(cliente, "POST", f"/ordenes/{garantia['id']}/extra-tiempo/{extra['id']}/responder", token_luis, json={"aprobar": True})
        cerrar(cliente, token_luis, token_rosa, garantia["id"])
        pedir(cliente, "POST", f"/ordenes/{garantia['id']}/calificar", token_luis, json={"puntaje": 2, "comentario": "Quedó polvo en los zócalos.", "categorias": {"limpieza": 2}})
        pedir(cliente, "POST", f"/ordenes/{garantia['id']}/garantia", token_luis)

        disputa, _ = crear_orden(cliente, token_ana, hogar, limpieza, dir_ana, "2026-12-02", "13:00:00", "total")
        asignar(cliente, token_admin, token_rosa, disputa, rosa_id, "2026-12-02")
        dejar_en_curso(cliente, token_rosa, disputa["id"])
        cerrar(cliente, token_ana, token_rosa, disputa["id"])
        incidente = pedir(cliente, "POST", f"/ordenes/{disputa['id']}/problemas", token_ana, json={"tipo": "resultado", "descripcion": "La cocina no quedó como acordamos."})
        pedir(cliente, "GET", "/admin/incidentes", token_admin)
        pedir(
            cliente,
            "POST",
            f"/admin/incidentes/{incidente['id']}/resolver",
            token_admin,
            json={"estado": "resuelto", "resolucion": "Se ofrece una re-limpieza de la cocina.", "monto": 0},
        )

        manual, _ = crear_orden(cliente, token_luis, hogar, limpieza, dir_luis, "2026-12-03", "08:00:00", "total")
        asignar(cliente, token_admin, token_rosa, manual, rosa_id, "2026-12-03")
        llegada = dejar_en_curso(cliente, token_rosa, manual["id"], manual=True)
        pedir(cliente, "POST", f"/admin/llegadas/{llegada['id']}/revisar", token_admin, params={"aprobar": True})
        cerrar(cliente, token_luis, token_rosa, manual["id"])

        cancela_emp, _ = crear_orden(cliente, token_ana, hogar, limpieza, dir_ana, "2026-12-04", "13:00:00", "total")
        asignar(cliente, token_admin, token_rosa, cancela_emp, rosa_id, "2026-12-04")
        pedir(cliente, "POST", f"/empleado/ordenes/{cancela_emp['id']}/cancelar", token_rosa, json={"motivo": "Me sentí mal y no llego."})

        rechazo, _ = crear_orden(cliente, token_luis, hogar, limpieza, dir_luis, "2026-12-07", "08:00:00", "total")
        if rechazo["estado"] == "EN_REVISION":
            pedir(cliente, "POST", f"/admin/ordenes/{rechazo['id']}/asignar", token_admin, json={"empleado_id": mario_id})
        oferta_mario = next(item for item in pedir(cliente, "GET", "/empleado/ofertas", token_mario) if str(item["fecha"]).startswith("2026-12-07"))
        pedir(cliente, "POST", f"/empleado/ofertas/{oferta_mario['id']}/rechazar", token_mario, json={"motivo": "Ese día tengo un trámite."})

        turno, _ = crear_orden(cliente, token_ana, hogar, limpieza, dir_ana, "2026-12-08", "13:00:00", "total")
        asignar(cliente, token_admin, token_rosa, turno, rosa_id, "2026-12-08")
        cambio = pedir(cliente, "POST", f"/empleado/ordenes/{turno['id']}/cambio-turno", token_rosa, json={"empleado_destino_id": mario_id})
        pedir(cliente, "POST", f"/admin/cambios-turno/{cambio['id']}/resolver", token_admin, params={"aprobar": True})

        cancela_cli, _ = crear_orden(cliente, token_luis, hogar, limpieza, dir_luis, "2026-12-09", "08:00:00", "total")
        pedir(cliente, "POST", f"/ordenes/{cancela_cli['id']}/cancelar", token_luis, json={"motivo": "Viajo esa semana."})

        reasigna, _ = crear_orden(cliente, token_ana, hogar, limpieza, dir_ana, "2026-12-10", "13:00:00", "total")
        asignar(cliente, token_admin, token_rosa, reasigna, rosa_id, "2026-12-10")
        pedir(cliente, "GET", f"/admin/ordenes/{reasigna['id']}/candidatos", token_admin)
        pedir(
            cliente,
            "POST",
            f"/admin/ordenes/{reasigna['id']}/reasignar",
            token_admin,
            json={"empleado_id": mario_id, "motivo": "Rosa ya tiene la mañana cargada."},
        )

        saldo, presupuesto_saldo = crear_orden(cliente, token_luis, hogar, limpieza, dir_luis, "2026-12-11", "08:00:00", "anticipo")
        pago = pedir(
            cliente,
            "POST",
            f"/ordenes/{saldo['id']}/pagos",
            token_luis,
            headers={"Idempotency-Key": f"saldo-{saldo['id']}"},
            json={"medio": "transferencia", "monto": presupuesto_saldo["total"] - presupuesto_saldo["anticipo"], "tipo": "saldo"},
        )
        pedir(cliente, "POST", f"/admin/finanzas/pagos/{pago['id']}/confirmar", token_admin)
        pedir(cliente, "POST", "/pagos/webhook", headers={"X-Webhook-Secret": "dev-webhook"}, json={"pago_id": pago["id"]})
        if saldo["estado"] == "EN_REVISION":
            pedir(cliente, "POST", f"/admin/ordenes/{saldo['id']}/asignar-auto", token_admin)

        bloqueo = pedir(
            cliente,
            "POST",
            "/empleado/bloqueos",
            token_mario,
            json={"desde": "2026-12-24T08:00:00-03:00", "hasta": "2026-12-24T12:00:00-03:00", "motivo": "Navidad"},
        )
        pedir(cliente, "DELETE", f"/empleado/bloqueos/{bloqueo['id']}", token_mario)
        pedir(cliente, "GET", "/empleado/agenda", token_rosa, params={"desde": "2026-12-01", "hasta": "2026-12-31"})
        pedir(cliente, "GET", "/empleado/inicio", token_rosa)
        pedir(cliente, "GET", "/empleado/ingresos", token_rosa)
        pedir(cliente, "GET", "/empleado/ranking", token_rosa)
        pedir(cliente, "POST", "/empleado/adelantos", token_rosa, json={"monto": 10000})
        pedir(cliente, "POST", "/empleado/sos", token_rosa, json={"lat": -25.28, "lng": -57.63})
        insumos = pedir(cliente, "GET", "/empleado/insumos", token_rosa)
        pedir(cliente, "POST", "/empleado/insumos/solicitudes", token_rosa, json={"insumo_id": insumos[0]["id"], "cantidad": 2})
        ausencia = pedir(
            cliente,
            "POST",
            "/empleado/ausencias",
            token_mario,
            json={"desde": "2027-01-04", "hasta": "2027-01-06", "motivo": "Trámite familiar"},
        )
        pedir(cliente, "POST", f"/admin/ausencias/{ausencia['id']}/resolver", token_admin, params={"aprobar": True})
        pedir(cliente, "POST", f"/admin/empleados/{mario_id}/sancionar", token_admin, json={"motivo": "Llegó tarde a una inducción.", "suspender": False})
        documento = pedir(
            cliente,
            "POST",
            "/archivos",
            token_mario,
            files={"archivo": ("antecedentes.png", PNG, "image/png")},
            data={"carpeta": "documentos"},
        )
        pedir(cliente, "POST", "/empleado/documentos", token_mario, params={"tipo": "antecedentes", "archivo_id": documento["id"]})
        pedir(cliente, "GET", "/empleado/documentos", token_mario)

        liquidacion = pedir(
            cliente,
            "POST",
            "/admin/liquidaciones/generar",
            token_admin,
            params={"empleado_id": rosa_id, "desde": "2026-11-01", "hasta": "2026-12-31"},
        )
        pedir(cliente, "POST", f"/admin/liquidaciones/{liquidacion['id']}/pagar", token_admin)
        pedir(cliente, "GET", "/empleado/liquidaciones", token_rosa)
        pedir(cliente, "GET", "/admin/finanzas/planilla", token_admin, params={"desde": "2026-11-01", "hasta": "2026-12-31"})

        pedir(cliente, "POST", "/admin/clientes", token_admin, aceptar=(409,), json={"nombre": "Eva", "apellido": "Maidana", "cedula": "40000003", "telefono": "595981000005", "correo": "eva@example.com", "direccion": "Av. España 800", "lat": -25.27, "lng": -57.62})
        token_eva = sesion_propia(cliente, "40000003", "40000003", "Cliente#2026")
        clientes = pedir(cliente, "GET", "/admin/clientes", token_admin, params={"q": "40000003"})
        eva_id = clientes[0]["id"]
        pedir(cliente, "POST", f"/admin/clientes/{eva_id}/bloquear", token_admin, params={"bloqueado": True})
        pedir(cliente, "POST", f"/admin/clientes/{eva_id}/bloquear", token_admin, params={"bloqueado": False})
        pedir(cliente, "POST", f"/admin/clientes/{eva_id}/restablecer", token_admin)
        token_eva = sesion_propia(cliente, "40000003", "40000003", "Cliente#2026")
        recuperacion = pedir(cliente, "POST", "/auth/recuperar", json={"cedula": "40000003"})
        pedir(
            cliente,
            "POST",
            "/auth/verificar-otp",
            json={"cedula": "40000003", "codigo": recuperacion["debug_codigo"], "password_nueva": "Cliente#2026"},
        )
        token_eva = entrar(cliente, "40000003", "Cliente#2026")["access_token"]
        pedir(cliente, "POST", "/auth/logout-todos", token_eva)

        pedir(cliente, "POST", "/admin/usuarios", token_admin, aceptar=(409,), json={"cedula": "80000002", "nombre": "Laura", "apellido": "Benítez", "rol": "admin"})
        token_panel = sesion_propia(cliente, "80000002", "80000002", "Admin#2026")
        pedir(cliente, "GET", "/auth/yo", token_panel)
        panel = entrar(cliente, "80000002", "Admin#2026", movil=True)
        pedir(cliente, "POST", "/auth/logout", panel["access_token"], json={"refresh_token": panel["refresh_token"]}, headers={"X-Client": "mobile"})

        pedir(cliente, "GET", "/admin/solicitudes", token_admin)
        pedir(cliente, "GET", "/admin/mapa", token_admin)
        pedir(cliente, "GET", "/admin/parametros", token_admin)
        pedir(cliente, "PUT", "/admin/parametros", token_admin, json={"clave": "whatsapp_origen", "valor": ""})
        pedir(cliente, "GET", "/admin/parametros/historial", token_admin, params={"clave": "whatsapp_origen"})
        pedir(
            cliente,
            "POST",
            "/admin/parametros/programar",
            token_admin,
            json={"clave": "numero_enmascarado", "valor": "+595211000000", "vigente_desde": "2027-06-01T00:00:00-03:00"},
        )
        combo = pedir(
            cliente,
            "POST",
            "/admin/combos",
            token_admin,
            json={"nombre": "Combo de prueba API", "descripcion": "Regular con un extra.", "tipo_precio": "porcentaje", "valor": 5, "tipo_limpieza_id": limpieza, "elementos": [elemento], "publico_web": False},
        )
        pedir(cliente, "GET", "/admin/combos", token_admin)
        pedir(cliente, "POST", f"/admin/combos/{combo['id']}/pausar", token_admin, params={"activo": False})
        pedir(cliente, "POST", "/admin/promociones/simular", token_admin, params={"combo_id": combo["id"]})
        pedir(
            cliente,
            "POST",
            "/admin/promociones",
            token_admin,
            json={"nombre": "Martes valle", "tipo": "horario_valle", "valor": 5, "es_porcentaje": True, "segmento": "todos", "acumulable": True, "prioridad": 1},
        )
        pedir(
            cliente,
            "POST",
            "/admin/cupones",
            token_admin,
            aceptar=(409,),
            json={"codigo": "PRUEBA10", "nombre": "Cupón de prueba", "es_porcentaje": True, "valor": 10, "tope": 20000, "uso_por_cliente": 1},
        )
        pedir(cliente, "POST", "/cupones/validar", token_luis, json={"codigo": "PRUEBA10", "subtotal": 180000})
        pedir(cliente, "POST", "/admin/cupones/masivo", token_admin, params={"prefijo": "QA", "cantidad": 2, "valor": 5})
        contenido = pedir(cliente, "GET", "/admin/contenido", token_admin)
        contacto = next(item["valor"] for item in contenido if item["clave"] == "contacto")
        pedir(cliente, "PUT", "/admin/contenido", token_admin, json={"clave": "contacto", "idioma": "es", "valor": contacto})
        postulaciones = pedir(cliente, "GET", "/admin/postulaciones", token_admin)
        if postulaciones:
            pedir(cliente, "PATCH", f"/admin/postulaciones/{postulaciones[0]['id']}", token_admin, params={"estado": "en_revision"})
        pedir(cliente, "GET", "/admin/reportes/kpis", token_admin)
        pedir(cliente, "GET", "/admin/reportes/ordenes", token_admin, params={"formato": "json"})
        pedir(cliente, "GET", "/admin/reportes/ordenes", token_admin, params={"formato": "csv"})
        pedir(cliente, "GET", "/admin/reportes/promociones", token_admin)
        pedir(cliente, "GET", "/admin/auditoria", token_admin)
        pedir(cliente, "GET", "/ordenes", token_ana, params={"estado": "CONFIRMADA"})

        print(f"Endpoints ejercitados: {len(HECHOS)}")
        for linea in HECHOS:
            print(" ", linea)


def cola() -> None:
    import socket
    from base64 import b64encode

    with httpx.Client(trust_env=False, timeout=60) as cliente:
        token_admin = entrar(cliente, "80000001", "Admin#2026")["access_token"]
        token_luis = sesion_propia(cliente, "40000002", "Cliente#2026", "Cliente#2026")
        token_rosa = sesion_propia(cliente, "50000001", "50000001", "Empleado#2026")
        servicios = pedir(cliente, "GET", "/publico/servicios")
        limpieza = next(item["id"] for item in servicios["limpiezas"] if item["codigo"] == "regular")
        elemento = servicios["elementos"][0]["id"]
        pedir(
            cliente,
            "POST",
            "/admin/parametros/programar",
            token_admin,
            json={"clave": "numero_enmascarado", "valor": "+595211000000", "vigente_desde": "2027-06-01T00:00:00-03:00"},
        )
        combo = pedir(
            cliente,
            "POST",
            "/admin/combos",
            token_admin,
            json={
                "nombre": "Combo de prueba API",
                "descripcion": "Regular con un extra.",
                "tipo_precio": "porcentaje",
                "valor": 5,
                "tipo_limpieza_id": limpieza,
                "elementos": [elemento],
                "publico_web": False,
            },
        )
        pedir(cliente, "GET", "/admin/combos", token_admin)
        pedir(cliente, "POST", "/admin/promociones/simular", token_admin, params={"combo_id": combo["id"]})
        pedir(cliente, "POST", f"/admin/combos/{combo['id']}/pausar", token_admin, params={"activo": False})
        pedir(
            cliente,
            "POST",
            "/admin/promociones",
            token_admin,
            json={"nombre": "Martes valle", "tipo": "horario_valle", "valor": 5, "es_porcentaje": True, "segmento": "todos", "acumulable": True, "prioridad": 1},
        )
        pedir(
            cliente,
            "POST",
            "/admin/cupones",
            token_admin,
            aceptar=(409,),
            json={"codigo": "PRUEBA10", "nombre": "Cupón de prueba", "es_porcentaje": True, "valor": 10, "tope": 20000, "uso_por_cliente": 1},
        )
        pedir(cliente, "POST", "/cupones/validar", token_luis, json={"codigo": "PRUEBA10", "subtotal": 180000})
        pedir(cliente, "POST", "/admin/cupones/masivo", token_admin, params={"prefijo": "QA", "cantidad": 2, "valor": 5})
        contenido = pedir(cliente, "GET", "/admin/contenido", token_admin)
        contacto = next(item["valor"] for item in contenido if item["clave"] == "contacto")
        pedir(cliente, "PUT", "/admin/contenido", token_admin, json={"clave": "contacto", "idioma": "es", "valor": contacto})
        postulaciones = pedir(cliente, "GET", "/admin/postulaciones", token_admin)
        if postulaciones:
            pedir(cliente, "PATCH", f"/admin/postulaciones/{postulaciones[0]['id']}", token_admin, params={"estado": "en_revision"})
        pedir(cliente, "GET", "/admin/reportes/kpis", token_admin)
        pedir(cliente, "GET", "/admin/reportes/ordenes", token_admin, params={"formato": "json"})
        pedir(cliente, "GET", "/admin/reportes/ordenes", token_admin, params={"formato": "csv"})
        pedir(cliente, "GET", "/admin/reportes/promociones", token_admin)
        pedir(cliente, "GET", "/admin/auditoria", token_admin)
        pedir(cliente, "GET", "/ordenes", token_admin and token_luis, params={"estado": "CONFIRMADA"})
        ordenes = pedir(cliente, "GET", "/ordenes", token_luis)
        orden_id = ordenes["items"][0]["id"]
        for ruta, token in (
            (f"/api/v1/ws/ordenes/{orden_id}", token_luis),
            ("/api/v1/ws/admin/mapa", token_admin),
            ("/api/v1/ws/empleado", token_rosa),
        ):
            sock = socket.create_connection(("127.0.0.1", 8001), timeout=5)
            clave = b64encode(b"limsys-prueba-ws").decode()
            pedido = (
                f"GET {ruta}?token={token} HTTP/1.1\r\n"
                "Host: 127.0.0.1:8001\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {clave}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(pedido.encode())
            respuesta = sock.recv(1024)
            sock.close()
            if b"101" not in respuesta:
                raise SystemExit(f"WebSocket {ruta} no abrió: {respuesta[:200]!r}")
            HECHOS.append(f"WS {ruta} 101")
    print(f"Cierre de cobertura: {len(HECHOS)} llamadas")
    for linea in HECHOS:
        print(" ", linea)


if __name__ == "__main__":
    import sys

    cola() if len(sys.argv) > 1 and sys.argv[1] == "cola" else main()
