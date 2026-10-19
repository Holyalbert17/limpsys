from app.core.errors import ApiError


async def aplicar_moneda(conn, resultado: dict, codigo: str | None) -> dict:
    codigo = (codigo or "PYG").upper()
    if codigo == "PYG":
        resultado["moneda"] = "PYG"
        return resultado
    moneda = await conn.fetchrow(
        "SELECT * FROM catalogo.monedas WHERE codigo = $1 AND habilitada",
        codigo,
    )
    tasa = await conn.fetchval(
        """
        SELECT tasa_gs FROM catalogo.tipos_cambio
        WHERE moneda = $1 AND (vigente_hasta IS NULL OR vigente_hasta > now())
        ORDER BY vigente_desde DESC
        LIMIT 1
        """,
        codigo,
    )
    if moneda is None or tasa is None:
        raise ApiError(422, "MONEDA", "Esa moneda no está habilitada o no tiene tipo de cambio.")
    salida = dict(resultado)
    salida["moneda"] = codigo
    salida["total_moneda"] = round(float(resultado["total"]) / float(tasa), int(moneda["decimales"]))
    return salida
