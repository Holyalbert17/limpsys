import secrets
from datetime import timedelta

from app.core.errors import ApiError
from app.core.security import hash_password, requisitos_password
from app.core.util import ahora, normalizar_cedula
from app.services.notificaciones import notificar
from app.services.parametros import obtener_parametros, pdec, pent


async def _codigo_referido(conn) -> str:
    for _ in range(5):
        codigo = secrets.token_hex(4).upper()
        existe = await conn.fetchval("SELECT 1 FROM crm.clientes WHERE codigo_referido = $1", codigo)
        if not existe:
            return codigo
    raise ApiError(500, "REFERIDO", "No pudimos generar el código de referido.")


async def crear_cliente(
    conn,
    *,
    cedula: str,
    nombre: str,
    apellido: str,
    telefono: str | None,
    correo: str | None,
    password: str,
    debe_cambiar: bool,
    creado_por,
    codigo_referido: str | None = None,
    tipo: str = "persona",
):
    try:
        cedula = normalizar_cedula(cedula)
    except ValueError as exc:
        raise ApiError(422, "CEDULA", "La cédula tiene que tener solo números, sin puntos.") from exc
    if debe_cambiar:
        password = cedula
    else:
        faltan = requisitos_password(password, cedula)
        if faltan:
            raise ApiError(422, "PASSWORD_DEBIL", "La contraseña no cumple los requisitos.", {"faltan": faltan})
    rol_id = await conn.fetchval("SELECT id FROM seguridad.roles WHERE codigo = 'cliente'")
    params = await obtener_parametros(conn)
    expira = ahora() + timedelta(hours=pent(params, "password_inicial_horas")) if debe_cambiar else None
    usuario_id = await conn.fetchval(
        """
        INSERT INTO seguridad.usuarios (
          rol_id, cedula, password_hash, debe_cambiar_password, password_expira_en,
          nombre, apellido, telefono, correo, creado_por
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        RETURNING id
        """,
        rol_id,
        cedula,
        hash_password(password),
        debe_cambiar,
        expira,
        nombre.strip(),
        (apellido or "").strip(),
        telefono,
        correo,
        creado_por,
    )
    referidor = None
    if codigo_referido:
        referidor = await conn.fetchval(
            "SELECT id FROM crm.clientes WHERE codigo_referido = $1",
            codigo_referido.strip().upper(),
        )
    cliente_id = await conn.fetchval(
        """
        INSERT INTO crm.clientes (usuario_id, tipo, nombre, apellido, telefono, correo, codigo_referido, referido_por)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        RETURNING id
        """,
        usuario_id,
        tipo,
        nombre.strip(),
        (apellido or "").strip(),
        telefono,
        correo,
        await _codigo_referido(conn),
        referidor,
    )
    if referidor:
        await conn.execute(
            """
            INSERT INTO comercial.referidos (referidor_id, referido_id)
            VALUES ($1, $2)
            ON CONFLICT (referido_id) DO NOTHING
            """,
            referidor,
            cliente_id,
        )
    await conn.execute(
        "INSERT INTO sistema.preferencias_notificacion (usuario_id) VALUES ($1) ON CONFLICT DO NOTHING",
        usuario_id,
    )
    await notificar(conn, "bienvenida", usuario_id, {"nombre": nombre.strip()})
    return usuario_id, cliente_id


async def crear_empleado(conn, *, cedula, nombre, apellido, telefono, correo, creado_por, zonas: list[int]):
    try:
        cedula = normalizar_cedula(cedula)
    except ValueError as exc:
        raise ApiError(422, "CEDULA", "La cédula tiene que tener solo números, sin puntos.") from exc
    rol_id = await conn.fetchval("SELECT id FROM seguridad.roles WHERE codigo = 'empleado'")
    params = await obtener_parametros(conn)
    usuario_id = await conn.fetchval(
        """
        INSERT INTO seguridad.usuarios (
          rol_id, cedula, password_hash, debe_cambiar_password, password_expira_en,
          nombre, apellido, telefono, correo, creado_por
        )
        VALUES ($1,$2,$3,true,$4,$5,$6,$7,$8,$9)
        RETURNING id
        """,
        rol_id,
        cedula,
        hash_password(cedula),
        ahora() + timedelta(hours=pent(params, "password_inicial_horas")),
        nombre.strip(),
        (apellido or "").strip(),
        telefono,
        correo,
        creado_por,
    )
    empleado_id = await conn.fetchval(
        """
        INSERT INTO personal.empleados (usuario_id, calificacion, tarifa_hora)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        usuario_id,
        pdec(params, "calificacion_inicial"),
        pdec(params, "pago_empleado_hora"),
    )
    for zona_id in zonas:
        await conn.execute(
            "INSERT INTO personal.empleado_zonas (empleado_id, zona_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            empleado_id,
            zona_id,
        )
    await conn.execute(
        """
        INSERT INTO personal.empleado_capacitacion (empleado_id, capacitacion_id, completada)
        SELECT $1, id, false FROM personal.capacitaciones WHERE obligatoria AND activo
        ON CONFLICT DO NOTHING
        """,
        empleado_id,
    )
    await conn.execute(
        "INSERT INTO sistema.preferencias_notificacion (usuario_id) VALUES ($1) ON CONFLICT DO NOTHING",
        usuario_id,
    )
    await notificar(conn, "bienvenida", usuario_id, {"nombre": nombre.strip()})
    return usuario_id, empleado_id


async def crear_admin(conn, *, cedula, nombre, apellido, rol: str, creado_por):
    if rol not in {"admin", "superadmin"}:
        raise ApiError(422, "ROL", "El rol del panel tiene que ser admin o superadmin.")
    try:
        cedula = normalizar_cedula(cedula)
    except ValueError as exc:
        raise ApiError(422, "CEDULA", "La cédula tiene que tener solo números, sin puntos.") from exc
    rol_id = await conn.fetchval("SELECT id FROM seguridad.roles WHERE codigo = $1", rol)
    params = await obtener_parametros(conn)
    usuario_id = await conn.fetchval(
        """
        INSERT INTO seguridad.usuarios (
          rol_id, cedula, password_hash, debe_cambiar_password, password_expira_en, nombre, apellido, creado_por
        )
        VALUES ($1,$2,$3,true,$4,$5,$6,$7)
        RETURNING id
        """,
        rol_id,
        cedula,
        hash_password(cedula),
        ahora() + timedelta(hours=pent(params, "password_inicial_horas")),
        nombre.strip(),
        (apellido or "").strip(),
        creado_por,
    )
    await conn.execute(
        "INSERT INTO sistema.preferencias_notificacion (usuario_id) VALUES ($1) ON CONFLICT DO NOTHING",
        usuario_id,
    )
    return usuario_id
