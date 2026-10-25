"""GET /api/v1/tenants: los tenants habilitados, para el selector del front.

Expone SOLO el id, el nombre para mostrar y si pide access key (para que el front sepa si mostrar la
pantalla de acceso). El system prompt, el umbral y los orígenes permitidos son configuración interna
y no salen por la API.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.tenants import ACCESO_PUBLICO, listar_tenants

router = APIRouter(prefix="/api/v1")


class TenantPublico(BaseModel):
    tenant_id: str
    nombre: str
    acceso: str = ACCESO_PUBLICO  # "publico" o "access_key"


@router.get("/tenants", response_model=list[TenantPublico])
def listar() -> list[TenantPublico]:
    """Tenants habilitados, ordenados por id. Los deshabilitados no aparecen."""
    return [TenantPublico(tenant_id=t.tenant_id, nombre=t.nombre, acceso=t.acceso) for t in listar_tenants()]
