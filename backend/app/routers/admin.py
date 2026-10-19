from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.db import transaccion
from app.core.deps import exigir_permiso, sesion_completa
from app.core.errors import ApiError
from app.core.util import ahora, ip_valida, rec, recs
from app.services.asignacion import asignar_automatico, asignar_manual, rankear
from app.services.cotizador import simular_margen
from app.services.estado_orden import cambiar_estado
from app.services.finanzas import cerrar_periodo, confirmar_pago_pendiente, encolar_fiscal, generar_liquidacion
from app.services.parametros import PARAMETROS_CRITICOS, guardar_parametro, obtener_parametros
from app.services.personas import crear_admin, crear_cliente, crear_empleado

router = APIRouter(prefix="/admin", tags=["Administración"])


class AsignarIn(BaseModel):
    empleado_id: UUID
    motivo: str | None = None


class ClienteIn(BaseModel):
    nombre: str
    apellido: str = ""
    cedula: str
    telefono: str | None = None
    correo: str | None = None
    direccion: str | None = None
    lat: float | None = None
    lng: float | None = None


class EmpleadoIn(BaseModel):
    nombre: str
    apellido: str = ""
    cedula: str
    telefono: str | None = None
    correo: str | None = None
    zonas: list[int] = []


class SucursalIn(BaseModel):
    nombre: str = Field(min_length=2)
    ciudad: str = Field(min_length=2)


class TipoCambioIn(BaseModel):
    moneda: str = Field(min_length=3, max_length=3)
    tasa_gs: float = Field(gt=0)


class DocumentoRevisionIn(BaseModel):
    estado: str
    vence_en: date | None = None
    nota: str | None = None


class EquipoIn(BaseModel):
    empleado_id: UUID


class ParametroIn(BaseModel):
    clave: str
    valor: object


class ProgramarIn(ParametroIn):
    vigente_desde: str


class ComboIn(BaseModel):
    nombre: str
    descripcion: str = ""
    tipo_precio: str
    valor: float
    acumulable: bool = False
    publico_web: bool = True
    cupo: int | None = None
    tipo_limpieza_id: int | None = None
    elementos: list[int] = []


class PromoIn(BaseModel):
    nombre: str
    tipo: str
    valor: float
    es_porcentaje: bool = True
    tope: float | None = None
    minimo: float | None = None
    segmento: str = "todos"
    zona_id: int | None = None
    cupo: int | None = None
    acumulable: bool = False
    prioridad: int = 0


class CuponIn(BaseModel):
    codigo: str
    nombre: str
    es_porcentaje: bool = True
    valor: float
    tope: float | None = None
    minimo: float | None = None
    uso_maximo: int | None = None
    uso_por_cliente: int = 1
    acumulable: bool = False


class ContenidoIn(BaseModel):
    clave: str
    idioma: str = "es"
    valor: object


class ResolverIn(BaseModel):
    estado: str
    resolucion: str | None = None
    monto: float | None = None


class SancionIn(BaseModel):
    motivo: str
    suspender: bool = False


class AdminUserIn(BaseModel):
    cedula: str
    nombre: str
    apellido: str = ""
    rol: str = "admin"


def _ip(request: Request) -> str | None:
    return ip_valida(request.client.host if request.client else None)


@router.get("/solicitudes")
async def solicitudes(
    estado: str | None = None,
    usuario: dict = Depends(exigir_permiso("ordenes.ver")),
):
    async with transaccion() as conn:
        filtro = "WHERE o.estado = ANY($1::text[])"
        estados = [estado] if estado else ["ENVIADA", "EN_REVISION", "ASIGNADA"]
        filas = await conn.fetch(
            f"""
            SELECT o.id, o.numero, o.estado, o.fecha, o.hora_inicio, o.total, o.es_emergencia,
                   o.creado_en, o.enviada_en, c.nombre, c.apellido, z.nombre AS zona
            FROM operacion.ordenes o
            JOIN crm.clientes c ON c.id = o.cliente_id
            LEFT JOIN crm.direcciones d ON d.id = o.direccion_id
            LEFT JOIN catalogo.zonas z ON z.id = d.zona_id
            {filtro}
            ORDER BY o.es_emergencia DESC, o.creado_en
            """,
            estados,
        )
    return recs(filas)


@router.post("/ordenes/{orden_id}/asignar")
async def asignar(orden_id: UUID, datos: AsignarIn, usuario: dict = Depends(exigir_permiso("ordenes.asignar"))):
    async with transaccion() as conn:
        oferta = await asignar_manual(conn, orden_id, datos.empleado_id, usuario["id"], datos.motivo)
    return {"oferta_id": str(oferta)}


@router.post("/ordenes/{orden_id}/asignar-auto")
async def asignar_auto(orden_id: UUID, usuario: dict = Depends(exigir_permiso("ordenes.asignar"))):
    async with transaccion() as conn:
        oferta = await asignar_automatico(conn, orden_id, usuario["id"])
    if oferta is None:
        raise ApiError(422, "SIN_CANDIDATOS", "No hay un empleado disponible para esta solicitud.")
    return {"oferta_id": str(oferta)}


