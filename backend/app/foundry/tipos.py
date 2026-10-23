"""Tipos compartidos de la capa Foundry, SIN importar ningún SDK de Azure ni openai.

Existe para que el dominio (app/rag, app/api) pueda hablar con "algo que genera
texto" sin conocer el SDK: depende de este Protocol, no de la implementación.
Es el equivalente a una interfaz `ITextGenerator` en C# que se inyecta por DI;
los tests le pasan un falso y la API real le pasa FoundryGenerador.
"""

from dataclasses import dataclass
from typing import Protocol

# Tope de tokens de SALIDA por respuesta (política de costos, FinOps). Una respuesta
# de ~250 tokens son tres o cuatro oraciones: alcanza para un chat y acota el gasto.
MAX_TOKENS_SALIDA = 250


class FoundryError(RuntimeError):
    """Falló la llamada al modelo (credenciales, red, cuota, etc.).

    El dominio solo conoce este tipo: los errores del SDK (AzureError, OpenAIError)
    se traducen dentro de app/foundry/, así nadie fuera de esa carpeta importa esas clases.
    """


class FoundryNoConfigurado(FoundryError):
    """Faltan variables de entorno de Foundry en el servidor (se responde 503, no 502)."""


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


class GeneradorTexto(Protocol):
    """Cualquier objeto con este método sirve (tipado estructural, como una interfaz de C#)."""

    def generar(self, instrucciones: str, mensaje: str, max_tokens_salida: int = MAX_TOKENS_SALIDA) -> ChatResult:
        """Genera texto o lanza FoundryError."""
        ...
