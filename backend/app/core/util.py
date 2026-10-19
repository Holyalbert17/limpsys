import hashlib
import hmac
import ipaddress
import json
import re
from datetime import date, datetime, time, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Asuncion")
CEDULA_RE = re.compile(r"^[0-9]{5,10}$")


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def hoy_py() -> date:
    return datetime.now(TZ).date()


def decimal(valor) -> Decimal:
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, bool):
        return Decimal(int(valor))
    if isinstance(valor, float):
        return Decimal(str(valor))
    return Decimal(str(valor))


def rec(row) -> dict | None:
    if row is None:
        return None
    salida = {}
    for clave, valor in dict(row).items():
        if isinstance(valor, Decimal):
            integral = valor.to_integral_value()
            salida[clave] = int(integral) if valor == integral else float(valor)
        elif isinstance(valor, UUID):
            salida[clave] = str(valor)
        else:
            salida[clave] = valor
    return salida


def recs(rows) -> list[dict]:
    return [rec(fila) for fila in rows]


def hash_sha256(valor: str) -> str:
    return hashlib.sha256(valor.encode()).hexdigest()


def hash_otp(codigo: str, secreto: str) -> str:
    return hmac.new(secreto.encode(), codigo.encode(), hashlib.sha256).hexdigest()


def ip_valida(valor: str | None) -> str | None:
    if not valor:
        return None
    try:
        return str(ipaddress.ip_address(valor.split("%")[0]))
    except ValueError:
        return None


def normalizar_cedula(cedula: str) -> str:
    limpia = cedula.strip().replace(".", "").replace(" ", "").replace("-", "")
    if not CEDULA_RE.match(limpia):
        raise ValueError("cedula")
    return limpia


def json_default(valor):
    if isinstance(valor, Decimal):
        integral = valor.to_integral_value()
        return int(integral) if valor == integral else float(valor)
    if isinstance(valor, datetime):
        return valor.isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, time):
        return valor.isoformat()
    if isinstance(valor, UUID):
        return str(valor)
    raise TypeError(f"No se puede serializar {type(valor)}")


def a_json(data) -> str:
    return json.dumps(data, default=json_default, ensure_ascii=False)