@router.post("/ordenes/{orden_id}/reasignar")
async def reasignar(orden_id: UUID, datos: AsignarIn, usuario: dict = Depends(exigir_permiso("ordenes.reasignar"))):
    if not datos.motivo:
        raise ApiError(422, "MOTIVO", "La reasignación pide un motivo.")
    async with transaccion() as conn:
        oferta = await asignar_manual(conn, orden_id, datos.empleado_id, usuario["id"], datos.motivo)
    return {"oferta_id": str(oferta)}


@router.get("/ordenes/{orden_id}/candidatos")
async def candidatos(orden_id: UUID, usuario: dict = Depends(exigir_permiso("ordenes.ver"))):
    async with transaccion() as conn:
        orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden_id)
        if orden is None:
            raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
        return await rankear(conn, orden)


@router.get("/mapa")
async def mapa(usuario: dict = Depends(exigir_permiso("mapa.ver"))):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT e.id, u.nombre, e.disponible, e.estado,
                   o.id AS orden_id, o.estado AS orden_estado, o.numero,
                   ub.lat, ub.lng, ub.fecha_hora
            FROM personal.empleados e
            JOIN seguridad.usuarios u ON u.id = e.usuario_id
            LEFT JOIN LATERAL (
              SELECT id, estado, numero FROM operacion.ordenes
              WHERE empleado_id = e.id AND estado IN ('CONFIRMADA','EN_CAMINO','EN_CURSO')
              ORDER BY actualizado_en DESC LIMIT 1
            ) o ON true
            LEFT JOIN LATERAL (
              SELECT lat, lng, fecha_hora FROM operacion.ubicaciones_empleado
              WHERE empleado_id = e.id ORDER BY fecha_hora DESC LIMIT 1
            ) ub ON true
            WHERE e.estado = 'activo'
            """
        )
    return recs(filas)


@router.post("/llegadas/{llegada_id}/revisar")
async def revisar_llegada(llegada_id: UUID, aprobar: bool, usuario: dict = Depends(exigir_permiso("ordenes.asignar"))):
    async with transaccion() as conn:
        llegada = await conn.fetchrow("SELECT * FROM operacion.registro_llegadas WHERE id = $1", llegada_id)
        if llegada is None:
            raise ApiError(404, "NO_ENCONTRADA", "No encontramos el registro.")
        await conn.execute(
            "UPDATE operacion.registro_llegadas SET revisado = $2, revisado_por = $3 WHERE id = $1",
            llegada_id,
            aprobar,
            usuario["id"],
        )
        if aprobar and llegada["tipo"] == "check-in":
            await cambiar_estado(conn, llegada["orden_id"], "EN_CURSO", usuario["id"], nota="Check-in manual aprobado")
    return {"ok": True}


@router.get("/clientes")
async def clientes(q: str | None = None, usuario: dict = Depends(exigir_permiso("clientes.ver"))):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT c.id, c.nombre, c.apellido, c.telefono, c.tipo, c.bloqueado, u.cedula, u.estado, c.creado_en
            FROM crm.clientes c
            JOIN seguridad.usuarios u ON u.id = c.usuario_id
            WHERE $1::text IS NULL OR c.nombre ILIKE '%' || $1 || '%' OR u.cedula = $1
            ORDER BY c.creado_en DESC
            LIMIT 100
            """,
            q,
        )
    return recs(filas)


@router.post("/clientes")
async def alta_cliente(datos: ClienteIn, request: Request, usuario: dict = Depends(exigir_permiso("clientes.crear"))):
    async with transaccion() as conn:
        usuario_id, cliente_id = await crear_cliente(
            conn,
            cedula=datos.cedula,
            nombre=datos.nombre,
            apellido=datos.apellido,
            telefono=datos.telefono,
            correo=datos.correo,
            password=datos.cedula,
            debe_cambiar=True,
            creado_por=usuario["id"],
        )
        if datos.direccion and datos.lat is not None and datos.lng is not None:
            from app.domain.geo import elegir_zona

            zonas = recs(await conn.fetch("SELECT * FROM catalogo.zonas WHERE activo"))
            zona = elegir_zona(datos.lat, datos.lng, zonas)
            await conn.execute(
                """
                INSERT INTO crm.direcciones (cliente_id, direccion, lat, lng, zona_id, es_principal)
                VALUES ($1,$2,$3,$4,$5,true)
                """,
                cliente_id,
                datos.direccion,
                datos.lat,
                datos.lng,
                None if zona is None else zona["id"],
            )
        await conn.execute(
            """
            INSERT INTO sistema.auditoria (usuario_id, accion, entidad, entidad_id, despues, ip)
            VALUES ($1, 'cliente.crear', 'cliente', $2, $3::jsonb, $4::inet)
            """,
            usuario["id"],
            str(cliente_id),
            {"cedula": datos.cedula},
            _ip(request),
        )
    return {
        "usuario_id": str(usuario_id),
        "cliente_id": str(cliente_id),
        "mensaje": "La contraseña inicial es la cédula y caduca según el parámetro. El mensaje de bienvenida no incluye la clave.",
    }


