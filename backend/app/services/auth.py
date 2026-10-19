import secrets
from datetime import timedelta
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.errors import ApiError
from app.core.security import (
    captcha_valido,
    crear_access_token,
    hash_password,
    nuevo_refresh,
    requisitos_password,
    verificar_contra_fantasma,
    verificar_password,
)
from app.core.util import ahora, hash_otp, hash_sha256, ip_valida, normalizar_cedula
from app.services.notificaciones import notificar
from app.services.parametros import obtener_parametros, pent


async def consumir_cupo(conn, clave: str, limite: int, ventana_seg: int) -> None:
    fila = await conn.fetchrow(
        """
        INSERT INTO seguridad.rate_limits (clave, ventana_inicio, contador)
        VALUES ($1, now(), 1)
        ON CONFLICT (clave) DO UPDATE SET
          contador = CASE
            WHEN seguridad.rate_limits.ventana_inicio < now() - make_interval(secs => $2::double precision)
            THEN 1 ELSE seguridad.rate_limits.contador + 1 END,
          ventana_inicio = CASE
            WHEN seguridad.rate_limits.ventana_inicio < now() - make_interval(secs => $2::double precision)
            THEN now() ELSE seguridad.rate_limits.ventana_inicio END,
          actualizado_en = now()
        RETURNING contador
        """,
        clave,
        float(ventana_seg),
    )
    if fila["contador"] > limite:
        raise ApiError(429, "DEMASIADAS_SOLICITUDES", "Demasiados intentos. Probá de nuevo más tarde.")


async def _permisos(conn, usuario_id) -> list[str]:
    filas = await conn.fetch(
        """
        SELECT codigo FROM (
          SELECT p.codigo
          FROM seguridad.permisos p
          JOIN seguridad.rol_permisos rp ON rp.permiso_id = p.id
          JOIN seguridad.usuarios u ON u.rol_id = rp.rol_id
          WHERE u.id = $1
          UNION
          SELECT p.codigo
          FROM seguridad.usuario_permisos up
          JOIN seguridad.permisos p ON p.id = up.permiso_id
          WHERE up.usuario_id = $1 AND up.concedido
        ) AS concedidos
        WHERE codigo NOT IN (
          SELECT p.codigo
          FROM seguridad.usuario_permisos up
          JOIN seguridad.permisos p ON p.id = up.permiso_id
          WHERE up.usuario_id = $1 AND NOT up.concedido
        )
        ORDER BY codigo
        """,
        usuario_id,
    )
    return [fila["codigo"] for fila in filas]


async def cargar_usuario(conn, usuario_id) -> dict | None:
    fila = await conn.fetchrow(
        """
        SELECT u.id, u.cedula, u.nombre, u.apellido, u.telefono, u.correo, u.idioma,
               u.debe_cambiar_password, u.estado, u.telefono_verificado,
               r.codigo AS rol,
               c.id AS cliente_id, c.bloqueado AS cliente_bloqueado, c.tour_pendiente,
               c.puntos_saldo, c.credito_saldo, c.tipo AS cliente_tipo, c.codigo_referido,
               e.id AS empleado_id, e.estado AS empleado_estado, e.disponible, e.nivel
        FROM seguridad.usuarios u
        JOIN seguridad.roles r ON r.id = u.rol_id
        LEFT JOIN crm.clientes c ON c.usuario_id = u.id
        LEFT JOIN personal.empleados e ON e.usuario_id = u.id
        WHERE u.id = $1
        """,
        usuario_id,
    )
    if fila is None:
        return None
    datos = dict(fila)
    datos["permisos"] = await _permisos(conn, usuario_id) if datos["rol"] in {"admin", "superadmin"} else []
    return datos


async def _emitir(conn, usuario, ip, user_agent, dispositivo) -> dict:
    crudo, token_hash = nuevo_refresh()
    familia = uuid4()
    await conn.execute(
        """
        INSERT INTO seguridad.refresh_tokens (
          usuario_id, token_hash, familia, expira_en, user_agent, ip, dispositivo
        )
        VALUES ($1, $2, $3, now() + make_interval(days => $4), $5, $6::inet, $7)
        """,
        usuario["id"],
        token_hash,
        familia,
        settings.jwt_refresh_days,
        user_agent,
        ip,
        dispositivo,
    )
    access, expira_en = crear_access_token(usuario["id"], usuario["rol"])
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": expira_en,
        "refresh_token": crudo,
        "debe_cambiar_password": usuario["debe_cambiar_password"],
        "usuario": _publico(usuario),
    }


