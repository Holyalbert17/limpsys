import asyncio
import json
import logging
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.bootstrap import sembrar
from app.core.config import settings
from app.core.db import conectar, desconectar, transaccion
from app.core.errors import ApiError
from app.core.util import a_json, json_default
from app.migrate import aplicar_migraciones
from app.routers import admin, archivos, auth, cliente, empleado, publico, ws
from app.services.tareas import ciclo

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("limsys")


@asynccontextmanager
async def vida(app: FastAPI):
    await conectar()
    await aplicar_migraciones()
    await sembrar()
    tarea = asyncio.create_task(_bucle())
    yield
    tarea.cancel()
    await desconectar()


async def _bucle():
    while True:
        try:
            await ciclo()
        except Exception:
            logger.exception("Falló el mantenimiento")
        await asyncio.sleep(30)


app = FastAPI(
    title="Limsys",
    summary="API de la plataforma de limpieza para web y móvil.",
    version="1.0.0",
    lifespan=vida,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origenes_cors,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def cabeceras(request: Request, call_next):
    respuesta = await call_next(request)
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["Referrer-Policy"] = "no-referrer"
    return respuesta


def _error(status: int, code: str, message: str, extra: dict | None = None) -> JSONResponse:
    cuerpo = {"code": code, "message": message, **(extra or {})}
    return JSONResponse(status_code=status, content={"detail": cuerpo})


@app.exception_handler(ApiError)
async def manejar_api(request: Request, exc: ApiError):
    return _error(exc.status, exc.code, exc.message, exc.extra)


@app.exception_handler(RequestValidationError)
async def manejar_validacion(request: Request, exc: RequestValidationError):
    return _error(422, "DATOS_INVALIDOS", "Revisá los datos enviados.", {"errores": exc.errors()})


@app.exception_handler(asyncpg.UniqueViolationError)
async def manejar_unico(request: Request, exc: asyncpg.UniqueViolationError):
    return _error(409, "DUPLICADO", "Ese dato ya existe.")


@app.exception_handler(Exception)
async def manejar_resto(request: Request, exc: Exception):
    logger.exception("Error interno")
    return _error(500, "ERROR_INTERNO", "Ocurrió un error interno.")


@app.get("/", include_in_schema=False)
async def raiz():
    return RedirectResponse(url="/docs")


@app.get("/api/v1/salud", tags=["Sistema"])
async def salud():
    async with transaccion() as conn:
        await conn.fetchval("SELECT 1")
    return {"ok": True, "servicio": "limsys"}


@app.post("/api/v1/pagos/webhook", tags=["Pagos"])
async def webhook(request: Request):
    from uuid import UUID

    from app.services.finanzas import confirmar_pago_pendiente

    secreto = request.headers.get("x-webhook-secret")
    if secreto != settings.webhook_secret:
        return _error(401, "WEBHOOK", "El secreto no coincide.")
    try:
        cuerpo = await request.json()
        pago_id = UUID(str(cuerpo["pago_id"]))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return _error(422, "DATOS_INVALIDOS", "El aviso tiene que traer un pago_id válido.")
    async with transaccion() as conn:
        pago = await confirmar_pago_pendiente(conn, pago_id)
    return {"estado": pago["estado"]}


app.include_router(auth.router, prefix="/api/v1")
app.include_router(publico.router, prefix="/api/v1")
app.include_router(cliente.router, prefix="/api/v1")
app.include_router(empleado.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1")
app.include_router(archivos.router, prefix="/api/v1")
app.include_router(ws.router, prefix="/api/v1")
