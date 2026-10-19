import secrets
from datetime import date, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


def codigo(respuesta):
    detalle = respuesta.json().get("detail")
    if isinstance(detalle, dict):
        return detalle.get("code")
    return None


def test_reglas_de_reserva_cobro_y_datos():
    with TestClient(app) as cliente:
        monedas = cliente.get("/api/v1/publico/monedas")
        assert monedas.status_code == 200
        assert any(item["codigo"] == "PYG" and item["es_base"] for item in monedas.json())
        paises = cliente.get("/api/v1/publico/paises")
        assert paises.status_code == 200
        paraguay = next(item for item in paises.json() if item["codigo"] == "PY")
        assert any(doc["codigo"] == "CI" for doc in paraguay["documentos"])

        servicios = cliente.get("/api/v1/publico/servicios").json()
        hogar = next(item["id"] for item in servicios["hogares"] if item["codigo"] == "casa_mediana")
        limpieza = next(item["id"] for item in servicios["limpiezas"] if item["codigo"] == "regular")
        dolar = cliente.post(
            "/api/v1/publico/cotizar",
            json={"tipo_hogar_id": hogar, "tipo_limpieza_id": limpieza, "lat": -25.28, "lng": -57.63, "moneda": "USD"},
        )
        assert dolar.status_code == 422 and codigo(dolar) == "MONEDA"

        admin = cliente.post("/api/v1/auth/login", json={"cedula": "80000001", "password": "Admin#2026"})
        assert admin.status_code == 200, admin.text
        token_admin = admin.json()["access_token"]
        cambio = cliente.post(
            "/api/v1/admin/tipos-cambio",
            json={"moneda": "USD", "tasa_gs": 7300},
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert cambio.status_code == 200, cambio.text
        cotizado = cliente.post(
            "/api/v1/publico/cotizar",
            json={"tipo_hogar_id": hogar, "tipo_limpieza_id": limpieza, "lat": -25.28, "lng": -57.63, "moneda": "USD"},
        )
        assert cotizado.status_code == 200, cotizado.text
        assert cotizado.json()["moneda"] == "USD"
        assert cotizado.json()["total_moneda"] > 0
        sucursal = cliente.post(
            "/api/v1/admin/sucursales-operacion",
            json={"nombre": f"Sucursal {secrets.token_hex(3)}", "ciudad": "Asunción"},
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert sucursal.status_code == 200, sucursal.text
        listado = cliente.get("/api/v1/admin/sucursales-operacion", headers={"Authorization": f"Bearer {token_admin}"})
        assert any(item["id"] == sucursal.json()["id"] for item in listado.json())

        cedula = str(43000000 + secrets.randbelow(899999))
        alta = cliente.post(
            "/api/v1/admin/clientes",
            json={
                "nombre": "Marta",
                "apellido": "Benítez",
                "cedula": cedula,
                "telefono": "595981222333",
                "direccion": "Av. España 123, Asunción",
                "lat": -25.28,
                "lng": -57.63,
            },
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert alta.status_code == 200, alta.text
        sesion = cliente.post("/api/v1/auth/login", json={"cedula": cedula, "password": cedula})
        token = sesion.json()["access_token"]
        token = cliente.post(
            "/api/v1/auth/cambiar-password",
            json={"password_actual": cedula, "password_nueva": "Cliente#2026", "confirmar": "Cliente#2026"},
            headers={"Authorization": f"Bearer {token}"},
        ).json()["access_token"]
        cabecera = {"Authorization": f"Bearer {token}"}
        lejos = date.today() + timedelta(days=120)
        anticipacion = cliente.get(
            "/api/v1/disponibilidad",
            params={"fecha": lejos.isoformat(), "horas": 2, "lat": -25.28, "lng": -57.63},
            headers=cabecera,
        )
        assert anticipacion.status_code == 422 and codigo(anticipacion) == "ANTICIPACION"
        prefs = cliente.put(
            "/api/v1/cliente/hogar",
            json={"mascotas": True, "ninos": False, "hipoalergenico": True, "productos_propios": False, "areas_restringidas": "estudio"},
            headers=cabecera,
        )
        assert prefs.status_code == 200 and prefs.json()["mascotas"] is True
        datos = cliente.get("/api/v1/cliente/datos", headers=cabecera)
        assert datos.status_code == 200 and datos.json()["perfil"]["cedula"] == cedula

        cedula_emp = str(53000000 + secrets.randbelow(899999))
        zonas = [zona["id"] for zona in cliente.get("/api/v1/publico/zonas").json()]
        alta_emp = cliente.post(
            "/api/v1/admin/empleados",
            json={"nombre": "Sara", "apellido": "Díaz", "cedula": cedula_emp, "telefono": "595981444555", "zonas": zonas},
            headers={"Authorization": f"Bearer {token_admin}"},
        )
        assert alta_emp.status_code == 200, alta_emp.text
        token_emp = cliente.post("/api/v1/auth/login", json={"cedula": cedula_emp, "password": cedula_emp}).json()["access_token"]
        token_emp = cliente.post(
            "/api/v1/auth/cambiar-password",
            json={"password_actual": cedula_emp, "password_nueva": "Empleado#2026", "confirmar": "Empleado#2026"},
            headers={"Authorization": f"Bearer {token_emp}"},
        ).json()["access_token"]
        medio = cliente.put(
            "/api/v1/empleado/medio-cobro",
            json={"tipo": "banco", "referencia": "001-123456"},
            headers={"Authorization": f"Bearer {token_emp}"},
        )
        assert medio.status_code == 200, medio.text
        assert medio.json()["utilizable_desde"]
        perfil = cliente.put(
            "/api/v1/empleado/perfil",
            json={"contacto_emergencia_nombre": "Ana", "contacto_emergencia_telefono": "595981000111", "modalidad": "independiente"},
            headers={"Authorization": f"Bearer {token_emp}"},
        )
        assert perfil.status_code == 200, perfil.text
        ajeno = cliente.post(
            f"/api/v1/ordenes/{uuid4()}/imprevistos",
            json={"tipo": "otro", "detalle": "puerta cerrada"},
            headers={"Authorization": f"Bearer {token_emp}"},
        )
        assert ajeno.status_code == 404 and codigo(ajeno) == "NO_ENCONTRADA"

        baja = cliente.post("/api/v1/cliente/cuenta/eliminar", headers=cabecera)
        assert baja.status_code == 200, baja.text
        bloqueado = cliente.get("/api/v1/cliente/inicio", headers=cabecera)
        assert bloqueado.status_code == 403 and codigo(bloqueado) == "CLIENTE_BLOQUEADO"
