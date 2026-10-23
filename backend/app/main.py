"""Punto de entrada de la API de MiRob (Etapa 1)."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.env import cargar_env

# Carga backend/.env UNA vez al arrancar, antes de atender requests. Así
# MIROB_ADMIN_API_KEY queda disponible en las variables de entorno (en .NET sería
# agregar el archivo de configuración al builder antes de Build()).
cargar_env()

app = FastAPI(title="MiRob API", version="0.1.0")

# CORS abierto a los puertos típicos de dev de Vite (5173) y CRA (3000).
# Esta etapa no tiene auth ni deploy (ver CLAUDE.md), así que no hace
# falta nada más restrictivo que esto para desarrollo local.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
