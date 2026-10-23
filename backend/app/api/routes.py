"""Endpoints de administración, por tenant: POST /admin/content y POST /admin/reindex.

El chat vive en app/api/chat.py. Esta capa solo orquesta: valida el request,
resuelve el tenant, guarda el archivo y llama a reindex.py.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.dependencias import limitar_admin, resolver_tenant
from app.api.security import exigir_api_key
from app.api.uploads import TAMANO_MAXIMO_BYTES, validar_contenido, validar_nombre_archivo
from app.reindex import carpeta_de_contenido, reindex
from app.tenants import listar_tenants

router = APIRouter()


# --- POST /admin/content -------------------------------------------------


class AdminContentResponse(BaseModel):
    filename: str
    total_chunks: int
    total_files: int


@router.post(
    "/admin/content",
    response_model=AdminContentResponse,
    # Exige el header X-API-Key antes de ejecutar la ruta (ver app/api/security.py).
    # limitar_admin va primero: frena la fuerza bruta de la clave antes de compararla.
    dependencies=[Depends(limitar_admin), Depends(exigir_api_key)],
)
async def admin_content(
    tenant_id: str = Form(...), file: UploadFile = File(...)
) -> AdminContentResponse:
    """Guarda un .txt/.md en data/tenants/<tenant_id>/ y reindexa el corpus de ESE tenant.

    Requiere el header X-API-Key (solución temporal, ver ADR 0002).

    Reindexar de cero (en vez de solo agregar el archivo nuevo) es
    intencional: mantiene ingest.py como única fuente de verdad de cómo
    se arma el corpus, sin duplicar esa lógica acá. El corpus de esta
    etapa es chico, así que el costo de rehacerlo entero es insignificante.
    """
    # 0) El tenant primero: define en qué carpeta se escribe. Es un dato no confiable
    #    que ya viene validado (formato + lista blanca) cuando resolver_tenant devuelve.
    tenant = resolver_tenant(tenant_id)
    # El tenant "demo" lee el perfil ficticio versionado: no se le sube contenido encima.
    if tenant.usa_sample:
        raise HTTPException(status_code=403, detail="El tenant demo es de solo lectura.")

    # 1) Validar el NOMBRE antes de usarlo para armar cualquier ruta. El nombre lo
    #    manda el cliente, así que es un dato no confiable (ver app/api/uploads.py).
    try:
        nombre = validar_nombre_archivo(file.filename)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    # 2) Leer como máximo TAMANO_MAXIMO_BYTES + 1 bytes: ese byte de más alcanza
    #    para detectar que el archivo se pasó, sin cargar un archivo gigante en memoria.
    contenido = await file.read(TAMANO_MAXIMO_BYTES + 1)

    # 3) Validar tamaño y codificación ANTES de escribir a disco. 413 = "Payload Too
    #    Large"; cualquier otro problema de contenido es un 422.
    try:
        validar_contenido(contenido)
    except ValueError as error:
        se_paso_de_tamano = len(contenido) > TAMANO_MAXIMO_BYTES
        raise HTTPException(status_code=413 if se_paso_de_tamano else 422, detail=str(error)) from error

    carpeta = carpeta_de_contenido(tenant)
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / nombre).write_bytes(contenido)

    state = reindex(tenant.tenant_id)

    return AdminContentResponse(
        filename=nombre,
        total_chunks=len(state.corpus),
        total_files=int(state.corpus["source"].nunique()),
    )


# --- POST /admin/reindex ---------------------------------------------------


class AdminReindexResponse(BaseModel):
    # tenant_id -> cantidad de chunks indexados (0 = el tenant no tiene contenido).
    chunks_por_tenant: dict[str, int]


@router.post(
    "/admin/reindex",
    response_model=AdminReindexResponse,
    dependencies=[Depends(limitar_admin), Depends(exigir_api_key)],
)
def admin_reindex(tenant_id: str | None = Form(default=None)) -> AdminReindexResponse:
    """Reconstruye el índice de un tenant (o de todos si no se indica) desde sus archivos.

    Para cuando el contenido se cambió por fuera de la API (por ejemplo, subiendo archivos
    directo al share de Azure Files): el índice vive en memoria y solo se arma al arrancar o
    al subir un archivo por /admin/content, así que sin esto habría que reiniciar el backend.

    Es `def` y no `async def` a propósito: reindexar es trabajo de CPU y disco; FastAPI corre
    los `def` en un hilo aparte y no bloquea al resto de los pedidos.
    """
    if tenant_id is not None:
        tenants = [resolver_tenant(tenant_id)]
    else:
        tenants = listar_tenants()

    return AdminReindexResponse(
        chunks_por_tenant={t.tenant_id: len(reindex(t.tenant_id).corpus) for t in tenants}
    )