@router.post("/clientes/{cliente_id}/bloquear")
async def bloquear_cliente(cliente_id: UUID, bloqueado: bool, usuario: dict = Depends(exigir_permiso("clientes.bloquear"))):
    async with transaccion() as conn:
        await conn.execute("UPDATE crm.clientes SET bloqueado = $2 WHERE id = $1", cliente_id, bloqueado)
        await conn.execute(
            """
            UPDATE seguridad.usuarios SET estado = $2
            WHERE id = (SELECT usuario_id FROM crm.clientes WHERE id = $1)
            """,
            cliente_id,
            "bloqueado" if bloqueado else "activo",
        )
    return {"bloqueado": bloqueado}


@router.post("/clientes/{cliente_id}/restablecer")
async def restablecer(cliente_id: UUID, usuario: dict = Depends(exigir_permiso("clientes.editar"))):
    from datetime import datetime, timezone

    from app.core.security import hash_password
    from app.services.parametros import pent

    async with transaccion() as conn:
        fila = await conn.fetchrow(
            "SELECT u.id, u.cedula FROM crm.clientes c JOIN seguridad.usuarios u ON u.id = c.usuario_id WHERE c.id = $1",
            cliente_id,
        )
        if fila is None:
            raise ApiError(404, "NO_ENCONTRADO", "No encontramos el cliente.")
        params = await obtener_parametros(conn)
        expira = datetime.now(timezone.utc) + timedelta(hours=pent(params, "password_inicial_horas"))
        await conn.execute(
            """
            UPDATE seguridad.usuarios
            SET password_hash = $2, debe_cambiar_password = true, password_expira_en = $3,
                intentos_fallidos = 0, bloqueado_hasta = NULL
            WHERE id = $1
            """,
            fila["id"],
            hash_password(fila["cedula"]),
            expira,
        )
        await conn.execute("UPDATE seguridad.refresh_tokens SET revocado = true WHERE usuario_id = $1", fila["id"])
    return {"mensaje": "La contraseña volvió a ser la cédula. Las sesiones anteriores quedaron cerradas."}


@router.get("/empleados")
async def empleados(usuario: dict = Depends(exigir_permiso("empleados.ver"))):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT e.id, u.nombre, u.apellido, u.cedula, e.calificacion, e.nivel, e.estado,
                   e.disponible, e.servicios_completados, e.sucursal_operacion_id
            FROM personal.empleados e
            JOIN seguridad.usuarios u ON u.id = e.usuario_id
            WHERE ($1::uuid IS NULL OR e.sucursal_operacion_id = $1)
            ORDER BY u.nombre
            """,
            usuario.get("sucursal_operacion_id"),
        )
    return recs(filas)


@router.post("/empleados")
async def alta_empleado(datos: EmpleadoIn, usuario: dict = Depends(exigir_permiso("empleados.crear"))):
    async with transaccion() as conn:
        usuario_id, empleado_id = await crear_empleado(
            conn,
            cedula=datos.cedula,
            nombre=datos.nombre,
            apellido=datos.apellido,
            telefono=datos.telefono,
            correo=datos.correo,
            creado_por=usuario["id"],
            zonas=datos.zonas,
        )
    return {
        "usuario_id": str(usuario_id),
        "empleado_id": str(empleado_id),
        "mensaje": "La contraseña inicial es la cédula. No se envía en el mensaje de bienvenida.",
    }


@router.post("/empleados/{empleado_id}/sancionar")
async def sancionar(empleado_id: UUID, datos: SancionIn, usuario: dict = Depends(exigir_permiso("empleados.sancionar"))):
    async with transaccion() as conn:
        await conn.execute(
            "INSERT INTO personal.sanciones (empleado_id, tipo, motivo, creado_por) VALUES ($1, 'manual', $2, $3)",
            empleado_id,
            datos.motivo,
            usuario["id"],
        )
        if datos.suspender:
            await conn.execute(
                "UPDATE personal.empleados SET estado = 'suspendido', disponible = false WHERE id = $1",
                empleado_id,
            )
    return {"ok": True}


@router.get("/finanzas/planilla")
async def planilla(
    desde: date | None = None,
    hasta: date | None = None,
    usuario: dict = Depends(exigir_permiso("finanzas.ver")),
):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT p.*, o.numero, o.fecha, o.estado AS estado_orden
            FROM finanzas.planilla_ingresos p
            JOIN operacion.ordenes o ON o.id = p.orden_id
            WHERE ($1::date IS NULL OR o.fecha >= $1) AND ($2::date IS NULL OR o.fecha <= $2)
            ORDER BY o.fecha DESC NULLS LAST
            LIMIT 300
            """,
            desde,
            hasta,
        )
    return recs(filas)


