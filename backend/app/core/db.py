import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.core.config import settings

pool: asyncpg.Pool | None = None


async def _iniciar_conexion(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )


async def conectar() -> None:
    global pool
    if pool is None:
        pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=10,
            init=_iniciar_conexion,
            command_timeout=30,
        )


async def desconectar() -> None:
    global pool
    if pool is not None:
        await pool.close()
        pool = None


def obtener_pool() -> asyncpg.Pool:
    if pool is None:
        raise RuntimeError("La base de datos no está conectada.")
    return pool


@asynccontextmanager
async def transaccion() -> AsyncIterator[asyncpg.Connection]:
    async with obtener_pool().acquire() as conn:
        async with conn.transaction():
            yield conn


@asynccontextmanager
async def conexion() -> AsyncIterator[asyncpg.Connection]:
    async with obtener_pool().acquire() as conn:
        yield conn
