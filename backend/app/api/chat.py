"""POST /api/v1/chat: consulta al bot de un tenant, en modo raw, humanized o foundry.

Esta capa solo orquesta: valida el request, resuelve el tenant, llama a /rag
(retrieve.py, humanize.py) y arma la respuesta. No hay lógica de retrieval acá.

Los nombres del contrato (tenant_id, message, mode, ...) están en inglés porque es
la API pública que consume el front y el widget; el resto del código sigue en español.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.api.dependencias import (
    ip_del_cliente,
    limitar_chat,
    obtener_estado_limites,
    obtener_generador,
    resolver_tenant,
    responder_429,
)
from app.finops import EstadoLimites, GeneradorConPresupuesto, LimiteExcedido, PresupuestoAgotado
from app.chunk_format import extract_answer, extract_header
from app.foundry.tipos import FoundryError, FoundryNoConfigurado, GeneradorTexto
from app.rag.generate import Turno, generar_respuesta
from app.rag.humanize import humanizar
from app.rag.retrieve import top_k
from app.reindex import get_state

# Tope de la pregunta: evita abusos y mantiene acotado el costo de tokens.
MAX_CARACTERES_MENSAJE = 350

# Cuántos chunks se recuperan y se muestran. En raw se ven todos para auditar el
# retrieval; en foundry, los que superan el umbral son el contexto que recibe el LLM.
CANTIDAD_CHUNKS = 3

# Tope de mensajes que acepta el historial. El recorte "fino" (últimos N turnos)
# se hace al armar el prompt del LLM (app/rag/generate.py); esto solo frena payloads absurdos.
MAX_MENSAJES_HISTORIAL = 20
MAX_CARACTERES_MENSAJE_HISTORIAL = 1000

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")


# --- Request -------------------------------------------------------------


class MensajeHistorial(BaseModel):
    """Un turno previo de la conversación."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_CARACTERES_MENSAJE_HISTORIAL)


class ChatRequest(BaseModel):
    # Obligatorio y sin valor por defecto a propósito: un request sin tenant nunca
    # debe caer "por defecto" en el contenido de otro cliente.
    tenant_id: str
    message: str = Field(max_length=MAX_CARACTERES_MENSAJE)
    conversation_history: list[MensajeHistorial] | None = Field(default=None, max_length=MAX_MENSAJES_HISTORIAL)
    mode: Literal["raw", "humanized", "foundry"] = "raw"

    @field_validator("message")
    @classmethod
    def mensaje_no_vacio(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message no puede estar vacío")
        return value


# --- Response ------------------------------------------------------------


class ChunkRecuperado(BaseModel):
    """Un chunk recuperado por la búsqueda local, con su similitud coseno."""

    chunk_id: int
    source: str  # archivo de origen (ej. "tecnica.md")
    section: str  # header markdown del chunk, o aviso de que es un .txt sin header
    text: str
    score: float


class UsoTokens(BaseModel):
    """Tokens que consumió la respuesta. Todo en 0 cuando no se invocó al LLM."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    tenant_id: str
    mode: Literal["raw", "humanized", "foundry"]
    answer: str
    top_score: float  # similitud del mejor chunk
    threshold: float  # umbral del tenant
    below_threshold: bool  # top_score < threshold
    llm_called: bool
    chunks: list[ChunkRecuperado]
    usage: UsoTokens
    latency_ms: float | None = None  # de la llamada al LLM; None si no se llamó
    deployment: str | None = None  # con qué deployment respondió; None si no se llamó


# --- Endpoint -------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(limitar_chat)])
def chat(
    request: ChatRequest,
    http_request: Request,
    generador: GeneradorTexto = Depends(obtener_generador),
    estado: EstadoLimites = Depends(obtener_estado_limites),
) -> ChatResponse:
    """Recupera los chunks más relevantes del tenant y responde según `mode`.

    - raw: la respuesta es el chunk top-1 tal cual. NO se aplica el umbral: sirve
      para auditar qué trajo el retrieval; `below_threshold` solo avisa.
    - humanized: el top-1 con una apertura conversacional (sin LLM, 0 tokens).
    - foundry: respuesta del LLM sobre los chunks recuperados. Si el mejor score no llega
      al umbral del tenant, NO se llama al LLM: mensaje fijo y 0 tokens. Las llamadas al
      LLM además cuentan contra el límite por IP y el tope diario de tokens (429 si se superan).

    El límite de pedidos por IP del chat se aplica antes de todo (dependencia `limitar_chat`).
    """
    # Se resuelve el tenant antes de cualquier otra cosa: ni siquiera se mira el
    # índice si el id es inválido o inexistente.
    tenant = resolver_tenant(request.tenant_id)

    state = get_state(tenant.tenant_id)
    if state.corpus.empty:
        raise HTTPException(
            status_code=503,
            detail="No hay contenido cargado para este asistente todavía.",
        )

    resultado = top_k(request.message, state.vectorizer, state.matrix, state.corpus, k=CANTIDAD_CHUNKS)
    mejor = resultado.iloc[0]
    top_score = float(mejor["similarity"])

    usage = UsoTokens()
    llm_called = False
    latency_ms = None
    deployment = None

    if request.mode == "foundry":
        historial = [Turno(m.role, m.content) for m in request.conversation_history or []]
        try:
            # El decorador aplica límite por IP y tope diario SOLO si se llega a llamar al LLM.
            generador_limitado = GeneradorConPresupuesto(generador, estado, tenant, ip_del_cliente(http_request))
            generada = generar_respuesta(tenant, request.message, resultado, historial, generador_limitado)
        except LimiteExcedido as error:
            raise responder_429(
                error.reintentar_en, "Demasiadas consultas al asistente en poco tiempo. Probá más tarde."
            ) from error
        except PresupuestoAgotado as error:
            raise responder_429(
                error.reintentar_en, "Se alcanzó el límite diario de consultas al asistente. Probá mañana."
            ) from error
        except FoundryNoConfigurado as error:
            raise HTTPException(status_code=503, detail="El modo foundry no está configurado en el servidor.") from error
        except FoundryError as error:
            raise HTTPException(status_code=502, detail="No se pudo obtener respuesta del modelo.") from error
        answer = generada.texto
        llm_called = generada.llm_invocado
        latency_ms = generada.latencia_ms
        deployment = generada.deployment
        usage = UsoTokens(
            input_tokens=generada.tokens_entrada,
            output_tokens=generada.tokens_salida,
            total_tokens=generada.tokens_total,
        )
        # Solo números y el tenant: nunca el mensaje ni la respuesta (pueden tener datos personales).
        logger.info("chat foundry tenant=%s llm=%s tokens=%d", tenant.tenant_id, llm_called, usage.total_tokens)
    elif request.mode == "humanized":
        answer = humanizar(request.message, extract_answer(mejor["text"]), top_score)
    else:
        answer = mejor["text"]

    chunks = [
        ChunkRecuperado(
            chunk_id=int(fila["chunk_id"]),
            source=fila["source"],
            section=extract_header(fila["text"]),
            text=fila["text"],
            score=float(fila["similarity"]),
        )
        for _, fila in resultado.iterrows()
    ]

    return ChatResponse(
        tenant_id=tenant.tenant_id,
        mode=request.mode,
        answer=answer,
        top_score=top_score,
        threshold=tenant.umbral_similitud,
        below_threshold=top_score < tenant.umbral_similitud,
        llm_called=llm_called,
        chunks=chunks,
        usage=usage,
        latency_ms=latency_ms,
        deployment=deployment,
    )
