import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr, Field

from app.core.db import transaccion
from app.core.errors import ApiError
from app.core.util import hoy_py, ip_valida, rec, recs
from app.domain.geo import extraer_coordenadas
from app.services.auth import consumir_cupo
from app.services.cotizador import cotizar, resolver_zona
from app.services.moneda import aplicar_moneda
from app.services.ordenes import seguimiento
from app.services.parametros import pbool
from app.services.parametros import obtener_parametros
from app.services.personas import crear_cliente

router = APIRouter(prefix="/publico", tags=["Sitio público"])


class CotizarIn(BaseModel):
    tipo_hogar_id: int
    tipo_limpieza_id: int
    elementos: list[dict] = []
    fecha: str | None = None
    hora_inicio: str | None = None
    lat: float | None = None
    lng: float | None = None
    zona_id: int | None = None
    combo_id: str | None = None
    cupon: str | None = None
    presupuesto_maximo: int | None = None
    moneda: str | None = None


class RegistroIn(BaseModel):
    nombre: str = Field(min_length=2)
    apellido: str = ""
    cedula: str
    telefono: str
    correo: EmailStr | None = None
    password: str
    acepta_terminos: bool
    codigo_referido: str | None = None
    borrador: dict | None = None


class VerificarRegistroIn(BaseModel):
    cedula: str
    codigo: str


class ContactoIn(BaseModel):
    nombre: str
    telefono: str | None = None
    correo: EmailStr | None = None
    mensaje: str = Field(min_length=5)


class EmpresaIn(BaseModel):
    empresa: str
    ruc: str | None = None
    contacto: str
    telefono: str
    correo: EmailStr | None = None
    sucursales: int | None = None
    horas_mes: float | None = None
    mensaje: str | None = None


class PostulacionIn(BaseModel):
    nombre: str
    apellido: str = ""
    cedula: str
    telefono: str
    correo: EmailStr | None = None
    zonas: str | None = None
    mensaje: str | None = None


class EsperaIn(BaseModel):
    nombre: str | None = None
    telefono: str | None = None
    direccion: str | None = None
    lat: float | None = None
    lng: float | None = None


def _vigente_sql() -> str:
    return "(vigente_desde IS NULL OR vigente_desde <= now()) AND (vigente_hasta IS NULL OR vigente_hasta >= now())"


@router.get("/tema")
async def tema(idioma: str = "es"):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            "SELECT valor FROM contenido.contenido_sitio WHERE clave = 'tema' AND idioma = $1",
            idioma,
        )
        params = await obtener_parametros(conn)
    valor = fila["valor"] if fila else {}
    valor = {
        **valor,
        "autoregistro": pbool(params, "autoregistro"),
        "login_whatsapp": pbool(params, "login_whatsapp"),
    }
    return valor


@router.get("/contenido")
async def contenido(idioma: str = "es"):
    async with transaccion() as conn:
        filas = await conn.fetch(
            f"""
            SELECT clave, valor FROM contenido.contenido_sitio
            WHERE idioma = $1 AND {_vigente_sql()}
            """,
            idioma,
        )
    return {fila["clave"]: fila["valor"] for fila in filas}


@router.get("/servicios")
async def servicios():
    async with transaccion() as conn:
        tipos = await conn.fetch(
            """
            SELECT id, codigo, nombre, descripcion, incluye, tarifa_hora, factor_horas
            FROM catalogo.tipos_limpieza WHERE activo ORDER BY orden
            """
        )
        hogares = await conn.fetch(
            """
            SELECT id, codigo, nombre, descripcion, horas_base, m2_orientativo
            FROM catalogo.tipos_hogar WHERE activo ORDER BY orden
            """
        )
        elementos = await conn.fetch(
            """
            SELECT id, codigo, nombre, descripcion, adicional, minutos
            FROM catalogo.elementos WHERE activo ORDER BY orden
            """
        )
    return {"limpiezas": recs(tipos), "hogares": recs(hogares), "elementos": recs(elementos)}


@router.get("/combos")
async def combos():
    async with transaccion() as conn:
        filas = await conn.fetch(
            f"""
            SELECT * FROM comercial.combos
            WHERE activo AND publico_web AND {_vigente_sql()}
            ORDER BY nombre
            """
        )
        salida = []
        for combo in filas:
            items = await conn.fetch(
                """
                SELECT ci.cantidad, t.nombre AS limpieza, e.nombre AS elemento
                FROM comercial.combo_items ci
                LEFT JOIN catalogo.tipos_limpieza t ON t.id = ci.tipo_limpieza_id
                LEFT JOIN catalogo.elementos e ON e.id = ci.elemento_id
                WHERE ci.combo_id = $1
                """,
                combo["id"],
            )
            data = rec(combo)
            data["items"] = recs(items)
            salida.append(data)
    return salida


