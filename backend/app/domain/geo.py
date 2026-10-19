import math
import re

ANILLOS = (
    re.compile(r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)"),
    re.compile(r"[?&](?:q|ll|query)=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)"),
    re.compile(r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)"),
)


def punto_en_poligono(lat: float, lng: float, poligono) -> bool:
    if not poligono:
        return False
    anillo = poligono[0]
    if len(anillo) < 4:
        return False
    dentro = False
    j = len(anillo) - 1
    for i, punto in enumerate(anillo):
        xi, yi = float(punto[0]), float(punto[1])
        xj, yj = float(anillo[j][0]), float(anillo[j][1])
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            dentro = not dentro
        j = i
    return dentro


def distancia_metros(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radio = 6371000
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    )
    return radio * 2 * math.asin(min(1, math.sqrt(a)))


def area_poligono(poligono) -> float:
    anillo = poligono[0]
    total = 0.0
    for i, punto in enumerate(anillo):
        x1, y1 = float(punto[0]), float(punto[1])
        x2, y2 = float(anillo[(i + 1) % len(anillo)][0]), float(anillo[(i + 1) % len(anillo)][1])
        total += x1 * y2 - x2 * y1
    return abs(total) / 2


def centroide(poligono) -> tuple[float, float]:
    anillo = poligono[0]
    puntos = anillo[:-1] if anillo[0] == anillo[-1] else anillo
    lng = sum(float(punto[0]) for punto in puntos) / len(puntos)
    lat = sum(float(punto[1]) for punto in puntos) / len(puntos)
    return lat, lng


def elegir_zona(lat: float, lng: float, zonas: list[dict]) -> dict | None:
    candidatas = [zona for zona in zonas if punto_en_poligono(lat, lng, zona["poligono"])]
    if not candidatas:
        return None
    return min(candidatas, key=lambda zona: area_poligono(zona["poligono"]))


def extraer_coordenadas(url: str) -> tuple[float, float] | None:
    for patron in ANILLOS:
        hallazgo = patron.search(url)
        if not hallazgo:
            continue
        if patron.pattern.startswith("!"):
            return float(hallazgo.group(1)), float(hallazgo.group(2))
        if patron.pattern.startswith("@"):
            return float(hallazgo.group(1)), float(hallazgo.group(2))
        return float(hallazgo.group(1)), float(hallazgo.group(2))
    return None
