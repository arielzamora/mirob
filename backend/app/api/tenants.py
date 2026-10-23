"""GET /api/v1/tenants: los tenants habilitados, para el selector del front.

Expone SOLO el id y el nombre para mostrar. El system prompt, el umbral y los
orígenes permitidos son configuración interna y no salen por la API.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.tenants import listar_tenants

router = APIRouter(prefix="/api/v1")


class TenantPublico(BaseModel):
    tenant_id: str
    nombre: str


@router.get("/tenants", response_model=list[TenantPublico])
def listar() -> list[TenantPublico]:
    """Tenants habilitados, ordenados por id. Los deshabilitados no aparecen."""
    return [TenantPublico(tenant_id=t.tenant_id, nombre=t.nombre) for t in listar_tenants()]
