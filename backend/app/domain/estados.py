TRANSICIONES: dict[str, set[str]] = {
    "BORRADOR": {"ENVIADA", "CANCELADA"},
    "ENVIADA": {"EN_REVISION", "CANCELADA"},
    "EN_REVISION": {"ASIGNADA", "CANCELADA"},
    "ASIGNADA": {"CONFIRMADA", "EN_REVISION", "CANCELADA"},
    "CONFIRMADA": {"EN_CAMINO", "EN_REVISION", "CANCELADA", "NO_SHOW"},
    "EN_CAMINO": {"EN_CURSO", "EN_REVISION", "CANCELADA"},
    "EN_CURSO": {"COMPLETADA", "DISPUTA"},
    "COMPLETADA": {"PAGADA", "DISPUTA", "CALIFICADA"},
    "PAGADA": {"CALIFICADA", "DISPUTA"},
    "CALIFICADA": set(),
    "CANCELADA": set(),
    "NO_SHOW": set(),
    "DISPUTA": {"COMPLETADA", "CANCELADA", "PAGADA"},
}

MARCA_TIEMPO = {
    "ENVIADA": "enviada_en",
    "ASIGNADA": "asignada_en",
    "CONFIRMADA": "confirmada_en",
    "EN_CAMINO": "en_camino_en",
    "EN_CURSO": "checkin_en",
    "COMPLETADA": "completada_en",
    "PAGADA": "pagada_en",
    "CANCELADA": "cancelada_en",
    "NO_SHOW": "cancelada_en",
}

ESTADOS_ACTIVOS = {
    "ENVIADA",
    "EN_REVISION",
    "ASIGNADA",
    "CONFIRMADA",
    "EN_CAMINO",
    "EN_CURSO",
}

ESTADOS_OCUPAN_AGENDA = ESTADOS_ACTIVOS - {"ENVIADA", "EN_REVISION"}
