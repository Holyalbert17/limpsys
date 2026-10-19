from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.db import conexion
from app.core.errors import ApiError
from app.core.security import leer_access_token
from app.realtime import hub
from app.services.auth import cargar_usuario
from app.services.ordenes import registrar_ubicacion

router = APIRouter(tags=["Tiempo real"])


async def _usuario(token: str):
    payload = leer_access_token(token)
    async with conexion() as conn:
        return await cargar_usuario(conn, UUID(payload["sub"]))


@router.websocket("/ws/ordenes/{orden_id}")
async def ws_orden(ws: WebSocket, orden_id: UUID, token: str):
    try:
        usuario = await _usuario(token)
    except ApiError:
        await ws.close(code=4401)
        return
    if usuario is None:
        await ws.close(code=4401)
        return
    async with conexion() as conn:
        orden = await conn.fetchrow("SELECT cliente_id, empleado_id, estado FROM operacion.ordenes WHERE id = $1", orden_id)
    if orden is None:
        await ws.close(code=4404)
        return
    permitido = usuario["rol"] in {"admin", "superadmin"}
    if usuario["rol"] == "cliente" and str(orden["cliente_id"]) == str(usuario.get("cliente_id")):
        permitido = True
    if usuario["rol"] == "empleado" and str(orden["empleado_id"]) == str(usuario.get("empleado_id")):
        permitido = True
    if not permitido:
        await ws.close(code=4403)
        return
    await hub.conectar(f"orden:{orden_id}", ws)
    try:
        while True:
            mensaje = await ws.receive_json()
            if mensaje.get("tipo") == "ubicacion" and usuario["rol"] == "empleado" and orden["estado"] in {"EN_CAMINO", "EN_CURSO"}:
                async with conexion() as conn:
                    await registrar_ubicacion(
                        conn,
                        usuario["empleado_id"],
                        float(mensaje["lat"]),
                        float(mensaje["lng"]),
                        orden_id,
                    )
    except WebSocketDisconnect:
        hub.salir(f"orden:{orden_id}", ws)


@router.websocket("/ws/admin/mapa")
async def ws_admin(ws: WebSocket, token: str):
    try:
        usuario = await _usuario(token)
    except ApiError:
        await ws.close(code=4401)
        return
    if usuario is None or "mapa.ver" not in (usuario.get("permisos") or []):
        await ws.close(code=4403)
        return
    await hub.conectar("admin", ws)
    await hub.conectar(f"usuario:{usuario['id']}", ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.salir("admin", ws)
        hub.salir(f"usuario:{usuario['id']}", ws)


@router.websocket("/ws/empleado")
async def ws_empleado(ws: WebSocket, token: str):
    try:
        usuario = await _usuario(token)
    except ApiError:
        await ws.close(code=4401)
        return
    if usuario is None or usuario["rol"] != "empleado":
        await ws.close(code=4403)
        return
    sala = f"empleado:{usuario['empleado_id']}"
    await hub.conectar(sala, ws)
    await hub.conectar(f"usuario:{usuario['id']}", ws)
    try:
        while True:
            mensaje = await ws.receive_json()
            if mensaje.get("tipo") == "ubicacion":
                async with conexion() as conn:
                    await registrar_ubicacion(
                        conn,
                        usuario["empleado_id"],
                        float(mensaje["lat"]),
                        float(mensaje["lng"]),
                        mensaje.get("orden_id"),
                    )
    except WebSocketDisconnect:
        hub.salir(sala, ws)
        hub.salir(f"usuario:{usuario['id']}", ws)
