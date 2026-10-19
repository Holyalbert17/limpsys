from datetime import date, time
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.db import transaccion
from app.core.errors import ApiError
from app.core.deps import cliente_actual, sesion_completa
from app.core.util import decimal, rec, recs
from app.domain.geo import elegir_zona
from app.services.cotizador import cotizar, evaluar_cupon
from app.services.finanzas import crear_pago
from app.services.idempotencia import guardar, reservar
from app.services.estado_orden import cambiar_estado
from app.services.moneda import aplicar_moneda
from app.services.ordenes import (
    calificar,
    cancelar_cliente,
    crear_enlace,
    enviar,
    guardar_borrador,
    pedir_garantia,
    reprogramar,
    repetir,
    responder_extra,
    seguimiento,
)
from app.services.asignacion import slots

router = APIRouter(tags=["Cliente"])


class DireccionIn(BaseModel):
    apodo: str = "Casa"
    direccion: str
    lat: float
    lng: float
    place_id: str | None = None
    referencia: str | None = None
    piso: str | None = None
    instrucciones: str | None = None
    es_principal: bool = False


class ElementoIn(BaseModel):
    elemento_id: int
    cantidad: int = Field(default=1, ge=1, le=30)


class BorradorIn(BaseModel):
    tipo_hogar_id: int | None = None
    tipo_limpieza_id: int | None = None
    elementos: list[ElementoIn] | None = None
    fecha: date | None = None
    hora_inicio: time | None = None
    direccion_id: UUID | None = None
    combo_id: UUID | None = None
    cupon: str | None = None
    puntos: int = 0
    preferencia_empleado_id: UUID | None = None
    notas: str | None = None
    presupuesto_maximo: int | None = None
    es_emergencia: bool = False
    factura_a_nombre: str | None = None
    origen: str = "app"
    cantidad_empleados: int = Field(default=1, ge=1, le=4)
    insumos_modo: str = "empresa"
    moneda: str | None = None


class PagoIn(BaseModel):
    medio: str
    monto: int | None = None
    tipo: str = "anticipo"
    referencia: str | None = None


class EnviarIn(BorradorIn):
    pago: PagoIn | None = None
    aceptar_precio: bool = False
    credito_empresa: bool = False


class PresupuestoIn(BorradorIn):
    lat: float | None = None
    lng: float | None = None


class CancelarIn(BaseModel):
    motivo: str = Field(min_length=3)


class CalificarIn(BaseModel):
    puntaje: Decimal
    categorias: dict = {}
    comentario: str | None = None
    propina: int = 0


class MensajeIn(BaseModel):
    texto: str = Field(min_length=1, max_length=2000)


class FiscalIn(BaseModel):
    ruc: str | None = None
    razon_social: str | None = None


class PerfilIn(BaseModel):
    nombre: str | None = None
    apellido: str | None = None
    telefono: str | None = None
    correo: str | None = None
    idioma: str | None = None
    fecha_nacimiento: date | None = None
    consentimiento_version: str | None = None


class HogarIn(BaseModel):
    mascotas: bool = False
    ninos: bool = False
    hipoalergenico: bool = False
    productos_propios: bool = False
    areas_restringidas: str = ""


class ReprogramarIn(BaseModel):
    fecha: date
    hora_inicio: time


class PlanIn(BaseModel):
    direccion_id: UUID
    tipo_hogar_id: int
    tipo_limpieza_id: int
    elementos: list[ElementoIn] = []
    frecuencia: str
    dia_semana: int
    hora_inicio: time
    empleado_fijo_id: UUID | None = None


class TicketIn(BaseModel):
    asunto: str
    texto: str
    orden_id: UUID | None = None


class PreferenciasIn(BaseModel):
    push: bool = True
    whatsapp: bool = True
    sms: bool = False
    correo: bool = True


class ExtraRespuestaIn(BaseModel):
    aprobar: bool


class CuponIn(BaseModel):
    codigo: str
    subtotal: int


def _ocultar(orden: dict) -> dict:
    data = rec(orden)
    data.pop("margen_calculado", None)
    data.pop("costo_empleado", None)
    if isinstance(data.get("detalle_precio"), dict):
        for clave in ("costo_mano_obra", "cargas", "insumos", "costo_total", "margen", "margen_pct", "costo_hora"):
            data["detalle_precio"].pop(clave, None)
    return data


