import logging
from pathlib import Path

from app.core.db import conexion

logger = logging.getLogger("limsys.migrate")
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def partir_sql(script: str) -> list[str]:
    partes: list[str] = []
    buffer: list[str] = []
    i = 0
    n = len(script)
    dolar = None
    en_texto = False
    en_comentario = False
    while i < n:
        caracter = script[i]
        if en_comentario:
            buffer.append(caracter)
            if caracter == "\n":
                en_comentario = False
            i += 1
            continue
        if dolar is not None:
            if script.startswith(dolar, i):
                buffer.append(dolar)
                i += len(dolar)
                dolar = None
            else:
                buffer.append(caracter)
                i += 1
            continue
        if en_texto:
            buffer.append(caracter)
            if caracter == "'":
                if i + 1 < n and script[i + 1] == "'":
                    buffer.append("'")
                    i += 2
                    continue
                en_texto = False
            i += 1
            continue
        if caracter == "-" and i + 1 < n and script[i + 1] == "-":
            en_comentario = True
            buffer.append(caracter)
            i += 1
            continue
        if caracter == "'":
            en_texto = True
            buffer.append(caracter)
            i += 1
            continue
        if caracter == "$":
            j = i + 1
            while j < n and (script[j].isalnum() or script[j] == "_"):
                j += 1
            if j < n and script[j] == "$":
                dolar = script[i : j + 1]
                buffer.append(dolar)
                i = j + 1
                continue
        if caracter == ";":
            sentencia = "".join(buffer).strip()
            if sentencia:
                partes.append(sentencia)
            buffer = []
            i += 1
            continue
        buffer.append(caracter)
        i += 1
    resto = "".join(buffer).strip()
    if resto:
        partes.append(resto)
    return partes


async def aplicar_migraciones() -> None:
    archivos = sorted(SQL_DIR.glob("*.sql"))
    async with conexion() as conn:
        await conn.execute(
            """
            CREATE SCHEMA IF NOT EXISTS sistema;
            CREATE TABLE IF NOT EXISTS sistema.schema_migrations (
              archivo text PRIMARY KEY,
              aplicado_en timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        aplicadas = {
            fila["archivo"]
            for fila in await conn.fetch("SELECT archivo FROM sistema.schema_migrations")
        }
        for archivo in archivos:
            if archivo.name in aplicadas:
                continue
            script = archivo.read_text(encoding="utf-8")
            async with conn.transaction():
                for sentencia in partir_sql(script):
                    await conn.execute(sentencia)
                await conn.execute(
                    "INSERT INTO sistema.schema_migrations (archivo) VALUES ($1)",
                    archivo.name,
                )
            logger.info("Migración aplicada: %s", archivo.name)