@router.post("/finanzas/pagos/{pago_id}/confirmar")
async def confirmar_pago(pago_id: UUID, usuario: dict = Depends(exigir_permiso("finanzas.conciliar"))):
    async with transaccion() as conn:
        pago = await confirmar_pago_pendiente(conn, pago_id, usuario["id"])
    return rec(pago)


@router.post("/liquidaciones/generar")
async def liquidar(empleado_id: UUID, desde: date, hasta: date, usuario: dict = Depends(exigir_permiso("finanzas.liquidar"))):
    async with transaccion() as conn:
        fila = await generar_liquidacion(conn, empleado_id, desde, hasta)
    return rec(fila)


@router.post("/liquidaciones/{liquidacion_id}/pagar")
async def pagar_liquidacion(liquidacion_id: UUID, usuario: dict = Depends(exigir_permiso("finanzas.liquidar"))):
    async with transaccion() as conn:
        medio = await conn.fetchrow(
            """
            SELECT m.utilizable_desde
            FROM finanzas.liquidaciones l
            JOIN personal.medios_cobro m ON m.empleado_id = l.empleado_id AND m.activo
            WHERE l.id = $1
            ORDER BY m.creado_en DESC
            LIMIT 1
            """,
            liquidacion_id,
        )
        if medio and medio["utilizable_desde"] and medio["utilizable_desde"] > ahora():
            raise ApiError(409, "MEDIO_COBRO", "El medio de cobro todavía no se puede usar.")
        fila = await conn.fetchrow(
            """
            UPDATE finanzas.liquidaciones SET estado = 'pagada', pagada_en = now()
            WHERE id = $1 AND estado IN ('generada', 'aprobada') RETURNING *
            """,
            liquidacion_id,
        )
    if fila is None:
        raise ApiError(409, "ESTADO_INVALIDO", "La liquidación no está pendiente de pago.")
    return rec(fila)


@router.get("/parametros")
async def listar_parametros(usuario: dict = Depends(exigir_permiso("parametros.ver"))):
    async with transaccion() as conn:
        filas = await conn.fetch("SELECT clave, valor, grupo, descripcion, actualizado_en FROM sistema.parametros ORDER BY grupo, clave")
    return recs(filas)


@router.put("/parametros")
async def editar_parametro(datos: ParametroIn, request: Request, usuario: dict = Depends(exigir_permiso("parametros.editar"))):
    if datos.clave in PARAMETROS_CRITICOS and "parametros.criticos" not in usuario["permisos"]:
        raise ApiError(403, "PROHIBIDO", "Ese parámetro es crítico.")
    async with transaccion() as conn:
        await guardar_parametro(conn, datos.clave, datos.valor, usuario["id"], _ip(request))
    return {"ok": True}


@router.get("/parametros/historial")
async def historial(clave: str | None = None, usuario: dict = Depends(exigir_permiso("parametros.ver"))):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT clave, valor_anterior, valor_nuevo, usuario_id, fecha
            FROM sistema.parametros_historial
            WHERE $1::text IS NULL OR clave = $1
            ORDER BY fecha DESC LIMIT 200
            """,
            clave,
        )
    return recs(filas)


@router.post("/parametros/programar")
async def programar(datos: ProgramarIn, usuario: dict = Depends(exigir_permiso("parametros.editar"))):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO sistema.parametros_programados (clave, valor, vigente_desde, creado_por)
            VALUES ($1, $2::jsonb, $3, $4) RETURNING id, vigente_desde
            """,
            datos.clave,
            datos.valor,
            datetime.fromisoformat(datos.vigente_desde),
            usuario["id"],
        )
    return rec(fila)


@router.get("/combos")
async def combos(usuario: dict = Depends(exigir_permiso("promociones.editar"))):
    async with transaccion() as conn:
        return recs(await conn.fetch("SELECT * FROM comercial.combos ORDER BY creado_en DESC"))


@router.post("/combos")
async def crear_combo(datos: ComboIn, usuario: dict = Depends(exigir_permiso("promociones.editar"))):
    if datos.tipo_precio not in {"fijo", "porcentaje"}:
        raise ApiError(422, "COMBO", "El tipo de precio es fijo o porcentaje.")
    async with transaccion() as conn:
        combo_id = await conn.fetchval(
            """
            INSERT INTO comercial.combos (nombre, descripcion, tipo_precio, valor, acumulable, publico_web, cupo)
            VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id
            """,
            datos.nombre,
            datos.descripcion,
            datos.tipo_precio,
            datos.valor,
            datos.acumulable,
            datos.publico_web,
            datos.cupo,
        )
        if datos.tipo_limpieza_id:
            await conn.execute(
                "INSERT INTO comercial.combo_items (combo_id, tipo_limpieza_id) VALUES ($1,$2)",
                combo_id,
                datos.tipo_limpieza_id,
            )
        for elemento_id in datos.elementos:
            await conn.execute(
                "INSERT INTO comercial.combo_items (combo_id, elemento_id) VALUES ($1,$2)",
                combo_id,
                elemento_id,
            )
    return {"id": str(combo_id)}


