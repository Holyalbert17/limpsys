from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self.salas: dict[str, set[WebSocket]] = {}

    async def conectar(self, sala: str, ws: WebSocket) -> None:
        await ws.accept()
        self.salas.setdefault(sala, set()).add(ws)

    def salir(self, sala: str, ws: WebSocket) -> None:
        self.salas.get(sala, set()).discard(ws)

    async def emitir(self, sala: str, mensaje: dict) -> None:
        muertas = []
        for ws in list(self.salas.get(sala, set())):
            try:
                await ws.send_json(mensaje)
            except Exception:
                muertas.append(ws)
        for ws in muertas:
            self.salir(sala, ws)


hub = Hub()