def _publico(usuario: dict) -> dict:
    return {
        "id": str(usuario["id"]),
        "cedula": usuario["cedula"],
        "nombre": usuario["nombre"],
        "apellido": usuario["apellido"],
        "rol": usuario["rol"],
        "idioma": usuario["idioma"],
        "telefono_verificado": usuario["telefono_verificado"],
        "debe_cambiar_password": usuario["debe_cambiar_password"],
        "permisos": usuario.get("permisos") or [],
        "cliente_id": None if usuario.get("cliente_id") is None else str(usuario["cliente_id"]),
        "empleado_id": None if usuario.get("empleado_id") is None else str(usuario["empleado_id"]),
        "tour_pendiente": usuario.get("tour_pendiente"),
    }


async def login(conn, cedula: str, password: str, captcha_token: str | None, ip: str | None, user_agent: str | None, dispositivo: str | None):
    ip = ip_valida(ip)
    try:
        cedula = normalizar_cedula(cedula)
    except ValueError as exc:
        raise ApiError(401, "CREDENCIALES", "Cédula o contraseña incorrecta.") from exc
    params = await obtener_parametros(conn)
    if ip:
        await consumir_cupo(conn, f"ip:{ip}:login", settings.rate_limit_login, 900)
    usuario_row = await conn.fetchrow(
        """
        SELECT u.*, r.codigo AS rol
        FROM seguridad.usuarios u
        JOIN seguridad.roles r ON r.id = u.rol_id
        WHERE u.cedula = $1
        """,
        cedula,
    )
    fallos_ip = 0
    if ip:
        fallos_ip = await conn.fetchval(
            """
            SELECT count(*) FROM seguridad.auditoria_acceso
            WHERE ip = $1::inet AND exito = false AND fecha > now() - interval '15 minutes'
            """,
            ip,
        )
    fallos_usuario = usuario_row["intentos_fallidos"] if usuario_row else 0
    captcha = fallos_ip >= pent(params, "captcha_tras_fallos") or fallos_usuario >= pent(params, "captcha_tras_fallos")
    if captcha and not captcha_valido(captcha_token):
        raise ApiError(401, "CAPTCHA_REQUERIDO", "Cédula o contraseña incorrecta.", {"captcha_requerido": True})
    if usuario_row and usuario_row["bloqueado_hasta"] and usuario_row["bloqueado_hasta"] > ahora():
        raise ApiError(429, "BLOQUEADO", "Demasiados intentos. Probá de nuevo más tarde.")
    if usuario_row is None:
        verificar_contra_fantasma(password)
        ok = False
    else:
        ok = verificar_password(usuario_row["password_hash"], password)
    if not ok:
        if usuario_row:
            intentos = usuario_row["intentos_fallidos"] + 1
            bloqueado = None
            if intentos >= pent(params, "intentos_maximos"):
                bloqueado = ahora() + timedelta(minutes=pent(params, "bloqueo_minutos"))
                intentos = 0
            await conn.execute(
                """
                UPDATE seguridad.usuarios
                SET intentos_fallidos = $2, bloqueado_hasta = $3, actualizado_en = now()
                WHERE id = $1
                """,
                usuario_row["id"],
                intentos,
                bloqueado,
            )
        await conn.execute(
            """
            INSERT INTO seguridad.auditoria_acceso (usuario_id, cedula, exito, motivo, ip, user_agent, dispositivo)
            VALUES ($1, $2, false, 'credenciales', $3::inet, $4, $5)
            """,
            None if usuario_row is None else usuario_row["id"],
            cedula,
            ip,
            user_agent,
            dispositivo,
        )
        extra = {}
        if captcha or fallos_usuario + 1 >= pent(params, "captcha_tras_fallos"):
            extra["captcha_requerido"] = True
        raise ApiError(401, "CREDENCIALES", "Cédula o contraseña incorrecta.", extra)
    if usuario_row["estado"] != "activo":
        raise ApiError(403, "CUENTA_SUSPENDIDA", "La cuenta no está habilitada.")
    if (
        usuario_row["debe_cambiar_password"]
        and usuario_row["password_expira_en"]
        and usuario_row["password_expira_en"] < ahora()
    ):
        raise ApiError(401, "PASSWORD_EXPIRED", "La contraseña inicial venció. Usá «Olvidé mi contraseña».")
    await conn.execute(
        """
        UPDATE seguridad.usuarios
        SET intentos_fallidos = 0, bloqueado_hasta = NULL, ultimo_login = now(), actualizado_en = now()
        WHERE id = $1
        """,
        usuario_row["id"],
    )
    await conn.execute(
        """
        INSERT INTO seguridad.auditoria_acceso (usuario_id, cedula, exito, ip, user_agent, dispositivo)
        VALUES ($1, $2, true, $3::inet, $4, $5)
        """,
        usuario_row["id"],
        cedula,
        ip,
        user_agent,
        dispositivo,
    )
    usuario = await cargar_usuario(conn, usuario_row["id"])
    return await _emitir(conn, usuario, ip, user_agent, dispositivo)


