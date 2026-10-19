import secrets
from datetime import timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from argon2.low_level import Type

from app.core.config import settings
from app.core.errors import ApiError
from app.core.util import ahora, hash_sha256

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=2,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
_HASH_FANTASMA = _hasher.hash("limsys-no-es-una-clave")


def hash_password(password: str) -> str:
    return _hasher.hash(password)

def verificar_password(hash_guardado: str, password: str) -> bool:
    try:
        ok = _hasher.verify(hash_guardado, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False
    return ok


def verificar_contra_fantasma(password: str) -> None:
    verificar_password(_HASH_FANTASMA, password)


def requisitos_password(password: str, cedula: str) -> list[str]:
    faltan = []
    if len(password) < 8:
        faltan.append("mínimo 8 caracteres")
    if not any(caracter.isalpha() for caracter in password):
        faltan.append("una letra")
    if not any(caracter.isdigit() for caracter in password):
        faltan.append("un número")
    if password == cedula:
        faltan.append("distinta a la cédula")
    return faltan


def crear_access_token(usuario_id: UUID, rol: str) -> tuple[str, int]:
    expira = ahora() + timedelta(minutes=settings.jwt_access_minutes)
    payload = {
        "sub": str(usuario_id),
        "rol": rol,
        "typ": "access",
        "exp": expira,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, settings.jwt_access_minutes * 60


def leer_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise ApiError(401, "NO_AUTENTICADO", "Tenés que iniciar sesión.") from exc
    if payload.get("typ") != "access":
        raise ApiError(401, "NO_AUTENTICADO", "Tenés que iniciar sesión.")
    return payload


def nuevo_refresh() -> tuple[str, str]:
    crudo = secrets.token_urlsafe(48)
    return crudo, hash_sha256(crudo)


def captcha_valido(token: str | None) -> bool:
    if not token:
        return False
    if settings.captcha_dev_mode and token == settings.captcha_dev_token:
        return True
    return False
