"""Endpoints HTTP de MiRob (Etapa 1): POST /chat y POST /admin/content.

Esta capa solo orquesta: valida el request, llama a /rag (retrieve.py,
humanize.py) y a reindex.py, y arma la respuesta HTTP. No hay lógica de
retrieval ni de vectorización acá — eso vive en app/rag y no se toca.
"""

from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, field_validator

from app.api.security import exigir_api_key
from app.api.uploads import TAMANO_MAXIMO_BYTES, validar_contenido, validar_nombre_archivo
from app.chunk_format import extract_answer, extract_header
from app.rag.humanize import humanizar
from app.rag.retrieve import top_k
from app.reindex import CONTENT_DIR, get_state, reindex

router = APIRouter()


# --- POST /chat ---------------------------------------------------------


class ChatRequest(BaseModel):
    pregunta: str
    modo: Literal["raw", "humanized"] = "raw"

    @field_validator("pregunta")
    @classmethod
    def pregunta_no_vacia(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("pregunta no puede estar vacía")
        return value


class ChatResponse(BaseModel):
    respuesta: str
    score: float
    source: str  # nombre del archivo de origen del chunk (ej. "tecnica.md")
    seccion: str  # header markdown del chunk, o aviso de que es un .txt sin header
    modo: Literal["raw", "humanized"]


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """Recupera el chunk más relevante para `pregunta` y lo devuelve.

    Sin generación de texto (ver CLAUDE.md): en modo "raw" la respuesta es
    el chunk tal cual; en "humanized" pasa por humanize.py antes de volver.
    Todavía no se combinan varios chunks — se usa solo el top-1 (misma
    decisión que ya se tomó en example_usage.py).
    """
    state = get_state()

    if state.corpus.empty:
        raise HTTPException(
            status_code=503,
            detail="No hay contenido cargado todavía. Subí un archivo con POST /admin/content.",
        )

    resultado = top_k(request.pregunta, state.vectorizer, state.matrix, state.corpus, k=1)
    row = resultado.iloc[0]

    if request.modo == "humanized":
        respuesta = humanizar(request.pregunta, extract_answer(row["text"]), row["similarity"])
    else:
        respuesta = row["text"]

    return ChatResponse(
        respuesta=respuesta,
        score=float(row["similarity"]),
        source=row["source"],
        seccion=extract_header(row["text"]),
        modo=request.modo,
    )


# --- POST /admin/content -------------------------------------------------


class AdminContentResponse(BaseModel):
    filename: str
    total_chunks: int
    total_files: int


@router.post(
    "/admin/content",
    response_model=AdminContentResponse,
    # Exige el header X-API-Key antes de ejecutar la ruta (ver app/api/security.py).
    dependencies=[Depends(exigir_api_key)],
)
async def admin_content(file: UploadFile = File(...)) -> AdminContentResponse:
    """Guarda un .txt/.md en data/ y reindexa el corpus completo.

    Requiere el header X-API-Key (solución temporal, ver ADR 0002).

    Reindexar de cero (en vez de solo agregar el archivo nuevo) es
    intencional: mantiene ingest.py como única fuente de verdad de cómo
    se arma el corpus, sin duplicar esa lógica acá. El corpus de esta
    etapa es chico, así que el costo de rehacerlo entero es insignificante.
    """
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

    CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    (CONTENT_DIR / nombre).write_bytes(contenido)

    state = reindex()

    return AdminContentResponse(
        filename=nombre,
        total_chunks=len(state.corpus),
        total_files=int(state.corpus["source"].nunique()),
    )
