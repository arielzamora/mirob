"""Adaptador genérico de chat: instrucciones + mensaje -> texto, tokens y latencia.

No sabe NADA de MiRob (ni de reclutadores, ni de chunks, ni del system prompt de
"asistente de Ariel"): eso vive en el dominio y se construirá en la Etapa 2.2.
Este módulo solo sabe hablar con el modelo y medir la llamada.

ESTADO: esqueleto. El resultado (ChatResult) y la firma están definidos; la
llamada real la completás vos en el lab 2.
"""

from dataclasses import dataclass

from openai import OpenAI

from app.foundry.config import FoundrySettings


# frozen=True: el resultado es inmutable, como un `record` de C#.
@dataclass(frozen=True)
class ChatResult:
    """Lo que devuelve una llamada al modelo de chat."""

    texto: str  # lo que respondió el modelo
    tokens_entrada: int  # tokens del prompt (instrucciones + mensaje)
    tokens_salida: int  # tokens generados
    tokens_total: int  # suma de ambos; es lo que se factura
    latencia_ms: float  # de ida y vuelta, medida del lado nuestro
    deployment: str  # con qué deployment se respondió


def generar(
    cliente: OpenAI,
    settings: FoundrySettings,
    instrucciones: str,
    mensaje: str,
) -> ChatResult:
    """Llama al modelo con la API Responses y devuelve texto, tokens y latencia.

    Datos verificados contra openai 2.54.0:
      - cliente.responses.create(model=..., instructions=..., input=...) acepta,
        entre otros, `max_output_tokens`, `temperature`, `store` y `stream`.
      - `model` recibe el nombre de TU DEPLOYMENT (settings.chat_deployment),
        igual que usan los labs de MS Learn con MODEL_DEPLOYMENT_NAME.
      - El objeto de respuesta tiene `.output_text` (el texto) y `.usage` con
        input_tokens, output_tokens y total_tokens. OJO: `.usage` puede ser None,
        así que hay que contemplarlo (usar 0 si falta).

    Pasos sugeridos:
      1. Anotar el tiempo de inicio con time.perf_counter().
      2. Llamar a cliente.responses.create(...) con el deployment, las
         instrucciones (rol del sistema) y el mensaje (input).
      3. Calcular la latencia en milisegundos con el tiempo de fin.
      4. Armar y devolver un ChatResult con los campos de arriba.
    """
    # TODO(Ariel): implementá los 4 pasos de arriba (lab 2).
    raise NotImplementedError("lab02: llamar a cliente.responses.create(...) y armar el ChatResult")