async def cambiar_password(conn, usuario_id, actual: str | None, nueva: str, confirmar: str, primer_ingreso: bool):
    usuario = await conn.fetchrow("SELECT * FROM seguridad.usuarios WHERE id = $1", usuario_id)
    if nueva != confirmar:
        raise ApiError(422, "PASSWORD", "La confirmación no coincide.")
    faltan = requisitos_password(nueva, usuario["cedula"])
    if faltan:
        raise ApiError(422, "PASSWORD_DEBIL", "La contraseña no cumple los requisitos.", {"faltan": faltan})
    if primer_ingreso:
        if not usuario["debe_cambiar_password"]:
            raise ApiError(409, "PASSWORD", "Esta cuenta ya tiene una contraseña propia.")
        if not verificar_password(usuario["password_hash"], actual or ""):
            raise ApiError(401, "CREDENCIALES", "Cédula o contraseña incorrecta.")
    else:
        if not actual or not verificar_password(usuario["password_hash"], actual):
            raise ApiError(401, "CREDENCIALES", "Cédula o contraseña incorrecta.")
    await conn.execute(
        """
        UPDATE seguridad.usuarios
        SET password_hash = $2, debe_cambiar_password = false, password_expira_en = NULL,
            intentos_fallidos = 0, bloqueado_hasta = NULL, actualizado_en = now()
        WHERE id = $1
        """,
        usuario_id,
        hash_password(nueva),
    )
    await conn.execute("UPDATE seguridad.refresh_tokens SET revocado = true WHERE usuario_id = $1", usuario_id)
    fresco = await cargar_usuario(conn, usuario_id)
    return fresco


async def rotar_refresh(conn, crudo: str, ip, user_agent, dispositivo) -> dict:
    ip = ip_valida(ip)
    fila = await conn.fetchrow(
        "SELECT * FROM seguridad.refresh_tokens WHERE token_hash = $1",
        hash_sha256(crudo),
    )
    if fila is None:
        raise ApiError(401, "REFRESH_INVALIDO", "La sesión no es válida.")
    if fila["revocado"]:
        await conn.execute("UPDATE seguridad.refresh_tokens SET revocado = true WHERE familia = $1", fila["familia"])
        raise ApiError(401, "REFRESH_REUTILIZADO", "Cerramos la sesión por seguridad. Volvé a ingresar.")
    if fila["expira_en"] < ahora():
        raise ApiError(401, "REFRESH_VENCIDO", "La sesión venció.")
    nuevo, token_hash = nuevo_refresh()
    nuevo_id = uuid4()
    await conn.execute(
        "UPDATE seguridad.refresh_tokens SET revocado = true, reemplazado_por = $2 WHERE id = $1",
        fila["id"],
        nuevo_id,
    )
    await conn.execute(
        """
        INSERT INTO seguridad.refresh_tokens (id, usuario_id, token_hash, familia, expira_en, user_agent, ip, dispositivo)
        VALUES ($1,$2,$3,$4, now() + make_interval(days => $5), $6, $7::inet, $8)
        """,
        nuevo_id,
        fila["usuario_id"],
        token_hash,
        fila["familia"],
        settings.jwt_refresh_days,
        user_agent,
        ip,
        dispositivo,
    )
    usuario = await cargar_usuario(conn, fila["usuario_id"])
    if usuario is None or usuario["estado"] != "activo":
        raise ApiError(401, "NO_AUTENTICADO", "Tenés que iniciar sesión.")
    access, expira_en = crear_access_token(usuario["id"], usuario["rol"])
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": expira_en,
        "refresh_token": nuevo,
        "debe_cambiar_password": usuario["debe_cambiar_password"],
        "usuario": _publico(usuario),
    }


