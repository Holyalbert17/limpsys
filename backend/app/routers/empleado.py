from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.core.db import transaccion
from app.core.deps import empleado_actual
from app.core.errors import ApiError
from app.core.util import ahora, decimal, rec, recs
from app.services.finanzas import disputar_item, pedir_adelanto
from app.services.parametros import obtener_parametros, pent
from app.services.idempotencia import guardar, reservar
from app.services.ordenes import (
    calificar,
    cancelar_empleado,
    completar_tarea,
    en_camino,
    pedir_extra,
    registrar_llegada,
    registrar_ubicacion,
)
from app.services.asignacion import aceptar_oferta, rechazar_oferta

router = APIRouter(tags=["Empleado"])
TZ = ZoneInfo("America/Asuncion")


class RechazoIn(BaseModel):
    motivo: str = Field(min_length=3)


class LlegadaIn(BaseModel):
    lat: float
    lng: float
    manual: bool = False
    nota: str | None = None
    foto_archivo_id: UUID | None = None
    pin: str | None = None


class PerfilEmpleadoIn(BaseModel):
    contacto_emergencia_nombre: str | None = None
    contacto_emergencia_telefono: str | None = None
    modalidad: str | None = None


class MedioCobroIn(BaseModel):
    tipo: str = Field(min_length=3, max_length=40)
    referencia: str = Field(min_length=3, max_length=80)


class PausaIn(BaseModel):
    hasta: datetime | None = None


class ImprevistoIn(BaseModel):
    tipo: str
    detalle: str = Field(min_length=5, max_length=2000)
    minutos_extra: int | None = Field(default=None, ge=1, le=240)


class DisputaPagoIn(BaseModel):
    motivo: str = Field(min_length=5, max_length=2000)


class TareaIn(BaseModel):
    completada: bool
    nota: str | None = None


class ExtraIn(BaseModel):
    minutos: int = Field(gt=0, le=240)
    motivo: str


class SosIn(BaseModel):
    lat: float | None = None
    lng: float | None = None
    orden_id: UUID | None = None


class UbicacionIn(BaseModel):
    lat: float
    lng: float
    orden_id: UUID | None = None


class BloqueoIn(BaseModel):
    desde: datetime
    hasta: datetime
    motivo: str | None = None


class AusenciaIn(BaseModel):
    desde: date
    hasta: date
    motivo: str


class DisponibilidadIn(BaseModel):
    dia_semana: int
    hora_desde: time
    hora_hasta: time


class PreferenciaIn(BaseModel):
    distancia_max_km: float | None = None
    tipos_preferidos: list[int] = []
    disponible: bool | None = None


class AdelantoIn(BaseModel):
    monto: int


class CalificarClienteIn(BaseModel):
    puntaje: Decimal
    categorias: dict = {}
    comentario: str | None = None


class CambioTurnoIn(BaseModel):
    empleado_destino_id: UUID | None = None


class InsumoIn(BaseModel):
    insumo_id: int
    cantidad: float


