# Limsys

API de la plataforma de limpieza para la web y la app móvil. FastAPI y PostgreSQL, sin ORM. El detalle de la lógica, los 159 endpoints, los WebSocket y el circuito de pruebas está en [backend/README.md](backend/README.md).

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

La documentación interactiva queda en `/docs`. Las pruebas:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

`.env` no se versiona. Las contraseñas del README son las de la base local después de correr los scripts de carga.
