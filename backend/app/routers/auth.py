from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.db import transaccion
from app.core.deps import sesion_completa, usuario_actual
from app.core.errors import ApiError
from app.core.security import hash_password
from app.core.util import ip_valida, rec
from app.services.auth import (
    cambiar_password,
    cerrar_sesion,
    crear_otp,
    login,
    pedir_recuperacion,
    rotar_refresh,
    verificar_otp,
)
from app.services.auth import _publico

router = APIRouter(prefix="/auth", tags=["Autenticación"])


class LoginIn(BaseModel):
    cedula: str
    password: str
    captcha_token: str | None = None
    dispositivo: str | None = None


class PasswordIn(BaseModel):
    password_actual: str | None = None
    password_nueva: str = Field(min_length=8)
    confirmar: str


class RecuperarIn(BaseModel):
    cedula: str


class OtpIn(BaseModel):
    cedula: str
    codigo: str
    password_nueva: str


class RefreshIn(BaseModel):
    refresh_token: str | None = None


def _cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "refresh_token",
        token,
        httponly=True,
        secure=not settings.es_desarrollo,
        samesite="strict",
        max_age=settings.jwt_refresh_days * 86400,
        path="/api/v1/auth",
    )


def _sesion_respuesta(payload: dict, request: Request, response: Response) -> dict:
    token = payload["refresh_token"]
    _cookie(response, token)
    if request.headers.get("x-client", "").lower() not in {"mobile", "capacitor"}:
        payload = {**payload, "refresh_token": None}
    return payload


@router.post("/login")
async def ingresar(datos: LoginIn, request: Request, response: Response):
    async with transaccion() as conn:
        payload = await login(
            conn,
            datos.cedula,
            datos.password,
            datos.captcha_token,
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
            datos.dispositivo,
        )
    return _sesion_respuesta(payload, request, response)


@router.post("/cambiar-password")
async def cambiar(datos: PasswordIn, request: Request, response: Response, usuario: dict = Depends(usuario_actual)):
    async with transaccion() as conn:
        fresco = await cambiar_password(
            conn,
            usuario["id"],
            datos.password_actual,
            datos.password_nueva,
            datos.confirmar,
            primer_ingreso=usuario["debe_cambiar_password"],
        )
        from app.services.auth import _emitir

        payload = await _emitir(
            conn,
            fresco,
            ip_valida(request.client.host if request.client else None),
            request.headers.get("user-agent"),
            None,
        )
    return _sesion_respuesta(payload, request, response)


@router.post("/recuperar")
async def recuperar(datos: RecuperarIn):
    async with transaccion() as conn:
        codigo = await pedir_recuperacion(conn, datos.cedula)
    cuerpo = {"mensaje": "Si la cuenta existe, enviamos un código por WhatsApp o SMS."}
    if settings.dev_return_otp and codigo:
        cuerpo["debug_codigo"] = codigo
    return cuerpo


@router.post("/verificar-otp")
async def verificar(datos: OtpIn):
    async with transaccion() as conn:
        return await verificar_otp(conn, datos.cedula, datos.codigo, datos.password_nueva)


@router.post("/refresh")
async def refrescar(datos: RefreshIn, request: Request, response: Response):
    crudo = datos.refresh_token or request.cookies.get("refresh_token")
    if not crudo:
        raise ApiError(401, "REFRESH_INVALIDO", "La sesión no es válida.")
    async with transaccion() as conn:
        payload = await rotar_refresh(
            conn,
            crudo,
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
            None,
        )
    return _sesion_respuesta(payload, request, response)


@router.post("/logout")
async def salir(datos: RefreshIn, request: Request, response: Response, usuario: dict = Depends(usuario_actual)):
    crudo = datos.refresh_token or request.cookies.get("refresh_token")
    async with transaccion() as conn:
        await cerrar_sesion(conn, crudo, usuario["id"], todas=False)
    response.delete_cookie("refresh_token", path="/api/v1/auth")
    return {"ok": True}


@router.post("/logout-todos")
async def salir_todos(response: Response, usuario: dict = Depends(usuario_actual)):
    async with transaccion() as conn:
        await cerrar_sesion(conn, None, usuario["id"], todas=True)
    response.delete_cookie("refresh_token", path="/api/v1/auth")
    return {"ok": True}


@router.get("/yo")
async def yo(usuario: dict = Depends(usuario_actual)):
    return _publico(usuario)