@router.get("/ofertas")
async def ofertas():
    async with transaccion() as conn:
        filas = await conn.fetch(
            f"""
            SELECT id, nombre, tipo, valor, es_porcentaje, tope, vigente_hasta, cupo, usos
            FROM comercial.promociones
            WHERE activo AND {_vigente_sql()}
            ORDER BY prioridad DESC
            """
        )
        banners = await conn.fetch(
            f"SELECT * FROM contenido.banners WHERE activo AND {_vigente_sql()} ORDER BY orden"
        )
    return {"promociones": recs(filas), "banners": recs(banners)}


@router.get("/zonas")
async def zonas():
    async with transaccion() as conn:
        filas = await conn.fetch(
            "SELECT id, codigo, nombre, poligono, traslado FROM catalogo.zonas WHERE activo ORDER BY nombre"
        )
    return recs(filas)


@router.get("/faq")
async def faq(idioma: str = "es"):
    async with transaccion() as conn:
        filas = await conn.fetch(
            "SELECT id, pregunta, respuesta FROM contenido.faq WHERE activo AND idioma = $1 ORDER BY orden",
            idioma,
        )
    return recs(filas)


@router.get("/testimonios")
async def testimonios():
    async with transaccion() as conn:
        curados = await conn.fetch(
            "SELECT nombre, texto, puntaje, zona FROM contenido.testimonios WHERE activo ORDER BY creado_en DESC LIMIT 12"
        )
        reales = await conn.fetch(
            """
            SELECT u.nombre, c.comentario AS texto, c.puntaje, z.nombre AS zona
            FROM operacion.calificaciones c
            JOIN seguridad.usuarios u ON u.id = c.de_usuario_id
            JOIN operacion.ordenes o ON o.id = c.orden_id
            LEFT JOIN crm.direcciones d ON d.id = o.direccion_id
            LEFT JOIN catalogo.zonas z ON z.id = d.zona_id
            WHERE c.de_rol = 'cliente' AND c.puntaje >= 4 AND c.comentario IS NOT NULL
            ORDER BY c.creado_en DESC LIMIT 12
            """
        )
    return {"curados": recs(curados), "reales": recs(reales)}


@router.get("/cobertura")
async def cobertura(lat: float, lng: float):
    async with transaccion() as conn:
        zona = await resolver_zona(conn, lat, lng, None)
    if zona is None:
        return {"cubre": False, "zona": None}
    return {"cubre": True, "zona": {"id": zona["id"], "nombre": zona["nombre"], "traslado": int(zona["traslado"])}}


@router.post("/cotizar")
async def cotizador(datos: CotizarIn):
    from datetime import date, time
    from uuid import UUID

    cuerpo = datos.model_dump()
    if cuerpo.get("fecha"):
        cuerpo["fecha"] = date.fromisoformat(cuerpo["fecha"])
    if cuerpo.get("hora_inicio"):
        cuerpo["hora_inicio"] = time.fromisoformat(cuerpo["hora_inicio"])
    if cuerpo.get("combo_id"):
        cuerpo["combo_id"] = UUID(cuerpo["combo_id"])
    async with transaccion() as conn:
        resultado = await cotizar(conn, cuerpo, None, con_margen=False)
        return await aplicar_moneda(conn, resultado, cuerpo.get("moneda"))


@router.get("/monedas")
async def monedas():
    async with transaccion() as conn:
        filas = await conn.fetch(
            "SELECT codigo, nombre, decimales, es_base FROM catalogo.monedas WHERE habilitada ORDER BY es_base DESC, codigo"
        )
    return recs(filas)