async def cerrar_sesion(conn, crudo: str | None, usuario_id, todas: bool) -> None:
    if todas:
        await conn.execute("UPDATE seguridad.refresh_tokens SET revocado = true WHERE usuario_id = $1", usuario_id)
        return
    if crudo:
        await conn.execute(
            "UPDATE seguridad.refresh_tokens SET revocado = true WHERE token_hash = $1 AND usuario_id = $2",
            hash_sha256(crudo),
            usuario_id,
        )


async def crear_otp(conn, usuario_id, destino: str, proposito: str, canal: str = "whatsapp") -> str:
    codigo = f"{secrets.randbelow(1000000):06d}"
    await conn.execute(
        "UPDATE seguridad.otp_codigos SET usado = true WHERE destino = $1 AND proposito = $2 AND usado = false",
        destino,
        proposito,
    )
    await conn.execute(
        """
        INSERT INTO seguridad.otp_codigos (usuario_id, destino, proposito, codigo_hash, expira_en, canal)
        VALUES ($1, $2, $3, $4, now() + interval '10 minutes', $5)
        """,
        usuario_id,
        destino,
        proposito,
        hash_otp(codigo, settings.jwt_secret),
        canal,
    )
    if usuario_id:
        await notificar(conn, "otp", usuario_id, {"codigo": codigo, "nombre": ""})
    return codigo


async def verificar_otp(conn, cedula: str, codigo: str, password_nueva: str | None = None) -> dict:
    try:
        cedula = normalizar_cedula(cedula)
    except ValueError as exc:
        raise ApiError(422, "OTP", "El código no es válido.") from exc
    usuario = await conn.fetchrow("SELECT * FROM seguridad.usuarios WHERE cedula = $1", cedula)
    if usuario is None:
        raise ApiError(422, "OTP", "El código no es válido.")
    fila = await conn.fetchrow(
        """
        SELECT * FROM seguridad.otp_codigos
        WHERE usuario_id = $1 AND proposito = 'recuperar' AND usado = false
        ORDER BY creado_en DESC LIMIT 1
        """,
        usuario["id"],
    )
    if fila is None or fila["expira_en"] < ahora() or fila["intentos"] >= 5:
        raise ApiError(422, "OTP", "El código no es válido.")
    if hash_otp(codigo, settings.jwt_secret) != fila["codigo_hash"]:
        await conn.execute("UPDATE seguridad.otp_codigos SET intentos = intentos + 1 WHERE id = $1", fila["id"])
        raise ApiError(422, "OTP", "El código no es válido.")
    if not password_nueva:
        raise ApiError(422, "PASSWORD", "Tenés que elegir una contraseña nueva.")
    faltan = requisitos_password(password_nueva, cedula)
    if faltan:
        raise ApiError(422, "PASSWORD_DEBIL", "La contraseña no cumple los requisitos.", {"faltan": faltan})
    await conn.execute("UPDATE seguridad.otp_codigos SET usado = true WHERE id = $1", fila["id"])
    await conn.execute(
        """
        UPDATE seguridad.usuarios
        SET password_hash = $2, debe_cambiar_password = false, password_expira_en = NULL,
            intentos_fallidos = 0, bloqueado_hasta = NULL, actualizado_en = now()
        WHERE id = $1
        """,
        usuario["id"],
        hash_password(password_nueva),
    )
    await conn.execute("UPDATE seguridad.refresh_tokens SET revocado = true WHERE usuario_id = $1", usuario["id"])
    return {"ok": True}


async def pedir_recuperacion(conn, cedula: str) -> str | None:
    try:
        cedula_ok = normalizar_cedula(cedula)
    except ValueError:
        return None
    usuario = await conn.fetchrow("SELECT * FROM seguridad.usuarios WHERE cedula = $1 AND estado = 'activo'", cedula_ok)
    if usuario is None or not usuario["telefono"]:
        return None
    return await crear_otp(conn, usuario["id"], usuario["telefono"], "recuperar")