@router.post("/combos/{combo_id}/pausar")
async def pausar_combo(combo_id: UUID, activo: bool, usuario: dict = Depends(exigir_permiso("promociones.editar"))):
    async with transaccion() as conn:
        await conn.execute("UPDATE comercial.combos SET activo = $2 WHERE id = $1", combo_id, activo)
    return {"activo": activo}


@router.post("/promociones/simular")
async def simular(combo_id: UUID, usuario: dict = Depends(exigir_permiso("promociones.editar"))):
    async with transaccion() as conn:
        return await simular_margen(conn, combo_id)


@router.post("/promociones")
async def crear_promo(datos: PromoIn, usuario: dict = Depends(exigir_permiso("promociones.editar"))):
    async with transaccion() as conn:
        promo_id = await conn.fetchval(
            """
            INSERT INTO comercial.promociones (
              nombre, tipo, valor, es_porcentaje, tope, minimo, segmento, zona_id, cupo, acumulable, prioridad
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING id
            """,
            datos.nombre,
            datos.tipo,
            datos.valor,
            datos.es_porcentaje,
            datos.tope,
            datos.minimo,
            datos.segmento,
            datos.zona_id,
            datos.cupo,
            datos.acumulable,
            datos.prioridad,
        )
    return {"id": str(promo_id)}


@router.post("/cupones")
async def crear_cupon(datos: CuponIn, usuario: dict = Depends(exigir_permiso("promociones.editar"))):
    async with transaccion() as conn:
        cupon_id = await conn.fetchval(
            """
            INSERT INTO comercial.cupones (
              codigo, nombre, es_porcentaje, valor, tope, minimo, uso_maximo, uso_por_cliente, acumulable
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id
            """,
            datos.codigo.strip().upper(),
            datos.nombre,
            datos.es_porcentaje,
            datos.valor,
            datos.tope,
            datos.minimo,
            datos.uso_maximo,
            datos.uso_por_cliente,
            datos.acumulable,
        )
    return {"id": str(cupon_id)}


@router.post("/cupones/masivo")
async def cupones_masivos(prefijo: str, cantidad: int = Query(ge=1, le=500), valor: float = 10, usuario: dict = Depends(exigir_permiso("promociones.editar"))):
    import secrets

    creados = []
    async with transaccion() as conn:
        for _ in range(cantidad):
            codigo = f"{prefijo}{secrets.token_hex(3)}".upper()
            await conn.execute(
                """
                INSERT INTO comercial.cupones (codigo, nombre, es_porcentaje, valor, uso_por_cliente)
                VALUES ($1, $2, true, $3, 1)
                """,
                codigo,
                f"Campaña {prefijo}",
                valor,
            )
            creados.append(codigo)
    return {"codigos": creados}


