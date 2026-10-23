"""Adaptador genérico de chat: instrucciones + mensaje -> texto, tokens y latencia.

No sabe NADA de MiRob (ni de tenants, ni de chunks, ni de system prompts): eso
vive en el dominio. Este módulo solo sabe hablar con el modelo y medir la llamada.
"""

import logging
import time

from openai import OpenAI

from app.foundry.config import FoundrySettings
from app.foundry.tipos import MAX_TOKENS_SALIDA, ChatResult

# Segundos máximos de espera por llamada. Sin esto, un modelo colgado dejaría
# el request del chat esperando minutos (el default del SDK es de 10 minutos).
TIMEOUT_SEGUNDOS = 30.0

logger = logging.getLogger(__name__)


def generar(
    cliente: OpenAI,
    settings: FoundrySettings,
    instrucciones: str,
    mensaje: str,
    max_tokens_salida: int = MAX_TOKENS_SALIDA,
) -> ChatResult:
    """Llama al modelo con la API Responses y devuelve texto, tokens y latencia.

    Datos verificados contra openai 2.54.0:
      - cliente.responses.create(model=..., instructions=..., input=...) acepta,
        entre otros, `max_output_tokens`, `temperature`, `store`, `stream` y `timeout`.
      - `model` recibe el nombre de TU DEPLOYMENT (settings.chat_deployment), por
        ejemplo el de gpt-5.4-nano.
      - `reasoning={"effort": ...}` fija cuánto "piensa" un modelo de razonamiento. Esos modelos
        cuentan los tokens de razonamiento DENTRO de `max_output_tokens`: con un tope bajo y un
        esfuerzo alto, pueden gastarlo todo pensando y devolver el texto vacío.
      - `.status` vale "incomplete" y `.incomplete_details.reason` indica por qué se cortó
        ("max_output_tokens" o "content_filter").
      - El objeto de respuesta tiene `.output_text` (el texto) y `.usage` con
        input_tokens, output_tokens y total_tokens. OJO: `.usage` puede ser None,
        así que se usa 0 si falta.
    """
    # Parámetros opcionales: solo se mandan si están configurados. Un modelo de razonamiento
    # rechaza `temperature` con razonamiento activo, así que por defecto no se envía.
    opcionales: dict = {}
    if settings.temperature is not None:
        opcionales["temperature"] = settings.temperature
    if settings.reasoning_effort is not None:
        opcionales["reasoning"] = {"effort": settings.reasoning_effort}

    # perf_counter es un reloj monotónico de alta resolución, el equivalente a
    # Stopwatch en .NET (no usar time.time() para medir duraciones).
    inicio = time.perf_counter()
    respuesta = cliente.responses.create(
        model=settings.chat_deployment,
        instructions=instrucciones,
        input=mensaje,
        max_output_tokens=max_tokens_salida,
        # store=False: no guardamos la conversación del lado de Azure (privacidad).
        store=False,
        timeout=TIMEOUT_SEGUNDOS,
        **opcionales,
    )
    latencia_ms = (time.perf_counter() - inicio) * 1000

    incompleta_por = None
    if respuesta.status == "incomplete":
        detalles = respuesta.incomplete_details
        incompleta_por = detalles.reason if detalles and detalles.reason else "desconocido"
        # Solo el motivo y los números: nunca el texto (puede tener datos personales).
        logger.warning(
            "Respuesta incompleta de Foundry: motivo=%s tokens_salida=%s",
            incompleta_por,
            respuesta.usage.output_tokens if respuesta.usage else "?",
        )

    uso = respuesta.usage
    return ChatResult(
        texto=respuesta.output_text,
        tokens_entrada=uso.input_tokens if uso else 0,
        tokens_salida=uso.output_tokens if uso else 0,
        tokens_total=uso.total_tokens if uso else 0,
        latencia_ms=latencia_ms,
        deployment=settings.chat_deployment,
        incompleta_por=incompleta_por,
    )