@router.get("/paises")
async def paises():
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT p.codigo, p.nombre,
                   COALESCE(
                     jsonb_agg(jsonb_build_object('codigo', d.codigo, 'nombre', d.nombre))
                       FILTER (WHERE d.id IS NOT NULL),
                     '[]'::jsonb
                   ) AS documentos
            FROM catalogo.paises p
            LEFT JOIN catalogo.tipos_documento d ON d.pais = p.codigo
            WHERE p.activo
            GROUP BY p.codigo, p.nombre
            ORDER BY p.nombre
            """
        )
    return recs(filas)


@router.post("/registro")
async def registro(datos: RegistroIn, request: Request):
    if not datos.acepta_terminos:
        raise ApiError(422, "TERMINOS", "Tenés que aceptar los términos.")
    async with transaccion() as conn:
        params = await obtener_parametros(conn)
        if not pbool(params, "autoregistro"):
            raise ApiError(403, "REGISTRO_CERRADO", "El registro público está desactivado.")
        ip = ip_valida(request.client.host if request.client else None)
        if ip:
            await consumir_cupo(conn, f"ip:{ip}:registro", 8, 3600)
        from app.core.security import hash_password
        from app.core.util import normalizar_cedula
        from app.services.auth import crear_otp

        try:
            cedula = normalizar_cedula(datos.cedula)
        except ValueError as exc:
            raise ApiError(422, "CEDULA", "La cédula tiene que tener solo números, sin puntos.") from exc
        existe = await conn.fetchval("SELECT 1 FROM seguridad.usuarios WHERE cedula = $1", cedula)
        if existe:
            raise ApiError(409, "CEDULA", "Esa cédula ya tiene una cuenta.")
        from app.core.security import requisitos_password

        faltan = requisitos_password(datos.password, cedula)
        if faltan:
            raise ApiError(422, "PASSWORD_DEBIL", "La contraseña no cumple los requisitos.", {"faltan": faltan})
        codigo = f"{__import__('secrets').randbelow(1000000):06d}"
        from app.core.config import settings
        from app.core.util import hash_otp

        await conn.execute("DELETE FROM seguridad.registros_pendientes WHERE cedula = $1", cedula)
        await conn.execute(
            """
            INSERT INTO seguridad.registros_pendientes (
              cedula, nombre, apellido, telefono, correo, password_hash, acepta_terminos,
              codigo_referido, borrador, codigo_hash, expira_en
            )
            VALUES ($1,$2,$3,$4,$5,$6,true,$7,$8::jsonb,$9, now() + interval '10 minutes')
            """,
            cedula,
            datos.nombre.strip(),
            datos.apellido.strip(),
            datos.telefono,
            datos.correo,
            hash_password(datos.password),
            datos.codigo_referido,
            datos.borrador,
            hash_otp(codigo, settings.jwt_secret),
        )
    cuerpo = {"mensaje": "Te enviamos un código para confirmar el teléfono."}
    if settings.dev_return_otp:
        cuerpo["debug_codigo"] = codigo
    return cuerpo


@router.post("/registro/verificar")
async def verificar_registro(datos: VerificarRegistroIn):
    from app.core.config import settings
    from app.core.util import hash_otp, normalizar_cedula

    cedula = normalizar_cedula(datos.cedula)
    async with transaccion() as conn:
        pendiente = await conn.fetchrow(
            """
            SELECT * FROM seguridad.registros_pendientes
            WHERE cedula = $1 AND expira_en > now()
            ORDER BY creado_en DESC LIMIT 1
            """,
            cedula,
        )
        if pendiente is None or pendiente["intentos"] >= 5:
            raise ApiError(422, "OTP", "El código no es válido.")
        if hash_otp(datos.codigo, settings.jwt_secret) != pendiente["codigo_hash"]:
            await conn.execute(
                "UPDATE seguridad.registros_pendientes SET intentos = intentos + 1 WHERE id = $1",
                pendiente["id"],
            )
            raise ApiError(422, "OTP", "El código no es válido.")
        usuario_id, cliente_id = await crear_cliente(
            conn,
            cedula=cedula,
            nombre=pendiente["nombre"],
            apellido=pendiente["apellido"],
            telefono=pendiente["telefono"],
            correo=pendiente["correo"],
            password=cedula,
            debe_cambiar=True,
            creado_por=None,
            codigo_referido=pendiente["codigo_referido"],
        )
        await conn.execute(
            """
            UPDATE seguridad.usuarios
            SET password_hash = $2, debe_cambiar_password = false, password_expira_en = NULL,
                telefono_verificado = true
            WHERE id = $1
            """,
            usuario_id,
            pendiente["password_hash"],
        )
        await conn.execute("DELETE FROM seguridad.registros_pendientes WHERE cedula = $1", cedula)
        await conn.execute(
            """
            INSERT INTO sistema.eventos_conversion (nombre, usuario_id, metadata)
            VALUES ('registro_completo', $1, '{}'::jsonb)
            """,
            usuario_id,
        )
    return {"usuario_id": str(usuario_id), "cliente_id": str(cliente_id), "borrador": pendiente["borrador"]}


@router.post("/contacto")
async def contacto(datos: ContactoIn, request: Request):
    async with transaccion() as conn:
        ip = ip_valida(request.client.host if request.client else None)
        if ip:
            await consumir_cupo(conn, f"ip:{ip}:contacto", 5, 3600)
        await conn.execute(
            "INSERT INTO contenido.contactos (nombre, telefono, correo, mensaje) VALUES ($1,$2,$3,$4)",
            datos.nombre,
            datos.telefono,
            datos.correo,
            datos.mensaje,
        )
    return {"ok": True}


@router.post("/empresas/cotizacion")
async def cotizacion_empresa(datos: EmpresaIn, request: Request):
    async with transaccion() as conn:
        ip = ip_valida(request.client.host if request.client else None)
        if ip:
            await consumir_cupo(conn, f"ip:{ip}:empresa", 5, 3600)
        fila = await conn.fetchrow(
            """
            INSERT INTO contenido.cotizaciones_empresa (empresa, ruc, contacto, telefono, correo, sucursales, horas_mes, mensaje)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            RETURNING id
            """,
            datos.empresa,
            datos.ruc,
            datos.contacto,
            datos.telefono,
            datos.correo,
            datos.sucursales,
            datos.horas_mes,
            datos.mensaje,
        )
    return {"id": str(fila["id"])}


@router.post("/postulaciones")
async def postular(datos: PostulacionIn, request: Request):
    async with transaccion() as conn:
        ip = ip_valida(request.client.host if request.client else None)
        if ip:
            await consumir_cupo(conn, f"ip:{ip}:postulacion", 3, 3600)
        fila = await conn.fetchrow(
            """
            INSERT INTO contenido.postulaciones (nombre, apellido, cedula, telefono, correo, zonas, mensaje)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            RETURNING id
            """,
            datos.nombre,
            datos.apellido,
            datos.cedula,
            datos.telefono,
            datos.correo,
            datos.zonas,
            datos.mensaje,
        )
    return {"id": str(fila["id"])}


@router.post("/lista-espera")
async def lista_espera(datos: EsperaIn):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO crm.lista_espera (nombre, telefono, direccion, lat, lng)
            VALUES ($1,$2,$3,$4,$5) RETURNING id
            """,
            datos.nombre,
            datos.telefono,
            datos.direccion,
            datos.lat,
            datos.lng,
        )
    return {"id": str(fila["id"])}


