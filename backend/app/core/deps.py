from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.db import conexion
from app.core.errors import ApiError
from app.core.security import leer_access_token
from app.services.auth import cargar_usuario

_bearer = HTTPBearer(auto_error=False)


async def usuario_actual(
    request: Request,
    credenciales: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if credenciales is None or not credenciales.credentials:
        raise ApiError(401, "NO_AUTENTICADO", "Tenés que iniciar sesión.")
    payload = leer_access_token(credenciales.credentials)
    async with conexion() as conn:
        usuario = await cargar_usuario(conn, UUID(payload["sub"]))
    if usuario is None:
        raise ApiError(401, "NO_AUTENTICADO", "Tenés que iniciar sesión.")
    if usuario["estado"] == "bloqueado" or usuario.get("cliente_bloqueado"):
        raise ApiError(403, "CLIENTE_BLOQUEADO", "La cuenta está bloqueada.")
    if usuario["estado"] != "activo":
        raise ApiError(401, "NO_AUTENTICADO", "Tenés que iniciar sesión.")
    request.state.usuario = usuario
    return usuario


async def sesion_completa(usuario: dict = Depends(usuario_actual)) -> dict:
    if usuario["debe_cambiar_password"]:
        raise ApiError(
            403,
            "PASSWORD_CHANGE_REQUIRED",
            "Tenés que crear una nueva contraseña para continuar.",
        )
    return usuario


def exigir_rol(*roles: str):
    async def _dependencia(usuario: dict = Depends(sesion_completa)) -> dict:
        if usuario["rol"] not in roles:
            raise ApiError(403, "PROHIBIDO", "No tenés permiso para esta acción.")
        return usuario

    return _dependencia


def exigir_permiso(codigo: str):
    async def _dependencia(usuario: dict = Depends(sesion_completa)) -> dict:
        if codigo not in (usuario.get("permisos") or []):
            raise ApiError(403, "PROHIBIDO", "No tenés permiso para esta acción.")
        return usuario

    return _dependencia


async def cliente_actual(usuario: dict = Depends(sesion_completa)) -> dict:
    if usuario["rol"] != "cliente" or not usuario.get("cliente_id"):
        raise ApiError(403, "PROHIBIDO", "Esta acción es del cliente.")
    if usuario.get("cliente_bloqueado"):
        raise ApiError(403, "CLIENTE_BLOQUEADO", "La cuenta está bloqueada.")
    return usuario


async def empleado_actual(usuario: dict = Depends(sesion_completa)) -> dict:
    if usuario["rol"] != "empleado" or not usuario.get("empleado_id"):
        raise ApiError(403, "PROHIBIDO", "Esta acción es del empleado.")
    if usuario.get("empleado_estado") != "activo":
        raise ApiError(403, "EMPLEADO_SUSPENDIDO", "La cuenta de empleado está suspendida.")
    return usuario
