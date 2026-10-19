import secrets
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app


def test_salud_cotizacion_y_acceso():
    with TestClient(app) as cliente:
        salud = cliente.get("/api/v1/salud")
        assert salud.status_code == 200

        servicios = cliente.get("/api/v1/publico/servicios").json()
        hogar = next(item for item in servicios["hogares"] if item["codigo"] == "casa_mediana")
        limpieza = next(item for item in servicios["limpiezas"] if item["codigo"] == "regular")
        cotizacion = cliente.post(
            "/api/v1/publico/cotizar",
            json={
                "tipo_hogar_id": hogar["id"],
                "tipo_limpieza_id": limpieza["id"],
                "lat": -25.28,
                "lng": -57.63,
            },
        )
        assert cotizacion.status_code == 200, cotizacion.text
        cuerpo = cotizacion.json()
        assert cuerpo["fuera_de_cobertura"] is False
        assert cuerpo["zona"]["nombre"] == "Asunción centro"
        assert cuerpo["horas_est"] == 4
        assert "margen" not in cuerpo
        assert cuerpo["total"] > 0

        cobertura = cliente.get("/api/v1/publico/cobertura", params={"lat": -27.0, "lng": -55.0})
        assert cobertura.json()["cubre"] is False

        admin = cliente.post("/api/v1/auth/login", json={"cedula": "80000001", "password": "Admin#2026"})
        assert admin.status_code == 200, admin.text
        token_admin = admin.json()["access_token"]
        assert admin.json()["usuario"]["rol"] == "superadmin"
        assert "ordenes.asignar" in admin.json()["usuario"]["permisos"]

        cedula = str(60000000 + secrets.randbelow(999999))
        alta = cliente.post(
            "/api/v1/admin/clientes",
            json={"nombre": "Prueba", "apellido": "Gate", "cedula": cedula, "telefono": "595981111111"},
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert alta.status_code == 200, alta.text

        primero = cliente.post("/api/v1/auth/login", json={"cedula": cedula, "password": cedula})
        assert primero.status_code == 200, primero.text
        assert primero.json()["debe_cambiar_password"] is True
        token = primero.json()["access_token"]
        bloqueado = cliente.get("/api/v1/cliente/inicio", headers={"Authorization": f"Bearer {token}"})
        assert bloqueado.status_code == 403
        assert bloqueado.json()["detail"]["code"] == "PASSWORD_CHANGE_REQUIRED"

        cambio = cliente.post(
            "/api/v1/auth/cambiar-password",
            json={"password_actual": cedula, "password_nueva": "Nueva#2026", "confirmar": "Nueva#2026"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert cambio.status_code == 200, cambio.text
        assert cambio.json()["debe_cambiar_password"] is False
        token = cambio.json()["access_token"]
        inicio = cliente.get("/api/v1/cliente/inicio", headers={"Authorization": f"Bearer {token}"})
        assert inicio.status_code == 200, inicio.text

        mal = cliente.post("/api/v1/auth/login", json={"cedula": cedula, "password": "incorrecta"})
        assert mal.status_code == 401
        assert mal.json()["detail"]["code"] == "CREDENCIALES"

        zonas = [zona["id"] for zona in cliente.get("/api/v1/publico/zonas").json()]
        cedula_cliente = str(41000000 + secrets.randbelow(899999))
        alta_cliente = cliente.post(
            "/api/v1/admin/clientes",
            json={
                "nombre": "Luis",
                "apellido": "Ortega",
                "cedula": cedula_cliente,
                "telefono": "595981000002",
                "direccion": "Av. España 123, Asunción",
                "lat": -25.28,
                "lng": -57.63,
            },
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert alta_cliente.status_code == 200, alta_cliente.text
        sesion_cliente = cliente.post(
            "/api/v1/auth/login",
            json={"cedula": cedula_cliente, "password": cedula_cliente},
        )
        assert sesion_cliente.status_code == 200, sesion_cliente.text
        token_luis = sesion_cliente.json()["access_token"]
        cambio_cliente = cliente.post(
            "/api/v1/auth/cambiar-password",
            json={"password_actual": cedula_cliente, "password_nueva": "Cliente#2026", "confirmar": "Cliente#2026"},
            headers={"Authorization": f"Bearer {token_luis}"},
        )
        assert cambio_cliente.status_code == 200, cambio_cliente.text
        token_luis = cambio_cliente.json()["access_token"]

        cedula_empleada = str(51000000 + secrets.randbelow(899999))
        alta_empleada = cliente.post(
            "/api/v1/admin/empleados",
            json={
                "nombre": "Rosa",
                "apellido": "Gómez",
                "cedula": cedula_empleada,
                "telefono": "595981000003",
                "zonas": zonas,
            },
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert alta_empleada.status_code == 200, alta_empleada.text
        sesion_empleada = cliente.post(
            "/api/v1/auth/login",
            json={"cedula": cedula_empleada, "password": cedula_empleada},
        )
        assert sesion_empleada.status_code == 200, sesion_empleada.text
        token_rosa = sesion_empleada.json()["access_token"]
        cambio_empleada = cliente.post(
            "/api/v1/auth/cambiar-password",
            json={"password_actual": cedula_empleada, "password_nueva": "Empleado#2026", "confirmar": "Empleado#2026"},
            headers={"Authorization": f"Bearer {token_rosa}"},
        )
        assert cambio_empleada.status_code == 200, cambio_empleada.text
        token_rosa = cambio_empleada.json()["access_token"]
        horarios = cliente.put(
            "/api/v1/empleado/horarios",
            json=[{"dia_semana": dia, "hora_desde": "07:00:00", "hora_hasta": "18:00:00"} for dia in range(1, 7)],
            headers={"Authorization": f"Bearer {token_rosa}"},
        )
        assert horarios.status_code == 200, horarios.text
        cursos = cliente.get("/api/v1/empleado/capacitaciones", headers={"Authorization": f"Bearer {token_rosa}"})
        assert cursos.status_code == 200, cursos.text
        for curso in cursos.json():
            if curso["obligatoria"] and not curso["completada"]:
                hecho = cliente.post(
                    f"/api/v1/empleado/capacitaciones/{curso['id']}/completar",
                    headers={"Authorization": f"Bearer {token_rosa}"},
                )
                assert hecho.status_code == 200, hecho.text
        empleado_id = alta_empleada.json()["empleado_id"]
        direcciones = cliente.get("/api/v1/direcciones", headers={"Authorization": f"Bearer {token_luis}"})
        assert direcciones.status_code == 200 and direcciones.json()
        direccion_id = direcciones.json()[0]["id"]
        fecha = date.today() + timedelta(days=21 + secrets.randbelow(20))
        while fecha.isoweekday() == 7:
            fecha += timedelta(days=1)
        hora = "08:00:00" if secrets.randbelow(2) == 0 else "13:00:00"
        clave = f"pedido-{secrets.token_hex(8)}"
        presupuesto = cliente.post(
            "/api/v1/presupuesto",
            json={
                "tipo_hogar_id": hogar["id"],
                "tipo_limpieza_id": limpieza["id"],
                "direccion_id": direccion_id,
                "fecha": fecha.isoformat(),
                "hora_inicio": hora,
            },
            headers={"Authorization": f"Bearer {token_luis}"},
        )
        assert presupuesto.status_code == 200, presupuesto.text
        assert presupuesto.json()["anticipacion" if False else "anticipo"] > 0

        pedido = cliente.post(
            "/api/v1/ordenes",
            json={
                "tipo_hogar_id": hogar["id"],
                "tipo_limpieza_id": limpieza["id"],
                "direccion_id": direccion_id,
                "fecha": fecha.isoformat(),
                "hora_inicio": hora,
                "pago": {"medio": "transferencia", "monto": presupuesto.json()["total"], "tipo": "total"},
            },
            headers={"Authorization": f"Bearer {token_luis}", "Idempotency-Key": clave},
        )
        assert pedido.status_code == 200, pedido.text
        assert pedido.json()["estado"] in {"EN_REVISION", "ASIGNADA"}
        orden_id = pedido.json()["id"]
        repetido = cliente.post(
            "/api/v1/ordenes",
            json={
                "tipo_hogar_id": hogar["id"],
                "tipo_limpieza_id": limpieza["id"],
                "direccion_id": direccion_id,
                "fecha": fecha.isoformat(),
                "hora_inicio": hora,
                "pago": {"medio": "transferencia", "monto": presupuesto.json()["total"], "tipo": "total"},
            },
            headers={"Authorization": f"Bearer {token_luis}", "Idempotency-Key": clave},
        )
        assert repetido.status_code == 200
        assert repetido.json()["id"] == orden_id

        if pedido.json()["estado"] == "EN_REVISION":
            asignada = cliente.post(
                f"/api/v1/admin/ordenes/{orden_id}/asignar",
                json={"empleado_id": empleado_id, "motivo": "Prueba de la jornada"},
                headers={"Authorization": f"Bearer {token_admin}"},
            )
            assert asignada.status_code == 200, asignada.text
        ofertas = cliente.get("/api/v1/empleado/ofertas", headers={"Authorization": f"Bearer {token_rosa}"})
        assert ofertas.status_code == 200 and ofertas.json(), ofertas.text
        aceptada = cliente.post(
            f"/api/v1/empleado/ofertas/{ofertas.json()[0]['id']}/aceptar",
            headers={"Authorization": f"Bearer {token_rosa}"},
        )
        assert aceptada.status_code == 200, aceptada.text
        assert aceptada.json()["estado"] == "CONFIRMADA"

        camino = cliente.post(f"/api/v1/ordenes/{orden_id}/en-camino", headers={"Authorization": f"Bearer {token_rosa}"})
        assert camino.status_code == 200, camino.text
        lejos = cliente.post(
            f"/api/v1/ordenes/{orden_id}/check-in",
            json={"lat": -25.40, "lng": -57.40},
            headers={"Authorization": f"Bearer {token_rosa}"},
        )
        assert lejos.status_code == 422
        assert lejos.json()["detail"]["code"] == "FUERA_DE_RADIO"
        pin = cliente.get(f"/api/v1/ordenes/{orden_id}", headers={"Authorization": f"Bearer {token_luis}"}).json()["pin"]
        adentro = cliente.post(
            f"/api/v1/ordenes/{orden_id}/check-in",
            json={"lat": -25.28, "lng": -57.63, "pin": pin},
            headers={"Authorization": f"Bearer {token_rosa}", "Idempotency-Key": f"checkin-{clave}"},
        )
        assert adentro.status_code == 200, adentro.text
        assert adentro.json()["estado"] == "EN_CURSO"

        detalle = cliente.get(f"/api/v1/ordenes/{orden_id}", headers={"Authorization": f"Bearer {token_luis}"}).json()
        for tarea in detalle["tareas"]:
            hecho = cliente.patch(
                f"/api/v1/ordenes/{orden_id}/tareas/{tarea['id']}",
                json={"completada": True},
                headers={"Authorization": f"Bearer {token_rosa}"},
            )
            assert hecho.status_code == 200, hecho.text
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
            b"\x00\x00\x00\x03\x00\x01\x00\x05\xfe\xd4\xef\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        subida = cliente.post(
            "/api/v1/archivos",
            files={"archivo": ("despues.png", png, "image/png")},
            data={"carpeta": "ordenes"},
            headers={"Authorization": f"Bearer {token_rosa}"},
        )
        assert subida.status_code == 200, subida.text
        foto = cliente.post(
            f"/api/v1/ordenes/{orden_id}/fotos",
            params={"tipo": "despues", "archivo_id": subida.json()["id"]},
            headers={"Authorization": f"Bearer {token_rosa}"},
        )
        assert foto.status_code == 200, foto.text
        salida = cliente.post(
            f"/api/v1/ordenes/{orden_id}/check-out",
            json={"lat": -25.28, "lng": -57.63},
            headers={"Authorization": f"Bearer {token_rosa}"},
        )
        assert salida.status_code == 200, salida.text
        assert salida.json()["estado"] in {"COMPLETADA", "PAGADA", "CALIFICADA"}
        seguimiento = cliente.get(
            f"/api/v1/ordenes/{orden_id}/seguimiento",
            headers={"Authorization": f"Bearer {token_luis}"},
        )
        assert seguimiento.status_code == 200, seguimiento.text
        facturas = cliente.get("/api/v1/cliente/facturas", headers={"Authorization": f"Bearer {token_luis}"})
        assert facturas.status_code == 200 and facturas.json(), facturas.text
        pdf = cliente.get(
            f"/api/v1/cliente/facturas/{facturas.json()[0]['id']}/pdf",
            headers={"Authorization": f"Bearer {token_luis}"},
        )
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        kpis = cliente.get("/api/v1/admin/reportes/kpis", headers={"Authorization": f"Bearer {token_admin}"})
        assert kpis.status_code == 200, kpis.text
        assert "margen_promedio" in kpis.json()
