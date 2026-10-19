import logging

import httpx

from app.core.config import settings
from app.domain.geo import distancia_metros
from app.services.parametros import obtener_parametros, pdec

logger = logging.getLogger("limsys.mapas")


async def estimar_viaje(conn, lat1: float, lng1: float, lat2: float, lng2: float) -> dict:
    params = await obtener_parametros(conn)
    recta = distancia_metros(lat1, lng1, lat2, lng2)
    factor = float(pdec(params, "factor_ruta"))
    velocidad = float(pdec(params, "velocidad_urbana_kmh"))
    metros = recta * factor
    segundos = metros / (velocidad * 1000 / 3600) if velocidad else 0
    origen = "estimado"
    if settings.google_maps_server_key:
        clave = f"{round(lat1, 4)},{round(lng1, 4)}:{round(lat2, 4)},{round(lng2, 4)}"
        cache = await conn.fetchrow(
            "SELECT valor FROM sistema.cache_geo WHERE clave = $1 AND expira_en > now()",
            clave,
        )
        if cache:
            return cache["valor"]
        try:
            async with httpx.AsyncClient(timeout=5) as cliente:
                respuesta = await cliente.get(
                    "https://maps.googleapis.com/maps/api/distancematrix/json",
                    params={
                        "origins": f"{lat1},{lng1}",
                        "destinations": f"{lat2},{lng2}",
                        "mode": "driving",
                        "key": settings.google_maps_server_key,
                    },
                )
            elemento = respuesta.json()["rows"][0]["elements"][0]
            if elemento.get("status") == "OK":
                metros = elemento["distance"]["value"]
                segundos = elemento["duration"]["value"]
                origen = "google"
                valor = {"metros": int(metros), "segundos": int(segundos), "origen": origen}
                await conn.execute(
                    """
                    INSERT INTO sistema.cache_geo (clave, valor, expira_en)
                    VALUES ($1, $2::jsonb, now() + interval '7 days')
                    ON CONFLICT (clave) DO UPDATE SET valor = $2::jsonb, expira_en = now() + interval '7 days'
                    """,
                    clave,
                    valor,
                )
                return valor
        except Exception:
            logger.exception("Distance Matrix no respondió; se usa la estimación local")
    return {"metros": int(metros), "segundos": int(segundos), "origen": origen}