@router.get("/reportes/kpis")
async def kpis(desde: date | None = None, hasta: date | None = None, usuario: dict = Depends(exigir_permiso("reportes.ver"))):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            SELECT
              count(*) FILTER (WHERE estado NOT IN ('BORRADOR')) AS ordenes,
              count(*) FILTER (WHERE estado = 'CANCELADA') AS canceladas,
              count(*) FILTER (WHERE estado IN ('PAGADA','CALIFICADA','COMPLETADA')) AS terminadas,
              COALESCE(AVG(margen_calculado) FILTER (WHERE margen_calculado IS NOT NULL), 0) AS margen_promedio,
              COALESCE(AVG(calificacion_promedio) FILTER (WHERE calificacion_promedio IS NOT NULL), 0) AS calificacion
            FROM operacion.ordenes
            WHERE ($1::date IS NULL OR fecha >= $1) AND ($2::date IS NULL OR fecha <= $2)
            """,
            desde,
            hasta,
        )
        puntuales = await conn.fetchrow(
            """
            SELECT
              count(*) FILTER (WHERE o.checkin_en IS NOT NULL AND o.fecha IS NOT NULL AND o.hora_inicio IS NOT NULL
                AND o.checkin_en <= (o.fecha + o.hora_inicio) AT TIME ZONE 'America/Asuncion' + interval '15 minutes') AS a_tiempo,
              count(*) FILTER (WHERE o.checkin_en IS NOT NULL) AS con_checkin
            FROM operacion.ordenes o
            WHERE o.estado IN ('EN_CURSO','COMPLETADA','PAGADA','CALIFICADA')
              AND ($1::date IS NULL OR o.fecha >= $1) AND ($2::date IS NULL OR o.fecha <= $2)
            """,
            desde,
            hasta,
        )
    ordenes = int(fila["ordenes"] or 0)
    canceladas = int(fila["canceladas"] or 0)
    con_checkin = int(puntuales["con_checkin"] or 0)
    return {
        "ordenes": ordenes,
        "tasa_cancelacion": (canceladas / ordenes) if ordenes else 0,
        "margen_promedio": float(fila["margen_promedio"] or 0),
        "calificacion_promedio": float(fila["calificacion"] or 0),
        "puntualidad": (int(puntuales["a_tiempo"] or 0) / con_checkin) if con_checkin else None,
        "metas": {
            "margen": 0.35,
            "cancelacion": 0.08,
            "puntualidad": 0.95,
            "calificacion": 4.6,
        },
    }


@router.get("/reportes/ordenes")
async def reporte_ordenes(
    desde: date | None = None,
    hasta: date | None = None,
    formato: str = "json",
    usuario: dict = Depends(exigir_permiso("reportes.exportar")),
):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT o.numero, o.estado, o.fecha, o.total, o.margen_calculado, c.nombre, z.nombre AS zona
            FROM operacion.ordenes o
            JOIN crm.clientes c ON c.id = o.cliente_id
            LEFT JOIN crm.direcciones d ON d.id = o.direccion_id
            LEFT JOIN catalogo.zonas z ON z.id = d.zona_id
            WHERE ($1::date IS NULL OR o.fecha >= $1) AND ($2::date IS NULL OR o.fecha <= $2)
            ORDER BY o.creado_en DESC
            LIMIT 2000
            """,
            desde,
            hasta,
        )
    if formato == "csv":
        lineas = ["numero,estado,fecha,total,margen,cliente,zona"]
        for fila in filas:
            lineas.append(
                f"{fila['numero']},{fila['estado']},{fila['fecha'] or ''},{fila['total']},{fila['margen_calculado'] or ''},{fila['nombre']},{fila['zona'] or ''}"
            )
        return Response("\ufeff" + "\n".join(lineas), media_type="text/csv")
    return recs(filas)


@router.get("/reportes/promociones")
async def reporte_promos(usuario: dict = Depends(exigir_permiso("reportes.ver"))):
    async with transaccion() as conn:
        combos = await conn.fetch("SELECT nombre, usos, valor, tipo_precio, activo FROM comercial.combos ORDER BY usos DESC")
        cupones = await conn.fetch("SELECT codigo, nombre, usos, valor, activo FROM comercial.cupones ORDER BY usos DESC")
    return {"combos": recs(combos), "cupones": recs(cupones)}


@router.get("/incidentes")
async def incidentes(usuario: dict = Depends(exigir_permiso("incidentes.resolver"))):
    async with transaccion() as conn:
        return recs(await conn.fetch("SELECT * FROM operacion.incidentes ORDER BY creado_en DESC LIMIT 100"))


@router.post("/incidentes/{incidente_id}/resolver")
async def resolver(incidente_id: UUID, datos: ResolverIn, usuario: dict = Depends(exigir_permiso("incidentes.resolver"))):
    if datos.estado not in {"resuelto", "rechazado", "en_revision"}:
        raise ApiError(422, "ESTADO", "El estado de resolución no es válido.")
    async with transaccion() as conn:
        await conn.execute(
            """
            UPDATE operacion.incidentes
            SET estado = $2, resolucion = $3, monto = $4, resuelto_por = $5, resuelto_en = now()
            WHERE id = $1
            """,
            incidente_id,
            datos.estado,
            datos.resolucion,
            datos.monto,
            usuario["id"],
        )
    return {"ok": True}


