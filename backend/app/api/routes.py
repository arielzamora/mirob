"""Endpoint de administración: POST /admin/content, por tenant.

El chat vive en app/api/chat.py. Esta capa solo orquesta: valida el request,
resuelve el tenant, guarda el archivo y llama a reindex.py.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.dependencias import limitar_admin, resolver_tenant
from app.api.security import exigir_api_key
from app.api.uploads import TAMANO_MAXIMO_BYTES, validar_contenido, validar_nombre_archivo
from app.reindex import carpeta_de_contenido, reindex

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
