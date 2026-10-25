"""Acceso con access key: POST /api/v1/acceso canjea la key por una sesión, y el chat la exige (Parte 3.0, grupo B).

Un tenant con `"acceso": "access_key"` en su tenant.json no responde el chat sin sesión. La persona
llega con un link que lleva la key; el front la manda acá UNA vez y desde entonces usa la sesión.
Los errores llevan un `codigo` estable (key_invalida, key_vencida, ...) para que el front muestre el
mensaje justo sin tener que interpretar textos.
"""

import logging
from collections.abc import Callable
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import sesiones
from app.accesskeys import (
    LARGO_MAXIMO_CLAVE,
    AccessKeyError,
    AlmacenClavesError,
    ClaveRevocada,
    ClaveVencida,
    ServicioClaves,
)
from app.api.dependencias import limitar_acceso, obtener_reloj, obtener_servicio_claves, resolver_tenant
from app.tenants import ACCESO_POR_KEY, TenantConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

PEDIR_KEY_NUEVA = "Pedí una nueva access key a quien te la envió."


def _error(status: int, codigo: str, mensaje: str) -> HTTPException:
    """HTTPException con detalle estructurado: {"detail": {"codigo": ..., "mensaje": ...}}."""
    return HTTPException(status_code=status, detail={"codigo": codigo, "mensaje": mensaje})


def _mensaje_con_contacto(base: str, tenant: TenantConfig) -> str:
    return f"{base} {tenant.contacto_acceso or PEDIR_KEY_NUEVA}"


def _secreto_o_503() -> bytes:
    try:
        return sesiones.leer_secreto()
    except sesiones.ConfigSesionError as error:
        logger.error("Acceso por key sin configurar: %s", error)
        raise _error(503, "acceso_no_configurado", "El acceso por access key no está configurado en el servidor.") from error


def _errores_de_clave(error: AccessKeyError, tenant: TenantConfig) -> HTTPException:
    """Traduce los errores del servicio de keys a respuestas HTTP."""
    if isinstance(error, ClaveRevocada):
        return _error(403, "key_revocada", _mensaje_con_contacto("Esta access key fue revocada.", tenant))
    if isinstance(error, ClaveVencida):
        return _error(403, "key_vencida", _mensaje_con_contacto("Esta access key venció.", tenant))
    if isinstance(error, AlmacenClavesError):
        logger.error("No se pudo leer el almacén de access keys.")
        return _error(503, "acceso_no_disponible", "El acceso por access key no está disponible por el momento.")
    # ClaveInvalida (no existe, o es de otro tenant): siempre la misma respuesta.
    return _error(401, "key_invalida", "Access key inválida.")


# --- POST /api/v1/acceso -----------------------------------------------------------


class AccesoRequest(BaseModel):
    tenant_id: str
    # El tope evita procesar textos enormes; una key real mide ~43 caracteres.
    key: str = Field(max_length=LARGO_MAXIMO_CLAVE)


class AccesoResponse(BaseModel):
    tenant_id: str
    session_token: str
    expires_in: int  # segundos de vida de la sesión
    etiqueta: str  # a quién se le dio la key (la ve la propia persona: sirve para saludarla)
    key_expires_at: datetime


@router.post("/acceso", response_model=AccesoResponse, dependencies=[Depends(limitar_acceso)])
def acceso(
    request: AccesoRequest,
    servicio: ServicioClaves = Depends(obtener_servicio_claves),
    reloj: Callable[[], datetime] = Depends(obtener_reloj),
) -> AccesoResponse:
    """Canjea una access key vigente por una sesión corta."""
    tenant = resolver_tenant(request.tenant_id)
    if tenant.acceso != ACCESO_POR_KEY:
        raise _error(400, "tenant_publico", "Este asistente es de acceso libre: no necesita access key.")

    secreto = _secreto_o_503()
    try:
        duracion = sesiones.duracion_de_sesion()
    except sesiones.ConfigSesionError as error:
        logger.error("Duración de sesión inválida: %s", error)
        raise _error(503, "acceso_no_configurado", "El acceso por access key no está configurado en el servidor.") from error

    try:
        registro = servicio.validar(request.key.strip(), tenant.tenant_id)
    except AccessKeyError as error:
        raise _errores_de_clave(error, tenant) from error

    ahora = reloj()
    token, expira_en = sesiones.emitir(registro, secreto, ahora, duracion)
    return AccesoResponse(
        tenant_id=tenant.tenant_id,
        session_token=token,
        expires_in=max(0, int((expira_en - ahora).total_seconds())),
        etiqueta=registro.etiqueta,
        key_expires_at=registro.vence_en,
    )


# --- Guardia del chat -----------------------------------------------------------------


def _token_bearer(autorizacion: str | None) -> str | None:
    """Saca el token de un header `Authorization: Bearer <token>`. None si falta o tiene otro formato."""
    if not autorizacion:
        return None
    esquema, _, token = autorizacion.partition(" ")
    if esquema.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def exigir_acceso_al_chat(
    tenant: TenantConfig,
    autorizacion: str | None,
    servicio: ServicioClaves,
    reloj: Callable[[], datetime],
) -> None:
    """No hace nada si el tenant es público. Si exige access key, deja pasar solo con una sesión válida.

    Comprueba, en este orden: que haya sesión, que su firma sea auténtica y no esté vencida, que sea de ESTE
    tenant y que la key detrás siga vigente (así una revocación corta el acceso al instante).
    """
    if tenant.acceso != ACCESO_POR_KEY:
        return

    token = _token_bearer(autorizacion)
    if token is None:
        raise _error(401, "acceso_requerido", "Este asistente se usa con una access key. Abrí el enlace que te enviaron.")

    secreto = _secreto_o_503()
    try:
        sesion = sesiones.leer(token, secreto, reloj())
    except sesiones.SesionExpirada as error:
        raise _error(401, "sesion_vencida", "Tu sesión venció. Abrí de nuevo el enlace con tu access key.") from error
    except sesiones.SesionInvalida as error:
        raise _error(401, "acceso_requerido", "Este asistente se usa con una access key. Abrí el enlace que te enviaron.") from error

    if sesion.tenant_id != tenant.tenant_id:
        raise _error(401, "acceso_requerido", "Este asistente se usa con una access key. Abrí el enlace que te enviaron.")

    try:
        servicio.verificar_vigente(sesion.key_id, tenant.tenant_id)
    except AccessKeyError as error:
        raise _errores_de_clave(error, tenant) from error


def encabezado_autorizacion(authorization: str | None = Header(default=None)) -> str | None:
    """Dependencia que lee el header Authorization del pedido (lo usa el endpoint del chat)."""
    return authorization
