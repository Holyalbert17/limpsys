"""Carga datos de prueba por la API, siguiendo el flujo real de Limsys.

El catálogo, los roles, los parámetros y el contenido público salen del SQL inicial.
Las personas, las direcciones y la jornada se crean acá, con los mismos endpoints
que usa la web y la app.
"""

from datetime import date

import httpx

BASE = "http://127.0.0.1:8001/api/v1"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x00\x03\x00\x01\x00\x05\xfe\xd4\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


def pedir(cliente: httpx.Client, metodo: str, path: str, token: str | None = None, aceptar=(), **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    respuesta = cliente.request(metodo, f"{BASE}{path}", headers=headers, **kwargs)
    if respuesta.status_code >= 400 and respuesta.status_code not in aceptar:
        raise SystemExit(f"{metodo} {path} -> {respuesta.status_code}\n{respuesta.text}")
    if "application/json" in respuesta.headers.get("content-type", ""):
        cuerpo = respuesta.json()
    else:
        cuerpo = respuesta.content
    if aceptar:
        return respuesta.status_code, cuerpo
    return cuerpo


def entrar(cliente: httpx.Client, cedula: str, password: str) -> dict:
    return pedir(cliente, "POST", "/auth/login", json={"cedula": cedula, "password": password})


def cambiar(cliente: httpx.Client, token: str, actual: str, nueva: str) -> str:
    sesion = pedir(
        cliente,
        "POST",
        "/auth/cambiar-password",
        token,
        json={"password_actual": actual, "password_nueva": nueva, "confirmar": nueva},
    )
    return sesion["access_token"]


def sesion_propia(cliente: httpx.Client, cedula: str, inicial: str, nueva: str) -> str:
    prueba = cliente.post(f"{BASE}/auth/login", json={"cedula": cedula, "password": nueva})
    if prueba.status_code == 200 and not prueba.json().get("debe_cambiar_password"):
        return prueba.json()["access_token"]
    primera = entrar(cliente, cedula, inicial)
    return cambiar(cliente, primera["access_token"], inicial, nueva)


def jornada(cliente: httpx.Client, token_admin: str, token_cliente: str, token_empleada: str, fecha: date, cerrar: bool):
    servicios = pedir(cliente, "GET", "/publico/servicios")
    hogar = next(item for item in servicios["hogares"] if item["codigo"] == "casa_mediana")
    limpieza = next(item for item in servicios["limpiezas"] if item["codigo"] == "regular")
    direccion_id = pedir(cliente, "GET", "/direcciones", token_cliente)[0]["id"]
    presupuesto = pedir(
        cliente,
        "POST",
        "/presupuesto",
        token_cliente,
        json={
            "tipo_hogar_id": hogar["id"],
            "tipo_limpieza_id": limpieza["id"],
            "direccion_id": direccion_id,
            "fecha": fecha.isoformat(),
            "hora_inicio": "08:00:00",
        },
    )
    orden = pedir(
        cliente,
        "POST",
        "/ordenes",
        token_cliente,
        headers={"Idempotency-Key": f"flujo-{fecha.isoformat()}"},
        json={
            "tipo_hogar_id": hogar["id"],
            "tipo_limpieza_id": limpieza["id"],
            "direccion_id": direccion_id,
            "fecha": fecha.isoformat(),
            "hora_inicio": "08:00:00",
            "pago": {"medio": "transferencia", "monto": presupuesto["total"], "tipo": "total"},
        },
    )
    if orden["estado"] == "EN_REVISION":
        pedir(cliente, "POST", f"/admin/ordenes/{orden['id']}/asignar-auto", token_admin)
    ofertas = pedir(cliente, "GET", "/empleado/ofertas", token_empleada)
    pedir(cliente, "POST", f"/empleado/ofertas/{ofertas[0]['id']}/aceptar", token_empleada)
    if not cerrar:
        return orden["numero"], "CONFIRMADA"
    pedir(cliente, "POST", f"/ordenes/{orden['id']}/en-camino", token_empleada)
    pedir(
        cliente,
        "POST",
        "/empleado/ubicacion",
        token_empleada,
        json={"lat": -25.285, "lng": -57.62, "orden_id": orden["id"]},
    )
    pedir(
        cliente,
        "POST",
        f"/ordenes/{orden['id']}/check-in",
        token_empleada,
        headers={"Idempotency-Key": f"checkin-{fecha.isoformat()}"},
        json={"lat": -25.28, "lng": -57.63},
    )
    detalle = pedir(cliente, "GET", f"/ordenes/{orden['id']}", token_cliente)
    for tarea in detalle["tareas"]:
        pedir(
            cliente,
            "PATCH",
            f"/ordenes/{orden['id']}/tareas/{tarea['id']}",
            token_empleada,
            json={"completada": True},
        )
    archivo = pedir(
        cliente,
        "POST",
        "/archivos",
        token_empleada,
        files={"archivo": ("despues.png", PNG, "image/png")},
        data={"carpeta": "ordenes"},
    )
    pedir(
        cliente,
        "POST",
        f"/ordenes/{orden['id']}/fotos",
        token_empleada,
        params={"tipo": "despues", "archivo_id": archivo["id"]},
    )
    pedir(
        cliente,
        "POST",
        f"/ordenes/{orden['id']}/check-out",
        token_empleada,
        json={"lat": -25.28, "lng": -57.63},
    )
    pedir(
        cliente,
        "POST",
        f"/ordenes/{orden['id']}/calificar",
        token_cliente,
        json={"puntaje": 5, "comentario": "Quedó muy bien.", "categorias": {"puntualidad": 5, "limpieza": 5}},
    )
    final = pedir(cliente, "GET", f"/ordenes/{orden['id']}", token_cliente)
    return orden["numero"], final["estado"]


def main() -> None:
    with httpx.Client(trust_env=False, timeout=30) as cliente:
        admin = entrar(cliente, "80000001", "Admin#2026")
        token_admin = admin["access_token"]

        pedir(
            cliente,
            "POST",
            "/admin/clientes",
            token_admin,
            aceptar=(409,),
            json={
                "nombre": "Ana",
                "apellido": "Benítez",
                "cedula": "40000001",
                "telefono": "595981000001",
                "correo": "ana@example.com",
                "direccion": "Av. España 123, Asunción",
                "lat": -25.28,
                "lng": -57.63,
            },
        )
        token_ana = sesion_propia(cliente, "40000001", "40000001", "Cliente#2026")
        pedir(cliente, "POST", "/cliente/tour", token_ana)

        estado_alta, alta = pedir(
            cliente,
            "POST",
            "/publico/registro",
            aceptar=(409,),
            json={
                "nombre": "Luis",
                "apellido": "Ortega",
                "cedula": "40000002",
                "telefono": "595981000002",
                "correo": "luis@example.com",
                "password": "Cliente#2026",
                "acepta_terminos": True,
            },
        )
        if estado_alta != 409:
            pedir(
                cliente,
                "POST",
                "/publico/registro/verificar",
                json={"cedula": "40000002", "codigo": alta["debug_codigo"]},
            )
        luis = entrar(cliente, "40000002", "Cliente#2026")
        token_luis = luis["access_token"]
        pedir(cliente, "POST", "/cliente/tour", token_luis)
        pedir(
            cliente,
            "POST",
            "/direcciones",
            token_luis,
            json={
                "apodo": "Casa",
                "direccion": "Av. España 450, Asunción",
                "lat": -25.28,
                "lng": -57.63,
                "referencia": "Portón gris",
                "es_principal": True,
            },
        )

        zonas = [zona["id"] for zona in pedir(cliente, "GET", "/publico/zonas")]
        pedir(
            cliente,
            "POST",
            "/admin/empleados",
            token_admin,
            json={
                "nombre": "Rosa",
                "apellido": "Gómez",
                "cedula": "50000001",
                "telefono": "595981000003",
                "correo": "rosa@example.com",
                "zonas": zonas,
            },
        )
        rosa = entrar(cliente, "50000001", "50000001")
        token_rosa = cambiar(cliente, rosa["access_token"], "50000001", "Empleado#2026")
        pedir(
            cliente,
            "PUT",
            "/empleado/horarios",
            token_rosa,
            json=[{"dia_semana": dia, "hora_desde": "07:00:00", "hora_hasta": "18:00:00"} for dia in range(1, 7)],
        )
        for curso in pedir(cliente, "GET", "/empleado/capacitaciones", token_rosa):
            if curso["obligatoria"] and not curso["completada"]:
                pedir(cliente, "POST", f"/empleado/capacitaciones/{curso['id']}/completar", token_rosa)
        pedir(cliente, "PATCH", "/empleado/disponibilidad", token_rosa, params={"disponible": True})
        cedula = pedir(
            cliente,
            "POST",
            "/archivos",
            token_rosa,
            files={"archivo": ("cedula.png", PNG, "image/png")},
            data={"carpeta": "documentos"},
        )
        pedir(
            cliente,
            "POST",
            "/empleado/documentos",
            token_rosa,
            params={"tipo": "cedula", "archivo_id": cedula["id"]},
        )

        pedir(
            cliente,
            "POST",
            "/publico/contacto",
            json={
                "nombre": "Marta Ruiz",
                "telefono": "595981100001",
                "correo": "marta@example.com",
                "mensaje": "Quiero saber si cubren Villa Morra los sábados.",
            },
        )
        pedir(
            cliente,
            "POST",
            "/publico/empresas/cotizacion",
            json={
                "empresa": "Estudio Norte",
                "ruc": "80012345-6",
                "contacto": "Andrés Vera",
                "telefono": "595981100002",
                "correo": "andres@example.com",
                "sucursales": 2,
                "horas_mes": 40,
                "mensaje": "Limpieza de oficina dos veces por semana.",
            },
        )
        pedir(
            cliente,
            "POST",
            "/publico/postulaciones",
            json={
                "nombre": "Carmen",
                "apellido": "Duarte",
                "cedula": "50000002",
                "telefono": "595981100003",
                "correo": "carmen@example.com",
                "zonas": "Asunción centro",
                "mensaje": "Tengo experiencia en limpieza de hogares.",
            },
        )
        pedir(
            cliente,
            "POST",
            "/publico/lista-espera",
            json={
                "nombre": "Pedro Acosta",
                "telefono": "595981100004",
                "direccion": "Encarnación centro",
                "lat": -27.33,
                "lng": -55.87,
            },
        )

        numero_cerrada, estado_cerrada = jornada(
            cliente, token_admin, token_luis, token_rosa, date(2026, 11, 4), cerrar=True
        )
        numero_proxima, estado_proxima = jornada(
            cliente, token_admin, token_ana, token_rosa, date(2026, 11, 5), cerrar=False
        )

    print("Datos cargados por la API.")
    print(f"  Orden cerrada {numero_cerrada}: {estado_cerrada}")
    print(f"  Orden próxima {numero_proxima}: {estado_proxima}")
    print("  Admin 80000001 / Admin#2026")
    print("  Ana 40000001 / Cliente#2026 (alta del panel)")
    print("  Luis 40000002 / Cliente#2026 (autoregistro)")
    print("  Rosa 50000001 / Empleado#2026")


if __name__ == "__main__":
    main()