@router.get("/sesiones")
async def sesiones(usuario: dict = Depends(sesion_completa)):
    async with transaccion() as conn:
        filas = await conn.fetch(
            """
            SELECT id, dispositivo, user_agent, ip::text AS ip, creado_en, expira_en
            FROM seguridad.refresh_tokens
            WHERE usuario_id = $1 AND revocado = false AND expira_en > now()
            ORDER BY creado_en DESC
            """,
            usuario["id"],
        )
    return [rec(fila) for fila in filas]


@router.delete("/sesiones/{sesion_id}")
async def cerrar_una(sesion_id: UUID, usuario: dict = Depends(sesion_completa)):
    async with transaccion() as conn:
        await conn.execute(
            "UPDATE seguridad.refresh_tokens SET revocado = true WHERE id = $1 AND usuario_id = $2",
            sesion_id,
            usuario["id"],
        )
    return {"ok": True}


@router.post("/telefono/enviar")
async def enviar_telefono(usuario: dict = Depends(usuario_actual)):
    async with transaccion() as conn:
        fila = await conn.fetchrow("SELECT telefono FROM seguridad.usuarios WHERE id = $1", usuario["id"])
        if not fila or not fila["telefono"]:
            raise ApiError(422, "TELEFONO", "La cuenta no tiene teléfono.")
        codigo = await crear_otp(conn, usuario["id"], fila["telefono"], "verificar_telefono")
    cuerpo = {"mensaje": "Enviamos un código a tu teléfono."}
    if settings.dev_return_otp:
        cuerpo["debug_codigo"] = codigo
    return cuerpo


class CodigoIn(BaseModel):
    codigo: str


@router.post("/telefono/verificar")
async def confirmar_telefono(datos: CodigoIn, usuario: dict = Depends(usuario_actual)):
    from app.core.config import settings as cfg
    from app.core.util import hash_otp

    async with transaccion() as conn:
        fila = await conn.fetchrow(
            """
            SELECT * FROM seguridad.otp_codigos
            WHERE usuario_id = $1 AND proposito = 'verificar_telefono' AND usado = false
            ORDER BY creado_en DESC LIMIT 1
            """,
            usuario["id"],
        )
        if fila is None or hash_otp(datos.codigo, cfg.jwt_secret) != fila["codigo_hash"]:
            raise ApiError(422, "OTP", "El código no es válido.")
        await conn.execute("UPDATE seguridad.otp_codigos SET usado = true WHERE id = $1", fila["id"])
        await conn.execute(
            "UPDATE seguridad.usuarios SET telefono_verificado = true, actualizado_en = now() WHERE id = $1",
            usuario["id"],
        )
    return {"telefono_verificado": True}


@router.post("/webauthn/registro/opciones")
async def webauthn_registro_opciones(usuario: dict = Depends(sesion_completa)):
    from webauthn import generate_registration_options
    from webauthn.helpers import options_to_json
    from webauthn.helpers.structs import AuthenticatorSelectionCriteria, UserVerificationRequirement

    opciones = generate_registration_options(
        rp_id=settings.rp_id,
        rp_name=settings.app_name,
        user_id=str(usuario["id"]).encode(),
        user_name=usuario["cedula"],
        user_display_name=f"{usuario['nombre']} {usuario['apellido']}".strip(),
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED
        ),
    )
    async with transaccion() as conn:
        await conn.execute(
            """
            INSERT INTO seguridad.webauthn_retos (usuario_id, reto, proposito, expira_en)
            VALUES ($1, $2, 'registro', now() + interval '5 minutes')
            """,
            usuario["id"],
            opciones.challenge.hex(),
        )
    return {"opciones": options_to_json(opciones)}


class WebauthnRegistroIn(BaseModel):
    credential: dict
    dispositivo: str | None = None


@router.post("/webauthn/registro")
async def webauthn_registrar(datos: WebauthnRegistroIn, usuario: dict = Depends(sesion_completa)):
    from webauthn import verify_registration_response
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

    async with transaccion() as conn:
        reto = await conn.fetchrow(
            """
            SELECT * FROM seguridad.webauthn_retos
            WHERE usuario_id = $1 AND proposito = 'registro' AND expira_en > now()
            ORDER BY creado_en DESC LIMIT 1
            """,
            usuario["id"],
        )
        if reto is None:
            raise ApiError(422, "WEBAUTHN", "El reto venció. Pedí uno nuevo.")
        try:
            verificado = verify_registration_response(
                credential=datos.credential,
                expected_challenge=bytes.fromhex(reto["reto"]),
                expected_origin=settings.origenes_webauthn,
                expected_rp_id=settings.rp_id,
                require_user_verification=True,
            )
        except Exception as exc:
            raise ApiError(422, "WEBAUTHN", "No pudimos registrar el dispositivo.") from exc
        await conn.execute(
            """
            INSERT INTO seguridad.webauthn_credenciales (usuario_id, credential_id, public_key, sign_count, dispositivo)
            VALUES ($1, $2, $3, $4, $5)
            """,
            usuario["id"],
            bytes_to_base64url(verificado.credential_id),
            verificado.credential_public_key,
            verificado.sign_count,
            datos.dispositivo,
        )
    return {"ok": True}