async def _zonas(conn):
    return recs(await conn.fetch("SELECT * FROM catalogo.zonas WHERE activo"))


@router.get("/cliente/inicio")
async def inicio(usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        proxima = await conn.fetchrow(
            """
            SELECT id, numero, estado, fecha, hora_inicio, total
            FROM operacion.ordenes
            WHERE cliente_id = $1 AND estado IN ('ENVIADA','EN_REVISION','ASIGNADA','CONFIRMADA','EN_CAMINO','EN_CURSO')
            ORDER BY fecha NULLS LAST, hora_inicio NULLS LAST LIMIT 1
            """,
            usuario["cliente_id"],
        )
        activa = await conn.fetchrow(
            """
            SELECT id, numero, estado, total FROM operacion.ordenes
            WHERE cliente_id = $1 AND estado IN ('EN_CAMINO','EN_CURSO')
            ORDER BY actualizado_en DESC LIMIT 1
            """,
            usuario["cliente_id"],
        )
        cliente = await conn.fetchrow("SELECT puntos_saldo, credito_saldo, nombre, tour_pendiente, codigo_referido FROM crm.clientes WHERE id = $1", usuario["cliente_id"])
    return {
        "saludo": cliente["nombre"],
        "proxima": rec(proxima),
        "activa": rec(activa),
        "puntos": cliente["puntos_saldo"],
        "credito": int(cliente["credito_saldo"]),
        "tour_pendiente": cliente["tour_pendiente"],
        "codigo_referido": cliente["codigo_referido"],
    }


@router.post("/cliente/tour")
async def tour(usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        await conn.execute("UPDATE crm.clientes SET tour_pendiente = false WHERE id = $1", usuario["cliente_id"])
    return {"tour_pendiente": False}


@router.get("/cliente/perfil")
async def perfil(usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            SELECT u.cedula, u.nombre, u.apellido, u.telefono, u.correo, u.idioma, u.telefono_verificado,
                   c.puntos_saldo, c.credito_saldo, c.ruc, c.razon_social, c.fecha_nacimiento, c.codigo_referido, c.tipo
            FROM crm.clientes c JOIN seguridad.usuarios u ON u.id = c.usuario_id
            WHERE c.id = $1
            """,
            usuario["cliente_id"],
        )
    return rec(fila)


@router.put("/cliente/perfil")
async def actualizar_perfil(datos: PerfilIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        if datos.idioma and datos.idioma not in {"es", "gn", "en"}:
            raise ApiError(422, "IDIOMA", "El idioma tiene que ser es, gn o en.")
        await conn.execute(
            """
            UPDATE seguridad.usuarios SET
              nombre = COALESCE($2, nombre),
              apellido = COALESCE($3, apellido),
              telefono = COALESCE($4, telefono),
              correo = COALESCE($5, correo),
              idioma = COALESCE($6, idioma),
              actualizado_en = now()
            WHERE id = $1
            """,
            usuario["id"],
            datos.nombre,
            datos.apellido,
            datos.telefono,
            datos.correo,
            datos.idioma,
        )
        await conn.execute(
            """
            UPDATE crm.clientes SET
              nombre = COALESCE($2, nombre),
              apellido = COALESCE($3, apellido),
              telefono = COALESCE($4, telefono),
              correo = COALESCE($5, correo),
              fecha_nacimiento = COALESCE($6, fecha_nacimiento),
              consentimiento_version = COALESCE($7, consentimiento_version),
              consentimiento_en = CASE WHEN $7 IS NULL THEN consentimiento_en ELSE now() END,
              actualizado_en = now()
            WHERE id = $1
            """,
            usuario["cliente_id"],
            datos.nombre,
            datos.apellido,
            datos.telefono,
            datos.correo,
            datos.fecha_nacimiento,
            datos.consentimiento_version,
        )
    return {"ok": True}


@router.put("/cliente/hogar")
async def hogar(datos: HogarIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        await conn.execute(
            "UPDATE crm.clientes SET preferencias_hogar = $2::jsonb, actualizado_en = now() WHERE id = $1",
            usuario["cliente_id"],
            datos.model_dump(),
        )
    return datos.model_dump()


@router.get("/cliente/datos")
async def exportar_datos(usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        await conn.execute(
            """
            INSERT INTO sistema.solicitudes_privacidad (usuario_id, tipo, estado)
            VALUES ($1, 'exportar', 'entregada')
            """,
            usuario["id"],
        )
        perfil_fila = await conn.fetchrow(
            """
            SELECT u.cedula, u.nombre, u.apellido, u.telefono, u.correo, c.preferencias_hogar, c.consentimiento_version
            FROM crm.clientes c JOIN seguridad.usuarios u ON u.id = c.usuario_id
            WHERE c.id = $1
            """,
            usuario["cliente_id"],
        )
        direcciones = await conn.fetch(
            "SELECT apodo, direccion, lat, lng, referencia FROM crm.direcciones WHERE cliente_id = $1",
            usuario["cliente_id"],
        )
        ordenes = await conn.fetch(
            """
            SELECT numero, estado, fecha, hora_inicio, total, insumos_modo, cantidad_empleados
            FROM operacion.ordenes WHERE cliente_id = $1 ORDER BY creado_en
            """,
            usuario["cliente_id"],
        )
    return {"perfil": rec(perfil_fila), "direcciones": recs(direcciones), "ordenes": recs(ordenes)}


@router.post("/cliente/cuenta/eliminar")
async def eliminar_cuenta(usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        abiertas = await conn.fetchval(
            """
            SELECT count(*) FROM operacion.ordenes
            WHERE cliente_id = $1 AND estado IN ('ENVIADA','EN_REVISION','ASIGNADA','CONFIRMADA','EN_CAMINO','EN_CURSO')
            """,
            usuario["cliente_id"],
        )
        if abiertas:
            raise ApiError(409, "CUENTA", "Hay un servicio en curso. Cancelalo antes de eliminar la cuenta.")
        await conn.execute(
            """
            INSERT INTO sistema.solicitudes_privacidad (usuario_id, tipo, estado)
            VALUES ($1, 'eliminar', 'aplicada')
            """,
            usuario["id"],
        )
        await conn.execute(
            """
            UPDATE seguridad.usuarios SET
              nombre = 'Cuenta',
              apellido = 'eliminada',
              correo = NULL,
              telefono = NULL,
              estado = 'bloqueado',
              actualizado_en = now()
            WHERE id = $1
            """,
            usuario["id"],
        )
        await conn.execute("UPDATE seguridad.refresh_tokens SET revocado = true WHERE usuario_id = $1", usuario["id"])
    return {"ok": True}


@router.get("/cliente/fiscal")
async def fiscal(usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        fila = await conn.fetchrow("SELECT ruc, razon_social FROM crm.clientes WHERE id = $1", usuario["cliente_id"])
    return rec(fila)


@router.put("/cliente/fiscal")
async def guardar_fiscal(datos: FiscalIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        await conn.execute(
            "UPDATE crm.clientes SET ruc = $2, razon_social = $3, actualizado_en = now() WHERE id = $1",
            usuario["cliente_id"],
            datos.ruc,
            datos.razon_social,
        )
    return {"ok": True}


@router.get("/direcciones")
async def listar_direcciones(usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        filas = await conn.fetch(
            "SELECT * FROM crm.direcciones WHERE cliente_id = $1 AND activo ORDER BY es_principal DESC, creado_en",
            usuario["cliente_id"],
        )
    return recs(filas)


@router.post("/direcciones")
async def crear_direccion(datos: DireccionIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        zonas = await _zonas(conn)
        zona = elegir_zona(datos.lat, datos.lng, zonas)
        if datos.es_principal:
            await conn.execute("UPDATE crm.direcciones SET es_principal = false WHERE cliente_id = $1", usuario["cliente_id"])
        fila = await conn.fetchrow(
            """
            INSERT INTO crm.direcciones (
              cliente_id, apodo, direccion, lat, lng, place_id, referencia, piso, instrucciones, zona_id, es_principal
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            RETURNING *
            """,
            usuario["cliente_id"],
            datos.apodo,
            datos.direccion,
            datos.lat,
            datos.lng,
            datos.place_id,
            datos.referencia,
            datos.piso,
            datos.instrucciones,
            None if zona is None else zona["id"],
            datos.es_principal,
        )
    cuerpo = rec(fila)
    cuerpo["fuera_de_cobertura"] = zona is None
    return cuerpo


@router.patch("/direcciones/{direccion_id}")
async def editar_direccion(direccion_id: UUID, datos: DireccionIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        zonas = await _zonas(conn)
        zona = elegir_zona(datos.lat, datos.lng, zonas)
        fila = await conn.fetchrow(
            """
            UPDATE crm.direcciones SET
              apodo=$3, direccion=$4, lat=$5, lng=$6, place_id=$7, referencia=$8, piso=$9,
              instrucciones=$10, zona_id=$11, es_principal=$12
            WHERE id=$1 AND cliente_id=$2 AND activo
            RETURNING *
            """,
            direccion_id,
            usuario["cliente_id"],
            datos.apodo,
            datos.direccion,
            datos.lat,
            datos.lng,
            datos.place_id,
            datos.referencia,
            datos.piso,
            datos.instrucciones,
            None if zona is None else zona["id"],
            datos.es_principal,
        )
    if fila is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la dirección.")
    return rec(fila)


@router.delete("/direcciones/{direccion_id}")
async def borrar_direccion(direccion_id: UUID, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        await conn.execute(
            "UPDATE crm.direcciones SET activo = false WHERE id = $1 AND cliente_id = $2",
            direccion_id,
            usuario["cliente_id"],
        )
    return {"ok": True}


@router.get("/disponibilidad")
async def disponibilidad(
    fecha: date,
    horas: float = 2,
    lat: float | None = None,
    lng: float | None = None,
    zona_id: int | None = None,
    usuario: dict = Depends(cliente_actual),
):
    if horas <= 0 or horas > 12:
        raise ApiError(422, "HORAS", "Las horas tienen que estar entre 1 y 12.")
    async with transaccion() as conn:
        return await slots(conn, fecha, decimal(horas), lat, lng, zona_id)


@router.post("/presupuesto")
async def presupuesto(datos: PresupuestoIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        cuerpo = await cotizar(conn, datos.model_dump(), usuario["cliente_id"], con_margen=False)
        return await aplicar_moneda(conn, cuerpo, datos.moneda)


@router.post("/cupones/validar")
async def validar_cupon(datos: CuponIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        cupon = await evaluar_cupon(conn, datos.codigo, usuario["cliente_id"], decimal(datos.subtotal))
    return {"codigo": cupon["codigo"], "monto": int(cupon["monto"]), "nombre": cupon["nombre"]}


@router.put("/ordenes/borrador")
async def borrador(datos: BorradorIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        orden = await guardar_borrador(conn, usuario["cliente_id"], datos.model_dump())
    return _ocultar(orden)


@router.post("/ordenes")
async def crear_orden(
    datos: EnviarIn,
    usuario: dict = Depends(cliente_actual),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    async with transaccion() as conn:
        previo = await reservar(conn, idempotency_key, usuario["id"], "POST /ordenes", datos.model_dump(mode="json"))
        if previo:
            return previo
        orden = await enviar(conn, usuario["cliente_id"], datos.model_dump(), usuario["id"])
        cuerpo = _ocultar(orden)
        await guardar(conn, idempotency_key, cuerpo)
    return cuerpo


@router.post("/ordenes/{orden_id}/reprogramar")
async def cambiar_fecha(orden_id: UUID, datos: ReprogramarIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        orden = await reprogramar(conn, usuario["cliente_id"], orden_id, datos.fecha, datos.hora_inicio, usuario["id"])
    return _ocultar(orden)


@router.get("/ordenes")
async def listar_ordenes(
    estado: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    usuario: dict = Depends(cliente_actual),
):
    filtro = ""
    args: list = [usuario["cliente_id"], page_size, (page - 1) * page_size]
    conteo: list = [usuario["cliente_id"]]
    if estado == "proximos":
        filtro = "AND estado IN ('ENVIADA','EN_REVISION','ASIGNADA','CONFIRMADA')"
    elif estado == "en_curso":
        filtro = "AND estado IN ('EN_CAMINO','EN_CURSO')"
    elif estado == "historial":
        filtro = "AND estado IN ('COMPLETADA','PAGADA','CALIFICADA')"
    elif estado == "cancelados":
        filtro = "AND estado IN ('CANCELADA','NO_SHOW')"
    elif estado:
        filtro = "AND estado = $4"
        args.append(estado)
        conteo.append(estado)
        filtro_conteo = "AND estado = $2"
    else:
        filtro_conteo = filtro
    if estado not in {None, "proximos", "en_curso", "historial", "cancelados"} and estado:
        pass
    else:
        filtro_conteo = filtro
    async with transaccion() as conn:
        filas = await conn.fetch(
            f"""
            SELECT id, numero, estado, fecha, hora_inicio, total, horas_est, es_emergencia, creado_en
            FROM operacion.ordenes
            WHERE cliente_id = $1 {filtro}
            ORDER BY creado_en DESC
            LIMIT $2 OFFSET $3
            """,
            *args,
        )
        total = await conn.fetchval(
            f"SELECT count(*) FROM operacion.ordenes WHERE cliente_id = $1 {filtro_conteo}",
            *conteo,
        )
    return {"items": recs(filas), "total": total, "page": page, "page_size": page_size}


@router.get("/ordenes/{orden_id}")
async def detalle_orden(orden_id: UUID, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        orden = await conn.fetchrow(
            "SELECT * FROM operacion.ordenes WHERE id = $1 AND cliente_id = $2",
            orden_id,
            usuario["cliente_id"],
        )
        if orden is None:
            raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
        elementos = await conn.fetch("SELECT * FROM operacion.orden_elementos WHERE orden_id = $1", orden_id)
        tareas = await conn.fetch("SELECT * FROM operacion.orden_tareas WHERE orden_id = $1 ORDER BY orden", orden_id)
        direccion = None
        if orden["direccion_id"]:
            direccion = await conn.fetchrow("SELECT * FROM crm.direcciones WHERE id = $1", orden["direccion_id"])
    cuerpo = _ocultar(orden)
    cuerpo["elementos"] = recs(elementos)
    cuerpo["tareas"] = recs(tareas)
    cuerpo["direccion"] = rec(direccion)
    return cuerpo


@router.get("/ordenes/{orden_id}/seguimiento")
async def seguir(orden_id: UUID, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        existe = await conn.fetchval(
            "SELECT 1 FROM operacion.ordenes WHERE id = $1 AND cliente_id = $2",
            orden_id,
            usuario["cliente_id"],
        )
        if not existe:
            raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
        return await seguimiento(conn, orden_id, "cliente")


@router.post("/ordenes/{orden_id}/compartir")
async def compartir(orden_id: UUID, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        existe = await conn.fetchval("SELECT 1 FROM operacion.ordenes WHERE id = $1 AND cliente_id = $2", orden_id, usuario["cliente_id"])
        if not existe:
            raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
        token = await crear_enlace(conn, orden_id)
    return {"token": token, "ruta": f"/api/v1/publico/seguimiento/{token}"}


@router.post("/ordenes/{orden_id}/cancelar")
async def cancelar(orden_id: UUID, datos: CancelarIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        return await cancelar_cliente(conn, orden_id, usuario["cliente_id"], datos.motivo, usuario["id"])


@router.post("/ordenes/{orden_id}/calificar")
async def calificar_orden(orden_id: UUID, datos: CalificarIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        orden = await conn.fetchval("SELECT 1 FROM operacion.ordenes WHERE id = $1 AND cliente_id = $2", orden_id, usuario["cliente_id"])
        if not orden:
            raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
        return await calificar(conn, orden_id, usuario["id"], "cliente", datos.puntaje, datos.categorias, datos.comentario, decimal(datos.propina))


@router.post("/ordenes/{orden_id}/repetir")
async def repetir_orden(orden_id: UUID, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        orden = await repetir(conn, orden_id, usuario["cliente_id"])
    return _ocultar(orden)


@router.post("/ordenes/{orden_id}/pagos")
async def pagar(
    orden_id: UUID,
    datos: PagoIn,
    usuario: dict = Depends(cliente_actual),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    async with transaccion() as conn:
        previo = await reservar(conn, idempotency_key, usuario["id"], f"POST /ordenes/{orden_id}/pagos", datos.model_dump())
        if previo:
            return previo
        orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1 AND cliente_id = $2", orden_id, usuario["cliente_id"])
        if orden is None:
            raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
        cliente = await conn.fetchrow("SELECT * FROM crm.clientes WHERE id = $1", usuario["cliente_id"])
        monto = decimal(datos.monto or orden["saldo"] or orden["anticipo"])
        pago = await crear_pago(conn, orden, cliente, datos.medio, datos.tipo, monto, datos.referencia, usuario["id"])
        cuerpo = rec(pago)
        await guardar(conn, idempotency_key, cuerpo)
    return cuerpo


@router.post("/ordenes/{orden_id}/extra-tiempo/{extra_id}/responder")
async def responder(orden_id: UUID, extra_id: UUID, datos: ExtraRespuestaIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        return await responder_extra(conn, orden_id, extra_id, usuario["cliente_id"], datos.aprobar)


@router.post("/ordenes/{orden_id}/garantia")
async def garantia(orden_id: UUID, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        nueva = await pedir_garantia(conn, orden_id, usuario["cliente_id"])
    return {"orden_id": str(nueva)}


class ProblemaIn(BaseModel):
    tipo: str = "otro"
    descripcion: str = Field(min_length=5, max_length=2000)


@router.post("/ordenes/{orden_id}/problemas")
async def reportar_problema(orden_id: UUID, datos: ProblemaIn, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        existe = await conn.fetchval(
            "SELECT 1 FROM operacion.ordenes WHERE id = $1 AND cliente_id = $2",
            orden_id,
            usuario["cliente_id"],
        )
        if not existe:
            raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
        await cambiar_estado(conn, orden_id, "DISPUTA", usuario["id"], nota=datos.descripcion)
        fila = await conn.fetchrow(
            """
            INSERT INTO operacion.incidentes (orden_id, reportado_por, tipo, descripcion)
            VALUES ($1,$2,$3,$4) RETURNING id
            """,
            orden_id,
            usuario["id"],
            datos.tipo,
            datos.descripcion,
        )
    return {"id": str(fila["id"])}


@router.post("/ordenes/{orden_id}/conformidad")
async def conformidad(orden_id: UUID, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE operacion.ordenes
            SET conformidad = true, conformidad_en = now()
            WHERE id = $1 AND cliente_id = $2 AND estado IN ('EN_CURSO','COMPLETADA','PAGADA','CALIFICADA')
            RETURNING id
            """,
            orden_id,
            usuario["cliente_id"],
        )
    if fila is None:
        raise ApiError(409, "ESTADO_INVALIDO", "Todavía no se puede dar la conformidad.")
    return {"ok": True}


@router.get("/ordenes/{orden_id}/mensajes")
async def mensajes(orden_id: UUID, usuario: dict = Depends(sesion_completa)):
    async with transaccion() as conn:
        await _participa(conn, orden_id, usuario)
        filas = await conn.fetch(
            """
            SELECT m.id, m.texto, m.autor_rol, m.creado_en, u.nombre AS autor
            FROM operacion.mensajes m
            JOIN seguridad.usuarios u ON u.id = m.autor_id
            WHERE m.orden_id = $1
            ORDER BY m.creado_en
            """,
            orden_id,
        )
    return recs(filas)


@router.post("/ordenes/{orden_id}/mensajes")
async def escribir(orden_id: UUID, datos: MensajeIn, usuario: dict = Depends(sesion_completa)):
    async with transaccion() as conn:
        await _participa(conn, orden_id, usuario)
        fila = await conn.fetchrow(
            """
            INSERT INTO operacion.mensajes (orden_id, autor_id, autor_rol, texto)
            VALUES ($1,$2,$3,$4) RETURNING id, creado_en
            """,
            orden_id,
            usuario["id"],
            usuario["rol"],
            datos.texto.strip(),
        )
    return rec(fila)


async def _participa(conn, orden_id, usuario):
    orden = await conn.fetchrow("SELECT cliente_id, empleado_id FROM operacion.ordenes WHERE id = $1", orden_id)
    if orden is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
    if usuario["rol"] in {"admin", "superadmin"}:
        return
    if usuario["rol"] == "cliente" and str(orden["cliente_id"]) == str(usuario["cliente_id"]):
        return
    if usuario["rol"] == "empleado" and str(orden["empleado_id"]) == str(usuario["empleado_id"]):
        return
    raise ApiError(403, "PROHIBIDO", "No participás de esta solicitud.")


@router.get("/cliente/facturas")
async def facturas(usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        filas = await conn.fetch(
            "SELECT id, numero, tipo, total, iva, estado, cdc, creado_en FROM finanzas.facturas WHERE cliente_id = $1 ORDER BY creado_en DESC",
            usuario["cliente_id"],
        )
    return recs(filas)


@router.get("/cliente/facturas/{factura_id}")
async def factura(factura_id: UUID, usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            "SELECT * FROM finanzas.facturas WHERE id = $1 AND cliente_id = $2",
            factura_id,
            usuario["cliente_id"],
        )
    if fila is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la factura.")
    return rec(fila)


@router.get("/cliente/facturas/{factura_id}/pdf")
async def factura_pdf(factura_id: UUID, usuario: dict = Depends(cliente_actual)):
    from app.services.pdf import comprobante

    async with transaccion() as conn:
        fila = await conn.fetchrow(
            "SELECT * FROM finanzas.facturas WHERE id = $1 AND cliente_id = $2",
            factura_id,
            usuario["cliente_id"],
        )
    if fila is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la factura.")
    return Response(comprobante(rec(fila)), media_type="application/pdf", headers={"Content-Disposition": f"inline; filename={fila['numero']}.pdf"})


@router.get("/cliente/puntos")
async def puntos(usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        saldo = await conn.fetchval("SELECT puntos_saldo FROM crm.clientes WHERE id = $1", usuario["cliente_id"])
        movs = await conn.fetch(
            "SELECT tipo, puntos, detalle, fecha, vence_en FROM comercial.puntos_movimientos WHERE cliente_id = $1 ORDER BY fecha DESC LIMIT 50",
            usuario["cliente_id"],
        )
    return {"saldo": saldo, "movimientos": recs(movs)}


@router.get("/cliente/referidos")
async def referidos(usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        codigo = await conn.fetchval("SELECT codigo_referido FROM crm.clientes WHERE id = $1", usuario["cliente_id"])
        filas = await conn.fetch(
            """
            SELECT r.estado, r.credito_otorgado, r.creado_en, c.nombre
            FROM comercial.referidos r
            JOIN crm.clientes c ON c.id = r.referido_id
            WHERE r.referidor_id = $1
            ORDER BY r.creado_en DESC
            """,
            usuario["cliente_id"],
        )
    return {"codigo": codigo, "invitados": recs(filas)}


@router.get("/cliente/planes")
async def planes(usuario: dict = Depends(cliente_actual)):
    async with transaccion() as conn:
        filas = await conn.fetch("SELECT * FROM operacion.planes_recurrentes WHERE cliente_id = $1 ORDER BY creado_en DESC", usuario["cliente_id"])
    return recs(filas)


@router.post("/cliente/planes")
async def crear_plan(datos: PlanIn, usuario: dict = Depends(cliente_actual)):
    if datos.frecuencia not in {"semanal", "quincenal"}:
        raise ApiError(422, "FRECUENCIA", "La frecuencia es semanal o quincenal.")
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO operacion.planes_recurrentes (
              cliente_id, direccion_id, tipo_hogar_id, tipo_limpieza_id, elementos,
              frecuencia, dia_semana, hora_inicio, empleado_fijo_id, proxima_fecha
            )
            VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9, CURRENT_DATE)
            RETURNING *
            """,
            usuario["cliente_id"],
            datos.direccion_id,
            datos.tipo_hogar_id,
            datos.tipo_limpieza_id,
            [item.model_dump() for item in datos.elementos],
            datos.frecuencia,
            datos.dia_semana,
            datos.hora_inicio,
            datos.empleado_fijo_id,
        )
    return rec(fila)


@router.patch("/cliente/planes/{plan_id}")
async def editar_plan(plan_id: UUID, estado: str, usuario: dict = Depends(cliente_actual)):
    if estado not in {"activo", "pausado", "cancelado"}:
        raise ApiError(422, "ESTADO", "El estado del plan no es válido.")
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            "UPDATE operacion.planes_recurrentes SET estado = $3 WHERE id = $1 AND cliente_id = $2 RETURNING id, estado",
            plan_id,
            usuario["cliente_id"],
            estado,
        )
    if fila is None:
        raise ApiError(404, "NO_ENCONTRADO", "No encontramos el plan.")
    return rec(fila)


@router.get("/cliente/notificaciones")
async def notificaciones(usuario: dict = Depends(sesion_completa)):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT id, evento, canal, titulo, cuerpo, leida, fecha
            FROM sistema.notificaciones
            WHERE usuario_id = $1
            ORDER BY fecha DESC LIMIT 50
            """,
            usuario["id"],
        )
    return recs(filas)


@router.post("/cliente/notificaciones/leer-todas")
async def leer_todas(usuario: dict = Depends(sesion_completa)):
    async with transaccion() as conn:
        await conn.execute("UPDATE sistema.notificaciones SET leida = true WHERE usuario_id = $1", usuario["id"])
    return {"ok": True}


@router.put("/cliente/notificaciones/preferencias")
async def preferencias(datos: PreferenciasIn, usuario: dict = Depends(sesion_completa)):
    async with transaccion() as conn:
        await conn.execute(
            """
            INSERT INTO sistema.preferencias_notificacion (usuario_id, push, whatsapp, sms, correo)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (usuario_id) DO UPDATE SET push=$2, whatsapp=$3, sms=$4, correo=$5
            """,
            usuario["id"],
            datos.push,
            datos.whatsapp,
            datos.sms,
            datos.correo,
        )
    return datos.model_dump()


class PushIn(BaseModel):
    endpoint: str
    p256dh: str | None = None
    auth: str | None = None


@router.post("/notificaciones/suscripcion")
async def suscribir(datos: PushIn, usuario: dict = Depends(sesion_completa)):
    async with transaccion() as conn:
        await conn.execute(
            """
            INSERT INTO sistema.push_suscripciones (usuario_id, endpoint, p256dh, auth)
            VALUES ($1,$2,$3,$4)
            ON CONFLICT (endpoint) DO UPDATE SET usuario_id = $1, p256dh = $3, auth = $4
            """,
            usuario["id"],
            datos.endpoint,
            datos.p256dh,
            datos.auth,
        )
    return {"ok": True}


@router.post("/soporte/tickets")
async def abrir_ticket(datos: TicketIn, usuario: dict = Depends(sesion_completa)):
    async with transaccion() as conn:
        ticket_id = await conn.fetchval(
            """
            INSERT INTO operacion.tickets_soporte (usuario_id, orden_id, asunto)
            VALUES ($1,$2,$3) RETURNING id
            """,
            usuario["id"],
            datos.orden_id,
            datos.asunto,
        )
        await conn.execute(
            "INSERT INTO operacion.ticket_mensajes (ticket_id, autor_id, texto) VALUES ($1,$2,$3)",
            ticket_id,
            usuario["id"],
            datos.texto,
        )
    return {"id": str(ticket_id)}


@router.get("/soporte/tickets")
async def tickets(usuario: dict = Depends(sesion_completa)):
    async with transaccion() as conn:
        if usuario["rol"] in {"admin", "superadmin"}:
            filas = await conn.fetch("SELECT * FROM operacion.tickets_soporte ORDER BY creado_en DESC LIMIT 100")
        else:
            filas = await conn.fetch(
                "SELECT * FROM operacion.tickets_soporte WHERE usuario_id = $1 ORDER BY creado_en DESC",
                usuario["id"],
            )
    return recs(filas)


@router.post("/soporte/tickets/{ticket_id}/mensajes")
async def responder_ticket(ticket_id: UUID, datos: MensajeIn, usuario: dict = Depends(sesion_completa)):
    async with transaccion() as conn:
        ticket = await conn.fetchrow("SELECT * FROM operacion.tickets_soporte WHERE id = $1", ticket_id)
        if ticket is None:
            raise ApiError(404, "NO_ENCONTRADO", "No encontramos el ticket.")
        if usuario["rol"] not in {"admin", "superadmin"} and ticket["usuario_id"] != usuario["id"]:
            raise ApiError(403, "PROHIBIDO", "No es tu ticket.")
        await conn.execute(
            "INSERT INTO operacion.ticket_mensajes (ticket_id, autor_id, texto) VALUES ($1,$2,$3)",
            ticket_id,
            usuario["id"],
            datos.texto,
        )
        estado = "respondido" if usuario["rol"] in {"admin", "superadmin"} else "abierto"
        await conn.execute("UPDATE operacion.tickets_soporte SET estado = $2 WHERE id = $1", ticket_id, estado)
    return {"ok": True}
