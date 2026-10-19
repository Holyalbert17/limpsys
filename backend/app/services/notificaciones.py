import json
import logging
import re

from app.core.util import json_default
from app.realtime import hub

logger = logging.getLogger("limsys.notificaciones")
_VARIABLE = re.compile(r"\{\{(\w+)\}\}")


def _render(texto: str, variables: dict) -> str:
    return _VARIABLE.sub(lambda hallazgo: str(variables.get(hallazgo.group(1), "")), texto)


async def notificar(conn, evento: str, usuario_id, variables: dict | None = None) -> None:
    variables = json.loads(json.dumps(variables or {}, default=json_default))
    plantilla = await conn.fetchrow(
        "SELECT titulo, cuerpo, canales FROM sistema.plantillas_notificacion WHERE evento = $1",
        evento,
    )
    if plantilla is None:
        titulo, cuerpo, canales = evento, str(variables), ["panel"]
    else:
        titulo = _render(plantilla["titulo"], variables)
        cuerpo = _render(plantilla["cuerpo"], variables)
        canales = list(plantilla["canales"])

    prefs = None
    usuario = None
    if usuario_id is not None:
        prefs = await conn.fetchrow(
            "SELECT * FROM sistema.preferencias_notificacion WHERE usuario_id = $1",
            usuario_id,
        )
        usuario = await conn.fetchrow(
            "SELECT telefono, correo, nombre FROM seguridad.usuarios WHERE id = $1",
            usuario_id,
        )
    permitidos = {
        "push": True if prefs is None else prefs["push"],
        "whatsapp": True if prefs is None else prefs["whatsapp"],
        "sms": False if prefs is None else prefs["sms"],
        "correo": True if prefs is None else prefs["correo"],
        "panel": True,
    }
    for canal in canales:
        if not permitidos.get(canal, False):
            continue
        if canal in {"whatsapp", "sms"} and (usuario is None or not usuario["telefono"]):
            continue
        if canal == "correo" and (usuario is None or not usuario["correo"]):
            continue
        await conn.execute(
            """
            INSERT INTO sistema.notificaciones (usuario_id, evento, canal, titulo, cuerpo, estado, data)
            VALUES ($1, $2, $3, $4, $5, 'registrada', $6::jsonb)
            """,
            usuario_id,
            evento,
            canal,
            titulo,
            cuerpo,
            variables,
        )
        logger.info("Notificación %s por %s para %s", evento, canal, usuario_id)
    if usuario_id is not None:
        await hub.emitir(
            f"usuario:{usuario_id}",
            {"tipo": "notificacion", "evento": evento, "titulo": titulo, "cuerpo": cuerpo},
        )
