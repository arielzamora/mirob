"""Administración de access keys: crear, listar y revocar (Parte 3.0, grupo D).

    POST /admin/keys                  crea una key y la devuelve UNA vez, con el enlace listo para enviar
    GET  /admin/keys                  lista las keys con su estado y sus contadores (nunca la key ni su hash)
    POST /admin/keys/{key_id}/revoke  revoca una key: corta el acceso al instante

Todo exige la API key del admin (header X-API-Key, igual que /admin/content) y pasa por el límite de
pedidos del admin. Revocar es un POST y no un DELETE porque el CORS de la API solo permite GET y POST.

El backend nunca ve el texto de las preguntas de las keys: los contadores son solo números y fechas.
"""

import logging
import os
from collections.abc import Callable
from datetime import datetime
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.accesskeys import (
    LARGO_MAXIMO_ETIQUETA,
    AccessKey,
    AlmacenClavesError,
    ClaveInvalida,
    DatosInvalidos,
    ServicioClaves,
    DIAS_VENCIMIENTO_MAXIMO,
    DIAS_VENCIMIENTO_POR_DEFECTO,
)
from app.api.dependencias import limitar_admin, obtener_reloj, obtener_servicio_claves, resolver_tenant
from app.api.embed import VAR_FRONTEND_URL
from app.api.security import exigir_api_key
from app.origenes import validar_origen
from app.tenants import ACCESO_POR_KEY, TenantConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", dependencies=[Depends(limitar_admin), Depends(exigir_api_key)])


# --- Modelos de la API ---------------------------------------------------------------


class KeyAdmin(BaseModel):
    """Lo que el admin ve de una key. Nunca incluye la key ni su hash."""

    key_id: str
    tenant_id: str
    etiqueta: str
    estado: Literal["activa", "vencida", "revocada"]
    creada_en: datetime
    vence_en: datetime
    cupo_preguntas: int | None
    preguntas_usadas: int
    preguntas_restantes: int | None
    accesos: int
    ultimo_acceso_en: datetime | None
    ultima_pregunta_en: datetime | None


class CrearKeyRequest(BaseModel):
    tenant_id: str
    etiqueta: str = Field(max_length=LARGO_MAXIMO_ETIQUETA)
    dias_vigencia: int = Field(default=DIAS_VENCIMIENTO_POR_DEFECTO, ge=1, le=DIAS_VENCIMIENTO_MAXIMO)
    cupo_preguntas: int | None = Field(default=None, ge=1)


class KeyCreada(KeyAdmin):
    # La key en claro: es la ÚNICA vez que se devuelve. En el servidor solo queda su hash.
    key: str
    # Enlace listo para enviar. None si el servidor no tiene configurado MIROB_FRONTEND_URL.
    enlace: str | None
    aviso: str = "Guardá la key o el enlace ahora: no se vuelve a mostrar."


def _a_modelo(clave: AccessKey, ahora: datetime) -> dict:
    return {
        "key_id": clave.key_id,
        "tenant_id": clave.tenant_id,
        "etiqueta": clave.etiqueta,
        "estado": clave.estado(ahora),
        "creada_en": clave.creada_en,
        "vence_en": clave.vence_en,
        "cupo_preguntas": clave.cupo_preguntas,
        "preguntas_usadas": clave.preguntas_usadas,
        "preguntas_restantes": clave.preguntas_restantes(),
        "accesos": clave.accesos,
        "ultimo_acceso_en": clave.ultimo_acceso_en,
        "ultima_pregunta_en": clave.ultima_pregunta_en,
    }


def _armar_enlace(tenant_id: str, key: str) -> str | None:
    """https://<front>/?tenant=<id>&embed=1#k=<key>. La key va en el fragmento (#): no viaja al servidor."""
    try:
        front = validar_origen(os.environ.get(VAR_FRONTEND_URL, "").strip().rstrip("/"))
    except ValueError:
        return None
    return f"{front}/?tenant={quote(tenant_id)}&embed=1#k={key}"


def _tenant_con_keys(tenant_id: str) -> TenantConfig:
    """Resuelve el tenant y exige que use access keys: crear keys para un tenant público no sirve de nada."""
    tenant = resolver_tenant(tenant_id)
    if tenant.acceso != ACCESO_POR_KEY:
        raise HTTPException(
            status_code=400,
            detail=(
                f"El tenant '{tenant.tenant_id}' es de acceso público. Para usar access keys, poné "
                '"acceso": "access_key" en su tenant.json.'
            ),
        )
    return tenant


def _error_de_almacen(error: AlmacenClavesError) -> HTTPException:
    logger.error("No se pudo usar el almacén de access keys.")  # sin el detalle: el archivo tiene hashes
    return HTTPException(status_code=503, detail="El almacén de access keys no está disponible.")


# --- Endpoints -------------------------------------------------------------------------


@router.post("/keys", response_model=KeyCreada, status_code=201)
def crear_key(
    pedido: CrearKeyRequest,
    servicio: ServicioClaves = Depends(obtener_servicio_claves),
    reloj: Callable[[], datetime] = Depends(obtener_reloj),
) -> KeyCreada:
    """Crea una key para un tenant con acceso por key y devuelve la key en claro (una sola vez) y su enlace."""
    tenant = _tenant_con_keys(pedido.tenant_id)
    try:
        registro, key = servicio.crear(
            tenant.tenant_id, pedido.etiqueta, pedido.dias_vigencia, pedido.cupo_preguntas
        )
    except DatosInvalidos as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except AlmacenClavesError as error:
        raise _error_de_almacen(error) from error

    return KeyCreada(
        **_a_modelo(registro, reloj()),
        key=key,
        enlace=_armar_enlace(tenant.tenant_id, key),
    )


@router.get("/keys", response_model=list[KeyAdmin])
def listar_keys(
    tenant_id: str | None = None,
    servicio: ServicioClaves = Depends(obtener_servicio_claves),
    reloj: Callable[[], datetime] = Depends(obtener_reloj),
) -> list[KeyAdmin]:
    """Las keys (de un tenant o de todos), de la más nueva a la más vieja, con estado y contadores."""
    if tenant_id is not None:
        resolver_tenant(tenant_id)  # 422 si el formato es inválido, 404 si no existe
    try:
        claves = servicio.listar(tenant_id)
    except AlmacenClavesError as error:
        raise _error_de_almacen(error) from error
    ahora = reloj()
    return [KeyAdmin(**_a_modelo(clave, ahora)) for clave in claves]


@router.post("/keys/{key_id}/revoke", response_model=KeyAdmin)
def revocar_key(
    key_id: str,
    servicio: ServicioClaves = Depends(obtener_servicio_claves),
    reloj: Callable[[], datetime] = Depends(obtener_reloj),
) -> KeyAdmin:
    """Revoca la key (no se borra: queda el registro). Revocar dos veces no falla."""
    try:
        revocada = servicio.revocar(key_id)
    except ClaveInvalida as error:
        raise HTTPException(status_code=404, detail="No existe una access key con ese identificador.") from error
    except AlmacenClavesError as error:
        raise _error_de_almacen(error) from error
    return KeyAdmin(**_a_modelo(revocada, reloj()))
