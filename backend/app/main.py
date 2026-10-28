"""Punto de entrada de la API de MiRob."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.acceso import router as acceso_router
from app.api.admin_keys import router as admin_keys_router
from app.api.chat import router as chat_router
from app.api.dependencias import obtener_generador
from app.api.embed import router as embed_router
from app.api.health import router as health_router
from app.api.routes import router
from app.api.tenants import router as tenants_router
from app.env import cargar_env
from app.origenes import origenes_cors
from app.precalentamiento import precalentar_en_segundo_plano
from app.reindex import get_state
from app.tenants import listar_tenants

# Carga backend/.env UNA vez al arrancar, antes de atender requests. Así
# MIROB_ADMIN_API_KEY queda disponible en las variables de entorno (en .NET sería
# agregar el archivo de configuración al builder antes de Build()).
cargar_env()


@asynccontextmanager
async def _ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
    """Lo que corre al arrancar (antes del `yield`) y al apagar. Es como IHostedService.StartAsync en .NET.

    Al arrancar lanza el precalentamiento en un hilo aparte: la API ya atiende pedidos mientras tanto.
    Los tests no lo ejecutan: solo corre cuando el servidor arranca de verdad (TestClient sin `with`).
    """
    precalentar_en_segundo_plano(
        [tenant.tenant_id for tenant in listar_tenants()], get_state, obtener_generador()
    )
    yield


def crear_app(origenes: list[str]) -> FastAPI:
    """Arma la aplicación con los orígenes CORS dados (los tests crean la suya con otros)."""
    app = FastAPI(title="MiRob API", version="0.1.0", lifespan=_ciclo_de_vida)

    # CORS con lista cerrada: solo los orígenes del front (desarrollo local + MIROB_CORS_ORIGINS).
    # Sin comodín "*". Solo los métodos y headers que la API usa de verdad.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origenes,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "Authorization"],
    )

    app.include_router(acceso_router)
    app.include_router(admin_keys_router)
    app.include_router(chat_router)
    app.include_router(tenants_router)
    app.include_router(embed_router)
    app.include_router(health_router)
    app.include_router(router)
    return app


app = crear_app(origenes_cors(os.environ))
