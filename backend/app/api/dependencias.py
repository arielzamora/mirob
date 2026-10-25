"""Piezas compartidas por los routers de la API.

En FastAPI las "dependencias" son funciones reutilizables que los endpoints
llaman o declaran (equivalente a un servicio inyectado o a un filtro en ASP.NET Core).
"""

import math
import os
from collections.abc import Callable
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import Depends, HTTPException, Request

from app.accesskeys import AlmacenClavesJson, ServicioClaves
from app.finops import EstadoLimites, Limites
from app.foundry.generador import FoundryGenerador
from app.foundry.tipos import GeneradorTexto
from app.tenants import TenantConfig, TenantInvalido, TenantNoEncontrado, obtener_tenant


def resolver_tenant(tenant_id: str) -> TenantConfig:
    """Traduce los errores del registro de tenants a respuestas HTTP.

    422 si el formato del id es inválido, 404 si no existe o está deshabilitado
    (como un `NotFound()` / `UnprocessableEntity()` devuelto desde un controller).
    """
    try:
        return obtener_tenant(tenant_id)
    except TenantInvalido as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except TenantNoEncontrado as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@lru_cache(maxsize=1)
def _generador_unico() -> FoundryGenerador:
    """Un solo FoundryGenerador para todo el proceso (singleton). No lee el entorno todavía."""
    return FoundryGenerador()


def obtener_generador() -> GeneradorTexto:
    """Dependencia que entrega el generador de texto al endpoint del chat.

    Es el punto de inyección (como registrar una interfaz en el contenedor de DI):
    los tests lo reemplazan con `app.dependency_overrides[obtener_generador] = ...`
    para no usar red ni gastar tokens.
    """
    return _generador_unico()


# --- Límites (rate limit por IP y tope diario) --------------------------------------

VAR_CONFIAR_EN_PROXY = "MIROB_TRUST_PROXY"


@lru_cache(maxsize=1)
def _estado_limites_unico() -> EstadoLimites:
    """Un solo EstadoLimites para todo el proceso, con los valores del entorno."""
    return EstadoLimites(Limites.from_env(os.environ))


def obtener_estado_limites() -> EstadoLimites:
    """Dependencia que entrega los límites; los tests la reemplazan con límites propios."""
    return _estado_limites_unico()


def ip_del_cliente(request: Request) -> str:
    """IP a la que se le aplican los límites.

    Detrás de un proxy (Azure Container Apps) la IP de la conexión es la del proxy, y la
    real viaja en X-Forwarded-For. Ese header lo puede escribir cualquiera, así que SOLO se
    usa si MIROB_TRUST_PROXY=1, y se toma el ÚLTIMO valor: es el que agregó nuestro proxy
    (los anteriores los manda el cliente y pueden ser falsos).
    """
    if os.environ.get(VAR_CONFIAR_EN_PROXY, "").strip().lower() in ("1", "true"):
        reenviado = request.headers.get("x-forwarded-for", "")
        ultimo = reenviado.split(",")[-1].strip()
        if ultimo:
            return ultimo
    return request.client.host if request.client else "desconocida"


def responder_429(reintentar_en: float, detalle: str) -> HTTPException:
    """429 Too Many Requests con el header estándar Retry-After (en segundos enteros, mínimo 1)."""
    return HTTPException(
        status_code=429,
        detail=detalle,
        headers={"Retry-After": str(max(1, math.ceil(reintentar_en)))},
    )


def limitar_chat(request: Request, estado: EstadoLimites = Depends(obtener_estado_limites)) -> None:
    """Dependencia del chat: corta con 429 si la IP superó sus pedidos por minuto."""
    espera = estado.chat.intentar(ip_del_cliente(request))
    if espera is not None:
        raise responder_429(espera, "Demasiados mensajes seguidos. Esperá un momento y volvé a intentar.")


def limitar_acceso(request: Request, estado: EstadoLimites = Depends(obtener_estado_limites)) -> None:
    """Dependencia del canje de access keys: frena a quien prueba keys al azar."""
    espera = estado.acceso.intentar(ip_del_cliente(request))
    if espera is not None:
        raise responder_429(espera, "Demasiados intentos de acceso. Esperá un momento y volvé a intentar.")


# --- Access keys (Parte 3.0) -----------------------------------------------------------


@lru_cache(maxsize=1)
def _servicio_claves_unico() -> ServicioClaves:
    """Un solo servicio de keys para todo el proceso, sobre el archivo real (junto al contenido)."""
    return ServicioClaves(AlmacenClavesJson())


def obtener_servicio_claves() -> ServicioClaves:
    """Dependencia que entrega el servicio de keys; los tests la reemplazan con uno sobre una carpeta temporal."""
    return _servicio_claves_unico()


def obtener_reloj() -> Callable[[], datetime]:
    """Dependencia que entrega el reloj (UTC); los tests lo reemplazan para simular el paso del tiempo."""
    return lambda: datetime.now(timezone.utc)


def limitar_admin(request: Request, estado: EstadoLimites = Depends(obtener_estado_limites)) -> None:
    """Dependencia del admin: frena los intentos repetidos (por ejemplo, adivinar la API key)."""
    espera = estado.admin.intentar(ip_del_cliente(request))
    if espera is not None:
        raise responder_429(espera, "Demasiados pedidos al admin. Esperá un momento.")
