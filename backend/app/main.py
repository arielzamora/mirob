"""Punto de entrada de la API de MiRob."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.embed import router as embed_router
from app.api.routes import router
from app.api.tenants import router as tenants_router
from app.env import cargar_env
from app.origenes import origenes_cors

# Carga backend/.env UNA vez al arrancar, antes de atender requests. Así
# MIROB_ADMIN_API_KEY queda disponible en las variables de entorno (en .NET sería
# agregar el archivo de configuración al builder antes de Build()).
cargar_env()


def crear_app(origenes: list[str]) -> FastAPI:
    """Arma la aplicación con los orígenes CORS dados (los tests crean la suya con otros)."""
    app = FastAPI(title="MiRob API", version="0.1.0")

    # CORS con lista cerrada: solo los orígenes del front (desarrollo local + MIROB_CORS_ORIGINS).
    # Sin comodín "*". Solo los métodos y headers que la API usa de verdad.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origenes,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    app.include_router(chat_router)
    app.include_router(tenants_router)
    app.include_router(embed_router)
    app.include_router(router)
    return app


app = crear_app(origenes_cors(os.environ))