@router.get("/auditoria")
async def auditoria(usuario: dict = Depends(exigir_permiso("auditoria.ver"))):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT a.fecha, a.accion, a.entidad, a.entidad_id, u.nombre
            FROM sistema.auditoria a
            LEFT JOIN seguridad.usuarios u ON u.id = a.usuario_id
            ORDER BY a.fecha DESC LIMIT 200
            """
        )
    return recs(filas)


@router.get("/contenido")
async def listar_contenido(usuario: dict = Depends(exigir_permiso("contenido.editar"))):
    async with transaccion() as conn:
        return recs(await conn.fetch("SELECT clave, idioma, valor, actualizado_en FROM contenido.contenido_sitio ORDER BY clave"))


@router.put("/contenido")
async def guardar_contenido(datos: ContenidoIn, usuario: dict = Depends(exigir_permiso("contenido.editar"))):
    async with transaccion() as conn:
        await conn.execute(
            """
            INSERT INTO contenido.contenido_sitio (clave, idioma, valor)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (clave, idioma) DO UPDATE SET valor = $3::jsonb, actualizado_en = now()
            """,
            datos.clave,
            datos.idioma,
            datos.valor,
        )
    return {"ok": True}


@router.post("/usuarios")
async def alta_admin(datos: AdminUserIn, usuario: dict = Depends(exigir_permiso("usuarios.admin"))):
    async with transaccion() as conn:
        usuario_id = await crear_admin(
            conn,
            cedula=datos.cedula,
            nombre=datos.nombre,
            apellido=datos.apellido,
            rol=datos.rol,
            creado_por=usuario["id"],
        )
    return {"usuario_id": str(usuario_id), "mensaje": "La contraseña inicial es la cédula."}


@router.get("/postulaciones")
async def postulaciones(usuario: dict = Depends(exigir_permiso("empleados.ver"))):
    async with transaccion() as conn:
        return recs(await conn.fetch("SELECT * FROM contenido.postulaciones ORDER BY creado_en DESC"))


@router.patch("/postulaciones/{postulacion_id}")
async def revisar_postulacion(postulacion_id: UUID, estado: str, usuario: dict = Depends(exigir_permiso("empleados.editar"))):
    if estado not in {"en_revision", "aceptada", "rechazada"}:
        raise ApiError(422, "ESTADO", "El estado no es válido.")
    async with transaccion() as conn:
        await conn.execute("UPDATE contenido.postulaciones SET estado = $2 WHERE id = $1", postulacion_id, estado)
    return {"estado": estado}


@router.post("/facturas/{factura_id}/emitir")
async def emitir(factura_id: UUID, cdc: str = "", usuario: dict = Depends(exigir_permiso("facturacion.emitir"))):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE finanzas.facturas SET estado = 'emitida', cdc = NULLIF($2, '')
            WHERE id = $1 RETURNING id, numero, estado, cdc
            """,
            factura_id,
            cdc,
        )
        if fila is not None:
            await encolar_fiscal(conn, fila["id"], "factura", "enviado" if cdc else "pendiente")
    if fila is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la factura.")
    return rec(fila)


@router.post("/ausencias/{ausencia_id}/resolver")
async def resolver_ausencia(ausencia_id: UUID, aprobar: bool, usuario: dict = Depends(exigir_permiso("empleados.editar"))):
    async with transaccion() as conn:
        await conn.execute(
            "UPDATE personal.ausencias SET estado = $2, resuelto_por = $3 WHERE id = $1",
            ausencia_id,
            "aprobada" if aprobar else "rechazada",
            usuario["id"],
        )
    return {"ok": True}


@router.post("/cambios-turno/{cambio_id}/resolver")
async def resolver_cambio(cambio_id: UUID, aprobar: bool, usuario: dict = Depends(exigir_permiso("ordenes.reasignar"))):
    async with transaccion() as conn:
        cambio = await conn.fetchrow("SELECT * FROM operacion.cambios_turno WHERE id = $1", cambio_id)
        if cambio is None:
            raise ApiError(404, "NO_ENCONTRADO", "No encontramos el cambio de turno.")
        if aprobar and cambio["empleado_destino_id"]:
            await conn.execute(
                "UPDATE operacion.ordenes SET empleado_id = $2, actualizado_en = now() WHERE id = $1",
                cambio["orden_id"],
                cambio["empleado_destino_id"],
            )
        await conn.execute(
            "UPDATE operacion.cambios_turno SET estado = $2 WHERE id = $1",
            cambio_id,
            "aprobado" if aprobar else "rechazado",
        )
    return {"ok": True}


@router.post("/liquidaciones/cerrar-periodo")
async def liquidar_periodo(ciclo: str, fecha: date, usuario: dict = Depends(exigir_permiso("finanzas.liquidar"))):
    async with transaccion() as conn:
        filas = await cerrar_periodo(conn, ciclo, fecha)
    return recs(filas)


@router.post("/liquidaciones/{liquidacion_id}/revisar")
async def revisar_liquidacion(liquidacion_id: UUID, usuario: dict = Depends(exigir_permiso("finanzas.liquidar"))):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE finanzas.liquidaciones SET estado = 'en_revision'
            WHERE id = $1 AND estado = 'generada' RETURNING *
            """,
            liquidacion_id,
        )
    if fila is None:
        raise ApiError(409, "ESTADO_INVALIDO", "La liquidación no está generada.")
    return rec(fila)


@router.post("/liquidaciones/{liquidacion_id}/aprobar")
async def aprobar_liquidacion(liquidacion_id: UUID, usuario: dict = Depends(exigir_permiso("finanzas.liquidar"))):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE finanzas.liquidaciones SET estado = 'aprobada'
            WHERE id = $1 AND estado = 'en_revision' RETURNING *
            """,
            liquidacion_id,
        )
    if fila is None:
        raise ApiError(409, "ESTADO_INVALIDO", "La liquidación no está en revisión.")
    return rec(fila)