@router.get("/seguimiento/{token}")
async def seguimiento_publico(token: str):
    async with transaccion() as conn:
        enlace = await conn.fetchrow(
            "SELECT orden_id FROM operacion.enlaces_seguimiento WHERE token = $1 AND expira_en > now()",
            token,
        )
        if enlace is None:
            raise ApiError(404, "NO_ENCONTRADO", "El enlace de seguimiento venció.")
        data = await seguimiento(conn, enlace["orden_id"], "publico")
    data.pop("total", None)
    if data.get("empleado"):
        data["empleado"].pop("id", None)
    return data


@router.get("/legal/{clave}")
async def legal(clave: str, idioma: str = "es"):
    if clave not in {"terminos", "privacidad", "cancelacion_texto"}:
        raise ApiError(404, "NO_ENCONTRADO", "No encontramos esa página.")
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            "SELECT valor FROM contenido.contenido_sitio WHERE clave = $1 AND idioma = $2",
            clave,
            idioma,
        )
    if fila is None:
        raise ApiError(404, "NO_ENCONTRADO", "No encontramos esa página.")
    return fila["valor"]


@router.post("/coordenadas")
async def coordenadas(url: str):
    punto = extraer_coordenadas(url)
    if punto is None and url.startswith("http"):
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as cliente:
                respuesta = await cliente.get(url)
            punto = extraer_coordenadas(str(respuesta.url))
        except httpx.HTTPError:
            punto = None
    if punto is None:
        raise ApiError(422, "ENLACE", "No pudimos leer las coordenadas de ese enlace.")
    return {"lat": punto[0], "lng": punto[1]}


@router.get("/fecha")
async def fecha():
    return {"hoy": hoy_py().isoformat()}