class WebauthnLoginOpcionesIn(BaseModel):
    cedula: str


@router.post("/webauthn/login/opciones")
async def webauthn_login_opciones(datos: WebauthnLoginOpcionesIn):
    from webauthn import generate_authentication_options
    from webauthn.helpers import options_to_json
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor

    from app.core.util import normalizar_cedula

    try:
        cedula = normalizar_cedula(datos.cedula)
    except ValueError as exc:
        raise ApiError(401, "CREDENCIALES", "Cédula o contraseña incorrecta.") from exc
    async with transaccion() as conn:
        usuario = await conn.fetchrow("SELECT id FROM seguridad.usuarios WHERE cedula = $1 AND estado = 'activo'", cedula)
        if usuario is None:
            raise ApiError(401, "CREDENCIALES", "Cédula o contraseña incorrecta.")
        credenciales = await conn.fetch(
            "SELECT credential_id FROM seguridad.webauthn_credenciales WHERE usuario_id = $1",
            usuario["id"],
        )
        if not credenciales:
            raise ApiError(422, "WEBAUTHN", "Esta cuenta no tiene huella o Face ID.")
        from webauthn.helpers import base64url_to_bytes

        opciones = generate_authentication_options(
            rp_id=settings.rp_id,
            allow_credentials=[
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(fila["credential_id"])) for fila in credenciales
            ],
        )
        await conn.execute(
            """
            INSERT INTO seguridad.webauthn_retos (usuario_id, cedula, reto, proposito, expira_en)
            VALUES ($1, $2, $3, 'login', now() + interval '5 minutes')
            """,
            usuario["id"],
            cedula,
            opciones.challenge.hex(),
        )
    return {"opciones": options_to_json(opciones)}


class WebauthnLoginIn(BaseModel):
    cedula: str
    credential: dict


@router.post("/webauthn/login")
async def webauthn_login(datos: WebauthnLoginIn, request: Request, response: Response):
    from webauthn import verify_authentication_response
    from webauthn.helpers import base64url_to_bytes

    from app.core.util import normalizar_cedula
    from app.services.auth import _emitir, cargar_usuario

    cedula = normalizar_cedula(datos.cedula)
    async with transaccion() as conn:
        usuario_row = await conn.fetchrow("SELECT id, debe_cambiar_password FROM seguridad.usuarios WHERE cedula = $1", cedula)
        reto = await conn.fetchrow(
            """
            SELECT * FROM seguridad.webauthn_retos
            WHERE cedula = $1 AND proposito = 'login' AND expira_en > now()
            ORDER BY creado_en DESC LIMIT 1
            """,
            cedula,
        )
        if usuario_row is None or reto is None or usuario_row["debe_cambiar_password"]:
            raise ApiError(401, "WEBAUTHN", "No se pudo verificar el dispositivo.")
        credencial_id = datos.credential.get("id") or datos.credential.get("rawId")
        guardada = await conn.fetchrow(
            "SELECT * FROM seguridad.webauthn_credenciales WHERE credential_id = $1 AND usuario_id = $2",
            credencial_id,
            usuario_row["id"],
        )
        if guardada is None:
            raise ApiError(401, "WEBAUTHN", "No se pudo verificar el dispositivo.")
        verificado = verify_authentication_response(
            credential=datos.credential,
            expected_challenge=bytes.fromhex(reto["reto"]),
            expected_origin=settings.origenes_webauthn,
            expected_rp_id=settings.rp_id,
            credential_public_key=guardada["public_key"],
            credential_current_sign_count=guardada["sign_count"],
            require_user_verification=True,
        )
        await conn.execute(
            "UPDATE seguridad.webauthn_credenciales SET sign_count = $2 WHERE id = $1",
            guardada["id"],
            verificado.new_sign_count,
        )
        usuario = await cargar_usuario(conn, usuario_row["id"])
        payload = await _emitir(conn, usuario, ip_valida(request.client.host if request.client else None), request.headers.get("user-agent"), "webauthn")
    return _sesion_respuesta(payload, request, response)