@router.post("/liquidaciones/{liquidacion_id}/conciliar")
async def conciliar_liquidacion(liquidacion_id: UUID, usuario: dict = Depends(exigir_permiso("finanzas.conciliar"))):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE finanzas.liquidaciones SET estado = 'conciliada'
            WHERE id = $1 AND estado = 'pagada' RETURNING *
            """,
            liquidacion_id,
        )
    if fila is None:
        raise ApiError(409, "ESTADO_INVALIDO", "La liquidación no está pagada.")
    return rec(fila)


@router.get("/liquidaciones/{liquidacion_id}/banco")
async def archivo_banco(liquidacion_id: UUID, usuario: dict = Depends(exigir_permiso("finanzas.liquidar"))):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            SELECT l.total, u.cedula, u.nombre, u.apellido, m.referencia
            FROM finanzas.liquidaciones l
            JOIN personal.empleados e ON e.id = l.empleado_id
            JOIN seguridad.usuarios u ON u.id = e.usuario_id
            LEFT JOIN personal.medios_cobro m ON m.empleado_id = e.id AND m.activo
            WHERE l.id = $1
            """,
            liquidacion_id,
        )
    if fila is None:
        raise ApiError(404, "NO_ENCONTRADA", "No encontramos la liquidación.")
    referencia = fila["referencia"] or ""
    cuerpo = "cedula,nombre,monto,referencia\n"
    cuerpo += f"{fila['cedula']},{fila['nombre']} {fila['apellido']},{int(fila['total'])},{referencia}\n"
    return Response(content=cuerpo, media_type="text/csv")


@router.get("/sucursales-operacion")
async def sucursales(usuario: dict = Depends(exigir_permiso("empleados.ver"))):
    async with transaccion() as conn:
        filas = await conn.fetch("SELECT * FROM catalogo.sucursales_operacion ORDER BY nombre")
    return recs(filas)


@router.post("/sucursales-operacion")
async def crear_sucursal(datos: SucursalIn, usuario: dict = Depends(exigir_permiso("usuarios.admin"))):
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            INSERT INTO catalogo.sucursales_operacion (nombre, ciudad)
            VALUES ($1, $2) RETURNING *
            """,
            datos.nombre,
            datos.ciudad,
        )
    return rec(fila)


@router.post("/tipos-cambio")
async def tipo_cambio(datos: TipoCambioIn, usuario: dict = Depends(exigir_permiso("parametros.ver"))):
    async with transaccion() as conn:
        moneda = datos.moneda.upper()
        existe = await conn.fetchval("SELECT 1 FROM catalogo.monedas WHERE codigo = $1", moneda)
        if not existe:
            raise ApiError(422, "MONEDA", "Esa moneda no está en el catálogo.")
        await conn.execute(
            "UPDATE catalogo.tipos_cambio SET vigente_hasta = now() WHERE moneda = $1 AND vigente_hasta IS NULL",
            moneda,
        )
        await conn.execute("UPDATE catalogo.monedas SET habilitada = true WHERE codigo = $1", moneda)
        fila = await conn.fetchrow(
            """
            INSERT INTO catalogo.tipos_cambio (moneda, tasa_gs)
            VALUES ($1, $2) RETURNING id, moneda, tasa_gs, vigente_desde
            """,
            moneda,
            datos.tasa_gs,
        )
    return rec(fila)


@router.patch("/documentos/{documento_id}")
async def revisar_documento(documento_id: UUID, datos: DocumentoRevisionIn, usuario: dict = Depends(exigir_permiso("empleados.editar"))):
    if datos.estado not in {"pendiente", "aprobado", "rechazado"}:
        raise ApiError(422, "ESTADO", "El estado del documento no es válido.")
    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            UPDATE personal.documentos
            SET estado = $2, vence_en = COALESCE($3, vence_en), nota = COALESCE($4, nota)
            WHERE id = $1
            RETURNING id, estado, vence_en
            """,
            documento_id,
            datos.estado,
            datos.vence_en,
            datos.nota,
        )
    if fila is None:
        raise ApiError(404, "NO_ENCONTRADO", "No encontramos el documento.")
    return rec(fila)


@router.post("/ordenes/{orden_id}/equipo")
async def sumar_equipo(orden_id: UUID, datos: EquipoIn, usuario: dict = Depends(exigir_permiso("ordenes.asignar"))):
    async with transaccion() as conn:
        orden = await conn.fetchrow("SELECT * FROM operacion.ordenes WHERE id = $1", orden_id)
        if orden is None:
            raise ApiError(404, "NO_ENCONTRADA", "No encontramos la solicitud.")
        cupo = int(orden["cantidad_empleados"] or 1)
        actual = await conn.fetchval("SELECT count(*) FROM operacion.orden_empleados WHERE orden_id = $1", orden_id)
        if actual >= cupo:
            raise ApiError(409, "EQUIPO", "El equipo de esta solicitud ya está completo.")
        await conn.execute(
            """
            INSERT INTO operacion.orden_empleados (orden_id, empleado_id, rol)
            VALUES ($1, $2, 'apoyo')
            ON CONFLICT (orden_id, empleado_id) DO NOTHING
            """,
            orden_id,
            datos.empleado_id,
        )
    return {"ok": True}
