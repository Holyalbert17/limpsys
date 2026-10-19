"""Circuito QA: intenta romper la API y después recorre la jornada completa.

Crea personas nuevas en cada corrida. No toca las cuentas de demostración.
"""

import base64
import json
import secrets
from datetime import date, datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x00\x03\x00\x01\x00\x05\xfe\xd4\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


def dias_habiles(cantidad: int, desde_hoy: int = 21) -> list[str]:
    dia = date.today() + timedelta(days=desde_hoy)
    salida = []
    while len(salida) < cantidad:
        if dia.isoweekday() != 7:
            salida.append(dia.isoformat())
        dia += timedelta(days=1)
    return salida


def codigo(respuesta):
    detalle = respuesta.json().get("detail")
    if isinstance(detalle, dict):
        return detalle.get("code")
    return None


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def entrar(cliente, cedula, password):
    respuesta = cliente.post("/api/v1/auth/login", json={"cedula": cedula, "password": password})
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def cambiar_clave(cliente, token, actual, nueva):
    respuesta = cliente.post(
        "/api/v1/auth/cambiar-password",
        json={"password_actual": actual, "password_nueva": nueva, "confirmar": nueva},
        headers=auth(token),
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["access_token"]


def alta_cliente(cliente, token_admin, cedula):
    respuesta = cliente.post(
        "/api/v1/admin/clientes",
        json={
            "nombre": "QA",
            "apellido": "Cliente",
            "cedula": cedula,
            "telefono": "595981000111",
            "correo": f"qa{cedula}@example.com",
            "direccion": "Av. España 123, Asunción",
            "lat": -25.28,
            "lng": -57.63,
        },
        headers=auth(token_admin),
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def alta_empleado(cliente, token_admin, cedula, zonas):
    respuesta = cliente.post(
        "/api/v1/admin/empleados",
        json={
            "nombre": "QA",
            "apellido": "Empleado",
            "cedula": cedula,
            "telefono": "595981000222",
            "zonas": zonas,
        },
        headers=auth(token_admin),
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def preparar_empleado(cliente, token):
    horarios = cliente.put(
        "/api/v1/empleado/horarios",
        json=[{"dia_semana": dia, "hora_desde": "07:00:00", "hora_hasta": "18:00:00"} for dia in range(1, 7)],
        headers=auth(token),
    )
    assert horarios.status_code == 200, horarios.text
    cursos = cliente.get("/api/v1/empleado/capacitaciones", headers=auth(token))
    assert cursos.status_code == 200, cursos.text
    for curso in cursos.json():
        if curso["obligatoria"] and not curso["completada"]:
            hecho = cliente.post(
                f"/api/v1/empleado/capacitaciones/{curso['id']}/completar",
                headers=auth(token),
            )
            assert hecho.status_code == 200, hecho.text


def token_falso(sub, rol):
    return jwt.encode(
        {
            "sub": sub,
            "rol": rol,
            "typ": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def token_sin_firma(sub):
    def parte(valor):
        return base64.urlsafe_b64encode(json.dumps(valor).encode()).rstrip(b"=").decode()

    cabecera = parte({"alg": "none", "typ": "JWT"})
    cuerpo = parte({"sub": sub, "rol": "superadmin", "typ": "access"})
    return f"{cabecera}.{cuerpo}."


def pedir_orden(cliente, token, hogar, limpieza, direccion_id, fecha, total, clave=None):
    cuerpo = {
        "tipo_hogar_id": hogar,
        "tipo_limpieza_id": limpieza,
        "direccion_id": direccion_id,
        "fecha": fecha,
        "hora_inicio": "08:00:00",
        "pago": {"medio": "transferencia", "monto": total, "tipo": "total"},
    }
    cabeceras = auth(token)
    if clave:
        cabeceras["Idempotency-Key"] = clave
    return cliente.post("/api/v1/ordenes", json=cuerpo, headers=cabeceras), cuerpo


def precio(cliente, token, hogar, limpieza, direccion_id, fecha):
    respuesta = cliente.post(
        "/api/v1/presupuesto",
        json={
            "tipo_hogar_id": hogar,
            "tipo_limpieza_id": limpieza,
            "direccion_id": direccion_id,
            "fecha": fecha,
            "hora_inicio": "08:00:00",
        },
        headers=auth(token),
    )
    assert respuesta.status_code == 200, respuesta.text
    assert "margen" not in respuesta.json()
    assert respuesta.json()["total"] > 0
    return respuesta.json()["total"]


def asignar_y_aceptar(cliente, token_admin, token_empleado, orden_id, empleado_id):
    asignada = cliente.post(
        f"/api/v1/admin/ordenes/{orden_id}/asignar",
        json={"empleado_id": empleado_id, "motivo": "Circuito QA"},
        headers=auth(token_admin),
    )
    assert asignada.status_code == 200, asignada.text
    ofertas = cliente.get("/api/v1/empleado/ofertas", headers=auth(token_empleado))
    assert ofertas.status_code == 200 and ofertas.json(), ofertas.text
    aceptada = cliente.post(
        f"/api/v1/empleado/ofertas/{ofertas.json()[0]['id']}/aceptar",
        headers=auth(token_empleado),
    )
    assert aceptada.status_code == 200, aceptada.text
    assert aceptada.json()["estado"] == "CONFIRMADA"


def test_circuito_qa_agresivo():
    fecha_ok, fecha_cancel, fecha_garantia, fecha_nueva = dias_habiles(4)
    with TestClient(app) as cliente:
        salud = cliente.get("/api/v1/salud")
        assert salud.status_code == 200 and salud.json()["ok"] is True

        servicios = cliente.get("/api/v1/publico/servicios").json()
        hogar = next(item["id"] for item in servicios["hogares"] if item["codigo"] == "casa_mediana")
        limpieza = next(item["id"] for item in servicios["limpiezas"] if item["codigo"] == "regular")
        elemento = servicios["elementos"][0]["id"]
        zonas = [zona["id"] for zona in cliente.get("/api/v1/publico/zonas").json()]

        # --- Ataques sin sesión y con datos rotos ---
        assert cliente.get("/api/v1/ordenes").status_code == 401
        assert cliente.get("/api/v1/admin/clientes").status_code == 401
        assert cliente.get("/api/v1/ordenes", params={"page": 0}).status_code in {401, 422}

        sql = cliente.post("/api/v1/auth/login", json={"cedula": "80000001' OR '1'='1", "password": "x"})
        assert sql.status_code == 401 and codigo(sql) == "CREDENCIALES"
        assert cliente.post("/api/v1/auth/login", json={}).status_code == 422

        negativo = cliente.post(
            "/api/v1/publico/cotizar",
            json={
                "tipo_hogar_id": hogar,
                "tipo_limpieza_id": limpieza,
                "elementos": [{"elemento_id": elemento, "cantidad": -50}],
                "lat": -25.28,
                "lng": -57.63,
            },
        )
        assert negativo.status_code == 422 and codigo(negativo) == "ELEMENTO"
        raro = cliente.post(
            "/api/v1/publico/cotizar",
            json={
                "tipo_hogar_id": hogar,
                "tipo_limpieza_id": limpieza,
                "elementos": [{"elemento_id": elemento, "cantidad": "muchos"}],
                "lat": -25.28,
                "lng": -57.63,
            },
        )
        assert raro.status_code == 422 and codigo(raro) == "ELEMENTO"
        lejos_mapa = cliente.post(
            "/api/v1/publico/cotizar",
            json={"tipo_hogar_id": hogar, "tipo_limpieza_id": limpieza, "lat": 999, "lng": 999},
        )
        assert lejos_mapa.status_code == 200 and lejos_mapa.json()["fuera_de_cobertura"] is True
        assert "margen" not in lejos_mapa.json()

        assert cliente.get("/api/v1/publico/cobertura").status_code == 422
        assert cliente.get("/api/v1/publico/seguimiento/no-existe").status_code == 404
        registro = cliente.post(
            "/api/v1/publico/registro",
            json={
                "nombre": "QA",
                "apellido": "Roto",
                "cedula": "42999991",
                "telefono": "595981000000",
                "correo": "roto@example.com",
                "password": "Clave#2026",
                "acepta_terminos": False,
            },
        )
        assert registro.status_code == 422 and codigo(registro) == "TERMINOS"
        debil = cliente.post(
            "/api/v1/publico/registro",
            json={
                "nombre": "QA",
                "apellido": "Debil",
                "cedula": "42999992",
                "telefono": "595981000000",
                "correo": "debil@example.com",
                "password": "123",
                "acepta_terminos": True,
            },
        )
        assert debil.status_code == 422 and codigo(debil) == "PASSWORD_DEBIL"

        webhook = cliente.post("/api/v1/pagos/webhook", json={"pago_id": "00000000-0000-0000-0000-000000000001"})
        assert webhook.status_code == 401 and codigo(webhook) == "WEBHOOK"
        basura = cliente.post(
            "/api/v1/pagos/webhook",
            content=b"no-es-json",
            headers={"X-Webhook-Secret": settings.webhook_secret, "Content-Type": "application/json"},
        )
        assert basura.status_code == 422 and codigo(basura) == "DATOS_INVALIDOS"
        inexistente = cliente.post(
            "/api/v1/pagos/webhook",
            json={"pago_id": "00000000-0000-0000-0000-000000000001"},
            headers={"X-Webhook-Secret": settings.webhook_secret},
        )
        assert inexistente.status_code == 404

        ninguno = cliente.get(
            "/api/v1/admin/clientes",
            headers=auth(token_sin_firma("00000000-0000-0000-0000-000000000001")),
        )
        assert ninguno.status_code == 401

        # --- Personas del circuito ---
        admin = entrar(cliente, "80000001", "Admin#2026")
        token_admin = admin["access_token"]
        cedula_a = str(42000000 + secrets.randbelow(899999))
        cedula_b = str(42000000 + secrets.randbelow(899999))
        while cedula_b == cedula_a:
            cedula_b = str(42000000 + secrets.randbelow(899999))
        cedula_emp = str(52000000 + secrets.randbelow(899999))
        cedula_intruso = str(52000000 + secrets.randbelow(899999))
        while cedula_intruso == cedula_emp:
            cedula_intruso = str(52000000 + secrets.randbelow(899999))
        cedula_panel = str(82000000 + secrets.randbelow(899999))

        alta_cliente(cliente, token_admin, cedula_a)
        cliente_b = alta_cliente(cliente, token_admin, cedula_b)
        empleado = alta_empleado(cliente, token_admin, cedula_emp, zonas)
        intruso = alta_empleado(cliente, token_admin, cedula_intruso, zonas)
        panel = cliente.post(
            "/api/v1/admin/usuarios",
            json={"cedula": cedula_panel, "nombre": "QA", "apellido": "Panel", "rol": "admin"},
            headers=auth(token_admin),
        )
        assert panel.status_code == 200, panel.text

        sesion_a = entrar(cliente, cedula_a, cedula_a)
        assert sesion_a["debe_cambiar_password"] is True
        bloqueado = cliente.get("/api/v1/cliente/inicio", headers=auth(sesion_a["access_token"]))
        assert bloqueado.status_code == 403 and codigo(bloqueado) == "PASSWORD_CHANGE_REQUIRED"
        token_a = cambiar_clave(cliente, sesion_a["access_token"], cedula_a, "Cliente#2026")
        token_b = cambiar_clave(cliente, entrar(cliente, cedula_b, cedula_b)["access_token"], cedula_b, "Cliente#2026")
        token_emp = cambiar_clave(
            cliente, entrar(cliente, cedula_emp, cedula_emp)["access_token"], cedula_emp, "Empleado#2026"
        )
        token_intruso = cambiar_clave(
            cliente,
            entrar(cliente, cedula_intruso, cedula_intruso)["access_token"],
            cedula_intruso,
            "Empleado#2026",
        )
        token_panel = cambiar_clave(
            cliente, entrar(cliente, cedula_panel, cedula_panel)["access_token"], cedula_panel, "Admin#2026"
        )
        preparar_empleado(cliente, token_emp)
        preparar_empleado(cliente, token_intruso)
        empleado_id = empleado["empleado_id"]

        # El rol del token no manda: se lee de la base.
        elevado = cliente.get(
            "/api/v1/admin/clientes",
            headers=auth(token_falso(sesion_a["usuario"]["id"], "superadmin")),
        )
        assert elevado.status_code == 403 and codigo(elevado) == "PROHIBIDO"
        assert cliente.get("/api/v1/admin/parametros", headers=auth(token_a)).status_code == 403
        critico = cliente.put(
            "/api/v1/admin/parametros",
            json={"clave": "margen_piso_absoluto", "valor": 0},
            headers=auth(token_panel),
        )
        assert critico.status_code == 403 and codigo(critico) == "PROHIBIDO"

        direccion_a = cliente.get("/api/v1/direcciones", headers=auth(token_a)).json()[0]["id"]
        direccion_b = cliente.get("/api/v1/direcciones", headers=auth(token_b)).json()[0]["id"]
        ajena = cliente.patch(
            f"/api/v1/direcciones/{direccion_a}",
            json={"direccion": "Robada", "lat": -25.28, "lng": -57.63},
            headers=auth(token_b),
        )
        assert ajena.status_code == 404

        horas = cliente.get(
            "/api/v1/disponibilidad",
            params={"fecha": fecha_ok, "horas": -4, "lat": -25.28, "lng": -57.63},
            headers=auth(token_a),
        )
        assert horas.status_code == 422 and codigo(horas) == "HORAS"
        cupon = cliente.post(
            "/api/v1/cupones/validar",
            json={"codigo": "NO-EXISTE", "subtotal": 100000},
            headers=auth(token_a),
        )
        assert cupon.status_code == 422 and codigo(cupon) == "CUPON_INVALIDO"

        archivo_malo = cliente.post(
            "/api/v1/archivos",
            files={"archivo": ("shell.html", b"<script>alert(1)</script>", "text/html")},
            data={"carpeta": "ordenes"},
            headers=auth(token_a),
        )
        assert archivo_malo.status_code == 415
        carpeta = cliente.post(
            "/api/v1/archivos",
            files={"archivo": ("x.png", PNG, "image/png")},
            data={"carpeta": "../secretos"},
            headers=auth(token_a),
        )
        assert carpeta.status_code == 422 and codigo(carpeta) == "CARPETA"
        subida_cliente = cliente.post(
            "/api/v1/archivos",
            files={"archivo": ("cliente.png", PNG, "image/png")},
            data={"carpeta": "general"},
            headers=auth(token_b),
        )
        assert subida_cliente.status_code == 200, subida_cliente.text
        archivo_b = subida_cliente.json()["id"]
        ver_ajeno = cliente.get(f"/api/v1/archivos/{archivo_b}", headers=auth(token_a))
        assert ver_ajeno.status_code == 403

        # Borrador: una disputa no puede saltar el estado.
        borrador = cliente.put(
            "/api/v1/ordenes/borrador",
            json={
                "tipo_hogar_id": hogar,
                "tipo_limpieza_id": limpieza,
                "direccion_id": direccion_a,
                "fecha": fecha_ok,
                "hora_inicio": "08:00:00",
            },
            headers=auth(token_a),
        )
        assert borrador.status_code == 200 and borrador.json()["estado"] == "BORRADOR"
        borrador_id = borrador.json()["id"]
        disputa_temprana = cliente.post(
            f"/api/v1/ordenes/{borrador_id}/problemas",
            json={"tipo": "otro", "descripcion": "quiero romper el estado"},
            headers=auth(token_a),
        )
        assert disputa_temprana.status_code == 409 and codigo(disputa_temprana) == "ESTADO_INVALIDO"
        sigue = cliente.get(f"/api/v1/ordenes/{borrador_id}", headers=auth(token_a))
        assert sigue.json()["estado"] == "BORRADOR"

        # --- Jornada feliz: pago, asignación, cierre y calificación ---
        total = precio(cliente, token_a, hogar, limpieza, direccion_a, fecha_ok)
        clave = f"qa-{secrets.token_hex(8)}"
        pedido, cuerpo = pedir_orden(cliente, token_a, hogar, limpieza, direccion_a, fecha_ok, total, clave)
        assert pedido.status_code == 200, pedido.text
        orden_id = pedido.json()["id"]
        assert pedido.json()["estado"] == "EN_REVISION"
        assert "margen" not in pedido.json()
        repetido, _ = pedir_orden(cliente, token_a, hogar, limpieza, direccion_a, fecha_ok, total, clave)
        assert repetido.status_code == 200 and repetido.json()["id"] == orden_id
        otra_clave = cliente.post(
            "/api/v1/ordenes",
            json={**cuerpo, "notas": "otro cuerpo"},
            headers={**auth(token_a), "Idempotency-Key": clave},
        )
        assert otra_clave.status_code == 409 and codigo(otra_clave) == "IDEMPOTENCIA"
        cruce = cliente.post(
            "/api/v1/ordenes",
            json=cuerpo,
            headers={**auth(token_b), "Idempotency-Key": clave},
        )
        assert cruce.status_code == 409 and codigo(cruce) == "IDEMPOTENCIA"
        assert orden_id not in cruce.text

        idor = cliente.get(f"/api/v1/ordenes/{orden_id}", headers=auth(token_b))
        assert idor.status_code == 404
        cancelar_ajena = cliente.post(
            f"/api/v1/ordenes/{orden_id}/cancelar",
            json={"motivo": "no es mia"},
            headers=auth(token_b),
        )
        assert cancelar_ajena.status_code == 404
        lista_b = cliente.get("/api/v1/ordenes", headers=auth(token_b)).json()
        assert all(item["id"] != orden_id for item in lista_b["items"])
        inyectado = cliente.get(
            "/api/v1/ordenes",
            params={"estado": "EN_REVISION' OR '1'='1"},
            headers=auth(token_a),
        )
        assert inyectado.status_code == 200
        assert all(item["estado"] == "EN_REVISION' OR '1'='1" for item in inyectado.json()["items"])

        pago_malo = cliente.post(
            f"/api/v1/ordenes/{orden_id}/pagos",
            json={"medio": "transferencia", "monto": -1, "tipo": "saldo"},
            headers=auth(token_a),
        )
        assert pago_malo.status_code == 422 and codigo(pago_malo) == "MONTO"
        reembolso = cliente.post(
            f"/api/v1/ordenes/{orden_id}/pagos",
            json={"medio": "transferencia", "monto": 1000, "tipo": "reembolso"},
            headers=auth(token_a),
        )
        assert reembolso.status_code == 422 and codigo(reembolso) == "TIPO_PAGO"
        bitcoin = cliente.post(
            f"/api/v1/ordenes/{orden_id}/pagos",
            json={"medio": "bitcoin", "monto": 1000, "tipo": "saldo"},
            headers=auth(token_a),
        )
        assert bitcoin.status_code == 422 and codigo(bitcoin) == "MEDIO_PAGO"

        asignar_y_aceptar(cliente, token_admin, token_emp, orden_id, empleado_id)
        detalle = cliente.get(f"/api/v1/ordenes/{orden_id}", headers=auth(token_a)).json()
        assert detalle["pin"] and len(detalle["pin"]) == 4
        movida = cliente.post(
            f"/api/v1/ordenes/{orden_id}/reprogramar",
            json={"fecha": fecha_nueva, "hora_inicio": "13:00:00"},
            headers=auth(token_a),
        )
        assert movida.status_code == 200, movida.text
        assert str(movida.json()["fecha"]).startswith(fecha_nueva)
        otra_fecha = cliente.post(
            f"/api/v1/ordenes/{orden_id}/reprogramar",
            json={"fecha": fecha_cancel, "hora_inicio": "13:00:00"},
            headers=auth(token_a),
        )
        assert otra_fecha.status_code == 409 and codigo(otra_fecha) == "REPROGRAMACION"
        agenda = cliente.get(
            "/api/v1/empleado/agenda",
            params={"desde": fecha_ok, "hasta": fecha_nueva},
            headers=auth(token_emp),
        )
        assert agenda.status_code == 200, agenda.text
        propia = next(item for item in agenda.json()["ordenes"] if item["id"] == orden_id)
        assert propia["direccion"] is None
        assert propia["zona"]
        camino_ajeno = cliente.post(f"/api/v1/ordenes/{orden_id}/en-camino", headers=auth(token_intruso))
        assert camino_ajeno.status_code == 404
        camino_cliente = cliente.post(f"/api/v1/ordenes/{orden_id}/en-camino", headers=auth(token_a))
        assert camino_cliente.status_code == 403
        camino = cliente.post(f"/api/v1/ordenes/{orden_id}/en-camino", headers=auth(token_emp))
        assert camino.status_code == 200, camino.text
        checkin_ajeno = cliente.post(
            f"/api/v1/ordenes/{orden_id}/check-in",
            json={"lat": -25.28, "lng": -57.63},
            headers=auth(token_intruso),
        )
        assert checkin_ajeno.status_code == 404
        lejos = cliente.post(
            f"/api/v1/ordenes/{orden_id}/check-in",
            json={"lat": -25.40, "lng": -57.40},
            headers=auth(token_emp),
        )
        assert lejos.status_code == 422 and codigo(lejos) == "FUERA_DE_RADIO"
        manual = cliente.post(
            f"/api/v1/ordenes/{orden_id}/check-in",
            json={"lat": -25.40, "lng": -57.40, "manual": True},
            headers=auth(token_emp),
        )
        assert manual.status_code == 422 and codigo(manual) == "LLEGADA_MANUAL"
        sin_pin = cliente.post(
            f"/api/v1/ordenes/{orden_id}/check-in",
            json={"lat": -25.28, "lng": -57.63},
            headers=auth(token_emp),
        )
        assert sin_pin.status_code == 422 and codigo(sin_pin) == "PIN"
        adentro = cliente.post(
            f"/api/v1/ordenes/{orden_id}/check-in",
            json={"lat": -25.28, "lng": -57.63, "pin": detalle["pin"]},
            headers=auth(token_emp),
        )
        assert adentro.status_code == 200 and adentro.json()["estado"] == "EN_CURSO"

        temprano = cliente.post(
            f"/api/v1/ordenes/{orden_id}/check-out",
            json={"lat": -25.28, "lng": -57.63},
            headers=auth(token_emp),
        )
        assert temprano.status_code == 422 and codigo(temprano) == "CHECKLIST"
        detalle = cliente.get(f"/api/v1/ordenes/{orden_id}", headers=auth(token_a)).json()
        for tarea in detalle["tareas"]:
            hecho = cliente.patch(
                f"/api/v1/ordenes/{orden_id}/tareas/{tarea['id']}",
                json={"completada": True},
                headers=auth(token_emp),
            )
            assert hecho.status_code == 200, hecho.text
        tarea_ajena = cliente.patch(
            f"/api/v1/ordenes/{orden_id}/tareas/{detalle['tareas'][0]['id']}",
            json={"completada": False},
            headers=auth(token_intruso),
        )
        assert tarea_ajena.status_code == 409
        sin_foto = cliente.post(
            f"/api/v1/ordenes/{orden_id}/check-out",
            json={"lat": -25.28, "lng": -57.63},
            headers=auth(token_emp),
        )
        assert sin_foto.status_code == 422 and codigo(sin_foto) == "FOTO_DESPUES"
        foto_ajena = cliente.post(
            f"/api/v1/ordenes/{orden_id}/fotos",
            params={"tipo": "despues", "archivo_id": archivo_b},
            headers=auth(token_emp),
        )
        assert foto_ajena.status_code == 403
        subida = cliente.post(
            "/api/v1/archivos",
            files={"archivo": ("despues.png", PNG, "image/png")},
            data={"carpeta": "ordenes"},
            headers=auth(token_emp),
        )
        assert subida.status_code == 200, subida.text
        foto = cliente.post(
            f"/api/v1/ordenes/{orden_id}/fotos",
            params={"tipo": "despues", "archivo_id": subida.json()["id"]},
            headers=auth(token_emp),
        )
        assert foto.status_code == 200, foto.text
        salida = cliente.post(
            f"/api/v1/ordenes/{orden_id}/check-out",
            json={"lat": -25.28, "lng": -57.63},
            headers=auth(token_emp),
        )
        assert salida.status_code == 200, salida.text
        assert salida.json()["estado"] in {"COMPLETADA", "PAGADA"}

        nota_mala = cliente.post(
            f"/api/v1/ordenes/{orden_id}/calificar",
            json={"puntaje": 9},
            headers=auth(token_a),
        )
        assert nota_mala.status_code == 422 and codigo(nota_mala) == "PUNTAJE"
        nota = cliente.post(
            f"/api/v1/ordenes/{orden_id}/calificar",
            json={"puntaje": 5, "comentario": "Quedó bien."},
            headers=auth(token_a),
        )
        assert nota.status_code == 200 and nota.json()["garantia_disponible"] is False
        otra_nota = cliente.post(
            f"/api/v1/ordenes/{orden_id}/calificar",
            json={"puntaje": 1},
            headers=auth(token_a),
        )
        assert otra_nota.status_code == 409
        intruso_nota = cliente.post(
            f"/api/v1/empleado/ordenes/{orden_id}/calificar-cliente",
            json={"puntaje": 1, "comentario": "ajeno"},
            headers=auth(token_intruso),
        )
        assert intruso_nota.status_code == 404
        propia_nota = cliente.post(
            f"/api/v1/empleado/ordenes/{orden_id}/calificar-cliente",
            json={"puntaje": 5, "comentario": "Puntual."},
            headers=auth(token_emp),
        )
        assert propia_nota.status_code == 200, propia_nota.text
        garantia = cliente.post(f"/api/v1/ordenes/{orden_id}/garantia", headers=auth(token_a))
        assert garantia.status_code == 422 and codigo(garantia) == "GARANTIA"
        disputa = cliente.post(
            f"/api/v1/ordenes/{orden_id}/problemas",
            json={"tipo": "otro", "descripcion": "ya está calificada"},
            headers=auth(token_a),
        )
        assert disputa.status_code == 409
        final = cliente.get(f"/api/v1/ordenes/{orden_id}", headers=auth(token_a)).json()
        assert final["estado"] == "CALIFICADA"
        facturas = cliente.get("/api/v1/cliente/facturas", headers=auth(token_a))
        assert facturas.status_code == 200 and facturas.json()
        pdf = cliente.get(
            f"/api/v1/cliente/facturas/{facturas.json()[0]['id']}/pdf",
            headers=auth(token_a),
        )
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        factura_ajena = cliente.get(
            f"/api/v1/cliente/facturas/{facturas.json()[0]['id']}",
            headers=auth(token_b),
        )
        assert factura_ajena.status_code == 404
        enlace = cliente.post(f"/api/v1/ordenes/{orden_id}/compartir", headers=auth(token_a))
        assert enlace.status_code == 200, enlace.text
        publico = cliente.get(f"/api/v1/publico/seguimiento/{enlace.json()['token']}")
        assert publico.status_code == 200 and publico.json()["estado"] == "CALIFICADA"

        # --- Cancelación y garantía, con las reglas del negocio ---
        total_cancel = precio(cliente, token_a, hogar, limpieza, direccion_a, fecha_cancel)
        cancelable, _ = pedir_orden(cliente, token_a, hogar, limpieza, direccion_a, fecha_cancel, total_cancel)
        assert cancelable.status_code == 200, cancelable.text
        cancel_id = cancelable.json()["id"]
        asignar_y_aceptar(cliente, token_admin, token_emp, cancel_id, empleado_id)
        cancelada = cliente.post(
            f"/api/v1/ordenes/{cancel_id}/cancelar",
            json={"motivo": "Cambio de planes del circuito"},
            headers=auth(token_a),
        )
        assert cancelada.status_code == 200, cancelada.text
        assert cliente.get(f"/api/v1/ordenes/{cancel_id}", headers=auth(token_a)).json()["estado"] == "CANCELADA"
        otra_cancel = cliente.post(
            f"/api/v1/ordenes/{cancel_id}/cancelar",
            json={"motivo": "de nuevo"},
            headers=auth(token_a),
        )
        assert otra_cancel.status_code == 409
        seguir = cliente.post(f"/api/v1/ordenes/{cancel_id}/en-camino", headers=auth(token_emp))
        assert seguir.status_code == 409

        total_garantia = precio(cliente, token_a, hogar, limpieza, direccion_a, fecha_garantia)
        mala, _ = pedir_orden(cliente, token_a, hogar, limpieza, direccion_a, fecha_garantia, total_garantia)
        assert mala.status_code == 200, mala.text
        mala_id = mala.json()["id"]
        asignar_y_aceptar(cliente, token_admin, token_emp, mala_id, empleado_id)
        assert cliente.post(f"/api/v1/ordenes/{mala_id}/en-camino", headers=auth(token_emp)).status_code == 200
        pin_mala = cliente.get(f"/api/v1/ordenes/{mala_id}", headers=auth(token_a)).json()["pin"]
        assert (
            cliente.post(
                f"/api/v1/ordenes/{mala_id}/check-in",
                json={"lat": -25.28, "lng": -57.63, "pin": pin_mala},
                headers=auth(token_emp),
            ).status_code
            == 200
        )
        tareas = cliente.get(f"/api/v1/ordenes/{mala_id}", headers=auth(token_a)).json()["tareas"]
        for tarea in tareas:
            assert (
                cliente.patch(
                    f"/api/v1/ordenes/{mala_id}/tareas/{tarea['id']}",
                    json={"completada": True},
                    headers=auth(token_emp),
                ).status_code
                == 200
            )
        subida_2 = cliente.post(
            "/api/v1/archivos",
            files={"archivo": ("despues.png", PNG, "image/png")},
            data={"carpeta": "ordenes"},
            headers=auth(token_emp),
        )
        assert subida_2.status_code == 200
        assert (
            cliente.post(
                f"/api/v1/ordenes/{mala_id}/fotos",
                params={"tipo": "despues", "archivo_id": subida_2.json()["id"]},
                headers=auth(token_emp),
            ).status_code
            == 200
        )
        cierre = cliente.post(
            f"/api/v1/ordenes/{mala_id}/check-out",
            json={"lat": -25.28, "lng": -57.63},
            headers=auth(token_emp),
        )
        assert cierre.status_code == 200, cierre.text
        baja = cliente.post(
            f"/api/v1/ordenes/{mala_id}/calificar",
            json={"puntaje": 2, "comentario": "No quedó bien."},
            headers=auth(token_a),
        )
        assert baja.status_code == 200 and baja.json()["garantia_disponible"] is True
        relimpieza = cliente.post(f"/api/v1/ordenes/{mala_id}/garantia", headers=auth(token_a))
        assert relimpieza.status_code == 200, relimpieza.text
        nueva = cliente.get(
            f"/api/v1/ordenes/{relimpieza.json()['orden_id']}",
            headers=auth(token_a),
        )
        assert nueva.status_code == 200 and nueva.json()["estado"] == "EN_REVISION"
        repetir_garantia = cliente.post(f"/api/v1/ordenes/{mala_id}/garantia", headers=auth(token_a))
        assert repetir_garantia.status_code == 422

        bloqueo = cliente.post(
            f"/api/v1/admin/clientes/{cliente_b['cliente_id']}/bloquear",
            params={"bloqueado": True},
            headers=auth(token_admin),
        )
        assert bloqueo.status_code == 200, bloqueo.text
        preso = cliente.get("/api/v1/cliente/inicio", headers=auth(token_b))
        assert preso.status_code == 403 and codigo(preso) == "CLIENTE_BLOQUEADO"

        kpis = cliente.get("/api/v1/admin/reportes/kpis", headers=auth(token_admin))
        assert kpis.status_code == 200 and "margen_promedio" in kpis.json()