@router.get("/empleado/inicio")
async def inicio(usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        empleado = await conn.fetchrow("SELECT * FROM personal.empleados WHERE id = $1", usuario["empleado_id"])
        proxima = await conn.fetchrow(
            """
            SELECT o.id, o.numero, o.estado, o.fecha, o.hora_inicio, z.nombre AS zona
            FROM operacion.ordenes o
            LEFT JOIN crm.direcciones d ON d.id = o.direccion_id
            LEFT JOIN catalogo.zonas z ON z.id = d.zona_id
            WHERE o.empleado_id = $1 AND o.estado IN ('CONFIRMADA','EN_CAMINO','EN_CURSO')
            ORDER BY o.fecha, o.hora_inicio LIMIT 1
            """,
            usuario["empleado_id"],
        )
        hoy = await conn.fetchval(
            """
            SELECT COALESCE(SUM(costo_mano_obra + propina), 0)
            FROM finanzas.planilla_ingresos p
            JOIN operacion.ordenes o ON o.id = p.orden_id
            WHERE p.empleado_id = $1 AND o.fecha = CURRENT_DATE
            """,
            usuario["empleado_id"],
        )
        ofertas = await conn.fetchval(
            """
            SELECT count(*) FROM operacion.ofertas
            WHERE empleado_id = $1 AND estado = 'pendiente' AND expira_en > now()
            """,
            usuario["empleado_id"],
        )
    return {
        "disponible": empleado["disponible"],
        "nivel": empleado["nivel"],
        "calificacion": float(empleado["calificacion"]),
        "ingresos_hoy": int(hoy or 0),
        "ofertas": ofertas,
        "proxima": rec(proxima),
    }


@router.patch("/empleado/disponibilidad")
async def marcar_disponible(disponible: bool, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        await conn.execute(
            "UPDATE personal.empleados SET disponible = $2, actualizado_en = now() WHERE id = $1",
            usuario["empleado_id"],
            disponible,
        )
    return {"disponible": disponible}


@router.get("/empleado/ofertas")
async def ofertas(usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT o.id, o.expira_en, o.pago_estimado, o.distancia_m, o.zona_nombre, o.puntaje,
                   ord.fecha, ord.hora_inicio, ord.horas_est, ord.es_emergencia,
                   t.nombre AS tipo_limpieza
            FROM operacion.ofertas o
            JOIN operacion.ordenes ord ON ord.id = o.orden_id
            LEFT JOIN catalogo.tipos_limpieza t ON t.id = ord.tipo_limpieza_id
            WHERE o.empleado_id = $1 AND o.estado = 'pendiente' AND o.expira_en > now()
            ORDER BY o.expira_en
            """,
            usuario["empleado_id"],
        )
    return recs(filas)


@router.post("/empleado/ofertas/{oferta_id}/aceptar")
async def aceptar(oferta_id: UUID, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        orden_id = await aceptar_oferta(conn, oferta_id, usuario["empleado_id"])
    return {"orden_id": str(orden_id), "estado": "CONFIRMADA"}


@router.post("/empleado/ofertas/{oferta_id}/rechazar")
async def rechazar(oferta_id: UUID, datos: RechazoIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        orden_id = await rechazar_oferta(conn, oferta_id, usuario["empleado_id"], datos.motivo)
    return {"orden_id": str(orden_id)}


def _dentro_de_ventana(orden, params) -> bool:
    if orden["fecha"] is None or orden["hora_inicio"] is None:
        return False
    inicio = datetime.combine(orden["fecha"], orden["hora_inicio"], tzinfo=TZ)
    fin = inicio + timedelta(hours=float(orden["horas_est"] or 0))
    antes = timedelta(hours=pent(params, "visibilidad_antes_horas"))
    despues = timedelta(hours=pent(params, "visibilidad_despues_horas"))
    return inicio - antes <= ahora() <= fin + despues


@router.get("/empleado/agenda")
async def agenda(desde: date, hasta: date, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        params = await obtener_parametros(conn)
        ordenes = await conn.fetch(
            """
            SELECT o.id, o.numero, o.estado, o.fecha, o.hora_inicio, o.horas_est, o.pin,
                   d.direccion, u.telefono, z.nombre AS zona
            FROM operacion.ordenes o
            LEFT JOIN crm.direcciones d ON d.id = o.direccion_id
            LEFT JOIN crm.clientes c ON c.id = o.cliente_id
            LEFT JOIN seguridad.usuarios u ON u.id = c.usuario_id
            LEFT JOIN catalogo.zonas z ON z.id = d.zona_id
            WHERE o.empleado_id = $1 AND o.fecha BETWEEN $2 AND $3
              AND o.estado NOT IN ('BORRADOR','CANCELADA')
            ORDER BY o.fecha, o.hora_inicio
            """,
            usuario["empleado_id"],
            desde,
            hasta,
        )
        bloqueos = await conn.fetch(
            "SELECT id, desde, hasta, motivo FROM personal.bloqueos WHERE empleado_id = $1 AND hasta >= $2 AND desde <= $3",
            usuario["empleado_id"],
            datetime.combine(desde, time.min),
            datetime.combine(hasta, time.max),
        )
    salida = []
    for orden in ordenes:
        fila = rec(orden)
        if not _dentro_de_ventana(orden, params):
            fila["direccion"] = None
            fila["telefono"] = None
            fila["pin"] = None
        salida.append(fila)
    return {"ordenes": salida, "bloqueos": recs(bloqueos)}


@router.post("/empleado/bloqueos")
async def bloquear(datos: BloqueoIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO personal.bloqueos (empleado_id, desde, hasta, motivo)
            VALUES ($1,$2,$3,$4) RETURNING *
            """,
            usuario["empleado_id"],
            datos.desde,
            datos.hasta,
            datos.motivo,
        )
    return rec(fila)


@router.delete("/empleado/bloqueos/{bloqueo_id}")
async def quitar_bloqueo(bloqueo_id: UUID, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        await conn.execute(
            "DELETE FROM personal.bloqueos WHERE id = $1 AND empleado_id = $2",
            bloqueo_id,
            usuario["empleado_id"],
        )
    return {"ok": True}


@router.put("/empleado/horarios")
async def horarios(franjas: list[DisponibilidadIn], usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        await conn.execute("DELETE FROM personal.disponibilidad WHERE empleado_id = $1", usuario["empleado_id"])
        for franja in franjas:
            await conn.execute(
                """
                INSERT INTO personal.disponibilidad (empleado_id, dia_semana, hora_desde, hora_hasta)
                VALUES ($1,$2,$3,$4)
                """,
                usuario["empleado_id"],
                franja.dia_semana,
                franja.hora_desde,
                franja.hora_hasta,
            )
    return {"ok": True}


@router.post("/ordenes/{orden_id}/en-camino")
async def salir(orden_id: UUID, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        orden = await en_camino(conn, orden_id, usuario["empleado_id"])
    return {"estado": orden["estado"]}


@router.post("/ordenes/{orden_id}/check-in")
async def check_in(
    orden_id: UUID,
    datos: LlegadaIn,
    usuario: dict = Depends(empleado_actual),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    async with transaccion() as conn:
        previo = await reservar(conn, idempotency_key, usuario["id"], f"check-in {orden_id}", datos.model_dump(mode="json"))
        if previo:
            return previo
        cuerpo = await registrar_llegada(
            conn,
            orden_id,
            usuario["empleado_id"],
            "check-in",
            datos.lat,
            datos.lng,
            datos.manual,
            datos.nota,
            datos.foto_archivo_id,
            datos.pin,
        )
        await guardar(conn, idempotency_key, cuerpo)
    return cuerpo


@router.post("/ordenes/{orden_id}/check-out")
async def check_out(
    orden_id: UUID,
    datos: LlegadaIn,
    usuario: dict = Depends(empleado_actual),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    async with transaccion() as conn:
        previo = await reservar(conn, idempotency_key, usuario["id"], f"check-out {orden_id}", datos.model_dump(mode="json"))
        if previo:
            return previo
        cuerpo = await registrar_llegada(
            conn, orden_id, usuario["empleado_id"], "check-out", datos.lat, datos.lng, datos.manual, datos.nota, datos.foto_archivo_id
        )
        await guardar(conn, idempotency_key, cuerpo)
    return cuerpo


@router.patch("/ordenes/{orden_id}/tareas/{tarea_id}")
async def tarea(orden_id: UUID, tarea_id: UUID, datos: TareaIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        fila = await completar_tarea(conn, orden_id, tarea_id, usuario["empleado_id"], datos.completada, datos.nota)
    return rec(fila)


@router.post("/ordenes/{orden_id}/extra-tiempo")
async def extra(orden_id: UUID, datos: ExtraIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        fila = await pedir_extra(conn, orden_id, usuario["empleado_id"], datos.minutos, datos.motivo)
    return rec(fila)


@router.post("/ordenes/{orden_id}/fotos")
async def foto(orden_id: UUID, tipo: str, archivo_id: UUID, usuario: dict = Depends(empleado_actual)):
    if tipo not in {"antes", "despues", "incidente", "fachada", "checkin"}:
        raise ApiError(422, "TIPO", "El tipo de foto no es válido.")
    async with transaccion() as conn:
        orden = await conn.fetchval(
            "SELECT 1 FROM operacion.ordenes WHERE id = $1 AND empleado_id = $2",
            orden_id,
            usuario["empleado_id"],
        )
        if not orden:
            raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
        archivo = await conn.fetchrow("SELECT dueno_id FROM sistema.archivos WHERE id = $1", archivo_id)
        if archivo is None:
            raise ApiError(404, "NO_ENCONTRADO", "No encontramos el archivo.")
        if archivo["dueno_id"] != usuario["id"]:
            raise ApiError(403, "PROHIBIDO", "Esa foto no es tuya.")
        fila = await conn.fetchrow(
            """
            INSERT INTO operacion.orden_fotos (orden_id, tipo, archivo_id, creado_por)
            VALUES ($1,$2,$3,$4) RETURNING id
            """,
            orden_id,
            tipo,
            archivo_id,
            usuario["id"],
        )
    return {"id": str(fila["id"])}


@router.post("/empleado/ordenes/{orden_id}/cancelar")
async def cancelar(orden_id: UUID, datos: RechazoIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        return await cancelar_empleado(conn, orden_id, usuario["empleado_id"], datos.motivo)


@router.post("/empleado/ordenes/{orden_id}/calificar-cliente")
async def calificar_cliente(orden_id: UUID, datos: CalificarClienteIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        propia = await conn.fetchval(
            "SELECT 1 FROM operacion.ordenes WHERE id = $1 AND empleado_id = $2",
            orden_id,
            usuario["empleado_id"],
        )
        if not propia:
            raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
        return await calificar(
            conn, orden_id, usuario["id"], "empleado", datos.puntaje, datos.categorias, datos.comentario, Decimal(0)
        )


@router.post("/empleado/sos")
async def sos(datos: SosIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO operacion.sos (empleado_id, orden_id, lat, lng)
            VALUES ($1,$2,$3,$4) RETURNING id, creado_en
            """,
            usuario["empleado_id"],
            datos.orden_id,
            datos.lat,
            datos.lng,
        )
        from app.services.notificaciones import notificar
        from app.realtime import hub

        await notificar(conn, "sos", None, {"empleado": usuario["nombre"], "numero": str(datos.orden_id or "")})
        await hub.emitir("admin", {"tipo": "sos", "empleado_id": str(usuario["empleado_id"]), "lat": datos.lat, "lng": datos.lng})
    return rec(fila)


@router.post("/empleado/ubicacion")
async def ubicacion(datos: UbicacionIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        mensaje = await registrar_ubicacion(conn, usuario["empleado_id"], datos.lat, datos.lng, datos.orden_id)
    return mensaje or {"guardada": False}


@router.get("/empleado/ingresos")
async def ingresos(usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT p.orden_id, o.numero, o.fecha, p.precio, p.costo_mano_obra, p.propina, p.margen, p.estado_pago, p.motivo_cancelacion
            FROM finanzas.planilla_ingresos p
            JOIN operacion.ordenes o ON o.id = p.orden_id
            WHERE p.empleado_id = $1
            ORDER BY o.fecha DESC NULLS LAST
            LIMIT 100
            """,
            usuario["empleado_id"],
        )
    visibles = []
    for fila in filas:
        data = rec(fila)
        data.pop("margen", None)
        data.pop("precio", None)
        visibles.append(data)
    return visibles


@router.get("/empleado/liquidaciones")
async def liquidaciones(usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        filas = await conn.fetch(
            "SELECT * FROM finanzas.liquidaciones WHERE empleado_id = $1 ORDER BY periodo_desde DESC",
            usuario["empleado_id"],
        )
    return recs(filas)


@router.post("/empleado/adelantos")
async def adelanto(datos: AdelantoIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        fila = await pedir_adelanto(conn, usuario["empleado_id"], decimal(datos.monto))
    return rec(fila)


@router.get("/empleado/capacitaciones")
async def capacitaciones(usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT c.id, c.nombre, c.descripcion, c.video_url, c.obligatoria,
                   COALESCE(ec.completada, false) AS completada
            FROM personal.capacitaciones c
            LEFT JOIN personal.empleado_capacitacion ec
              ON ec.capacitacion_id = c.id AND ec.empleado_id = $1
            WHERE c.activo
            ORDER BY c.obligatoria DESC, c.nombre
            """,
            usuario["empleado_id"],
        )
    return recs(filas)


@router.post("/empleado/capacitaciones/{capacitacion_id}/completar")
async def completar_curso(capacitacion_id: int, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        await conn.execute(
            """
            INSERT INTO personal.empleado_capacitacion (empleado_id, capacitacion_id, completada, fecha)
            VALUES ($1,$2,true, now())
            ON CONFLICT (empleado_id, capacitacion_id)
            DO UPDATE SET completada = true, fecha = now()
            """,
            usuario["empleado_id"],
            capacitacion_id,
        )
    return {"ok": True}


@router.get("/empleado/documentos")
async def documentos(usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        filas = await conn.fetch(
            "SELECT id, tipo, archivo_id, estado, nota, vence_en, creado_en FROM personal.documentos WHERE empleado_id = $1",
            usuario["empleado_id"],
        )
    return recs(filas)


@router.post("/empleado/documentos")
async def subir_documento(tipo: str, archivo_id: UUID, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO personal.documentos (empleado_id, tipo, archivo_id)
            VALUES ($1,$2,$3) RETURNING id
            """,
            usuario["empleado_id"],
            tipo,
            archivo_id,
        )
    return {"id": str(fila["id"])}


@router.get("/empleado/insumos")
async def insumos(usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        filas = await conn.fetch("SELECT id, nombre, unidad FROM personal.insumos WHERE activo")
    return recs(filas)


@router.post("/empleado/insumos/solicitudes")
async def pedir_insumo(datos: InsumoIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO personal.solicitudes_insumo (empleado_id, insumo_id, cantidad)
            VALUES ($1,$2,$3) RETURNING id, estado
            """,
            usuario["empleado_id"],
            datos.insumo_id,
            datos.cantidad,
        )
    return rec(fila)


@router.post("/empleado/ausencias")
async def ausencia(datos: AusenciaIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO personal.ausencias (empleado_id, desde, hasta, motivo)
            VALUES ($1,$2,$3,$4) RETURNING id, estado
            """,
            usuario["empleado_id"],
            datos.desde,
            datos.hasta,
            datos.motivo,
        )
    return rec(fila)


@router.post("/empleado/ordenes/{orden_id}/cambio-turno")
async def cambio_turno(orden_id: UUID, datos: CambioTurnoIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        orden = await conn.fetchval(
            "SELECT 1 FROM operacion.ordenes WHERE id = $1 AND empleado_id = $2 AND estado = 'CONFIRMADA'",
            orden_id,
            usuario["empleado_id"],
        )
        if not orden:
            raise ApiError(409, "ESTADO_INVALIDO", "Solo se puede ofrecer un servicio confirmado.")
        fila = await conn.fetchrow(
            """
            INSERT INTO operacion.cambios_turno (orden_id, empleado_origen_id, empleado_destino_id, estado)
            VALUES ($1,$2,$3,$4) RETURNING id, estado
            """,
            orden_id,
            usuario["empleado_id"],
            datos.empleado_destino_id,
            "pendiente_destino" if datos.empleado_destino_id else "pendiente_admin",
        )
    return rec(fila)


@router.get("/empleado/ranking")
async def ranking(usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT u.nombre, e.nivel, e.calificacion, e.servicios_completados
            FROM personal.empleados e
            JOIN seguridad.usuarios u ON u.id = e.usuario_id
            WHERE e.estado = 'activo'
            ORDER BY e.calificacion DESC, e.servicios_completados DESC
            LIMIT 20
            """
        )
        propio = await conn.fetchrow(
            "SELECT nivel, calificacion, servicios_completados FROM personal.empleados WHERE id = $1",
            usuario["empleado_id"],
        )
    return {"vos": rec(propio), "ranking": recs(filas)}


@router.put("/empleado/preferencias")
async def preferencias(datos: PreferenciaIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        await conn.execute(
            """
            UPDATE personal.empleados SET
              distancia_max_km = COALESCE($2, distancia_max_km),
              tipos_preferidos = $3,
              disponible = COALESCE($4, disponible),
              actualizado_en = now()
            WHERE id = $1
            """,
            usuario["empleado_id"],
            datos.distancia_max_km,
            datos.tipos_preferidos,
            datos.disponible,
        )
    return {"ok": True}


@router.put("/empleado/perfil")
async def perfil_empleado(datos: PerfilEmpleadoIn, usuario: dict = Depends(empleado_actual)):
    if datos.modalidad and datos.modalidad not in {"independiente", "dependiente"}:
        raise ApiError(422, "MODALIDAD", "La modalidad es independiente o dependiente.")
    async with transaccion() as conn:
        await conn.execute(
            """
            UPDATE personal.empleados SET
              contacto_emergencia_nombre = COALESCE($2, contacto_emergencia_nombre),
              contacto_emergencia_telefono = COALESCE($3, contacto_emergencia_telefono),
              modalidad = COALESCE($4, modalidad),
              actualizado_en = now()
            WHERE id = $1
            """,
            usuario["empleado_id"],
            datos.contacto_emergencia_nombre,
            datos.contacto_emergencia_telefono,
            datos.modalidad,
        )
    return {"ok": True}


@router.put("/empleado/medio-cobro")
async def medio_cobro(datos: MedioCobroIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        params = await obtener_parametros(conn)
        espera = pent(params, "medio_cobro_espera_horas")
        await conn.execute(
            "UPDATE personal.medios_cobro SET activo = false WHERE empleado_id = $1 AND activo",
            usuario["empleado_id"],
        )
        fila = await conn.fetchrow(
            """
            INSERT INTO personal.medios_cobro (empleado_id, tipo, referencia, verificado_en, utilizable_desde)
            VALUES ($1, $2, $3, now(), now() + make_interval(hours => $4))
            RETURNING id, tipo, referencia, utilizable_desde
            """,
            usuario["empleado_id"],
            datos.tipo,
            datos.referencia,
            espera,
        )
    return rec(fila)


@router.post("/empleado/pausa")
async def pausar(datos: PausaIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        await conn.execute(
            "UPDATE personal.empleados SET pausa_hasta = $2, actualizado_en = now() WHERE id = $1",
            usuario["empleado_id"],
            datos.hasta,
        )
    return {"pausa_hasta": None if datos.hasta is None else datos.hasta.isoformat()}


@router.post("/ordenes/{orden_id}/imprevistos")
async def imprevisto(orden_id: UUID, datos: ImprevistoIn, usuario: dict = Depends(empleado_actual)):
    tipos = {"necesito_mas_tiempo", "sin_acceso", "falta_insumo", "dano", "cliente_ausente", "otro"}
    if datos.tipo not in tipos:
        raise ApiError(422, "IMPREVISTO", "Ese tipo de imprevisto no existe.")
    async with transaccion() as conn:
        orden = await conn.fetchrow(
            """
            SELECT id FROM operacion.ordenes
            WHERE id = $1 AND empleado_id = $2
              AND estado IN ('CONFIRMADA', 'EN_CAMINO', 'EN_CURSO')
            """,
            orden_id,
            usuario["empleado_id"],
        )
        if orden is None:
            raise ApiError(404, "NO_ENCONTRADA", "No encontramos el servicio.")
        fila = await conn.fetchrow(
            """
            INSERT INTO operacion.imprevistos (orden_id, empleado_id, tipo, detalle, minutos_extra)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, tipo, estado
            """,
            orden_id,
            usuario["empleado_id"],
            datos.tipo,
            datos.detalle,
            datos.minutos_extra,
        )
    return rec(fila)


@router.post("/empleado/liquidaciones/{liquidacion_id}/items/{item_id}/disputar")
async def disputar_pago(liquidacion_id: UUID, item_id: UUID, datos: DisputaPagoIn, usuario: dict = Depends(empleado_actual)):
    async with transaccion() as conn:
        fila = await disputar_item(conn, usuario["empleado_id"], liquidacion_id, item_id, datos.motivo)
    return rec(fila)
