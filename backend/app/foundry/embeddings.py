"""FoundryEmbeddings: la implementación real de GeneradorEmbeddings (junto con generador.py, lo único que usa el SDK).

Convierte textos en vectores con el deployment de embeddings del proyecto. No sabe nada de tenants, chunks ni
presupuestos: eso vive en el dominio (app/rag/semantico.py) y en app/finops.py.

Usa el MISMO cliente OpenAI (y la misma credencial de Entra ID) que el chat: se lo pide a una función que recibe
por parámetro, así los tests le pasan un cliente falso y no hace falta Azure. En .NET sería un `Func<OpenAI>`
inyectado por constructor.
"""

import logging
import time
from collections.abc import Callable, Sequence

import numpy as np
from azure.core.exceptions import AzureError
from openai import OpenAI, OpenAIError

from app.foundry.config import FoundryConfigError, FoundrySettings, leer_lote_embeddings
from app.foundry.tipos import FoundryError, FoundryNoConfigurado, ResultadoEmbeddings

# Segundos máximos de espera por llamada (el mismo criterio que el chat: sin esto un cuelgue deja el pedido esperando).
TIMEOUT_SEGUNDOS = 30.0

logger = logging.getLogger(__name__)


def _detalle_seguro(error: Exception) -> str:
    """Lo que se puede loguear de un error de Azure sin riesgo: tipo, estado HTTP y código de error (no el mensaje)."""
    partes = [type(error).__name__]
    estado = getattr(error, "status_code", None)
    codigo = getattr(error, "code", None)
    if isinstance(estado, int):
        partes.append(f"estado={estado}")
    if isinstance(codigo, str) and codigo:
        partes.append(f"codigo={codigo[:60]}")
    return " ".join(partes)


class FoundryEmbeddings:
    """Genera embeddings con el deployment `FOUNDRY_EMBEDDINGS_DEPLOYMENT`, en lotes."""

    def __init__(
        self,
        obtener_cliente: Callable[[], tuple[OpenAI, FoundrySettings]],
        tamano_lote: int | None = None,
    ) -> None:
        self._obtener_cliente = obtener_cliente
        # None: se lee de MIROB_EMBEDDINGS_BATCH en el primer uso (así el arranque no depende del entorno).
        self._tamano_lote = tamano_lote

    def deployment(self) -> str:
        """El nombre del deployment de embeddings. Lanza FoundryNoConfigurado si no está definido (no llama a Azure)."""
        _, settings = self._obtener_cliente()
        if not settings.embeddings_deployment:
            raise FoundryNoConfigurado("Falta FOUNDRY_EMBEDDINGS_DEPLOYMENT: no hay deployment de embeddings.")
        return settings.embeddings_deployment

    def embeber(self, textos: Sequence[str]) -> ResultadoEmbeddings:
        """Un vector por texto, en el mismo orden. Lotes de `tamano_lote` textos, no una llamada por texto."""
        try:
            cliente, settings = self._obtener_cliente()
            deployment = settings.embeddings_deployment
            if not deployment:
                raise FoundryNoConfigurado("Falta FOUNDRY_EMBEDDINGS_DEPLOYMENT: no hay deployment de embeddings.")
            lote = self._tamano_lote or leer_lote_embeddings()
        except FoundryConfigError as error:
            raise FoundryNoConfigurado(str(error)) from error

        if not textos:
            return ResultadoEmbeddings(np.empty((0, 0), dtype=np.float32), 0, deployment, 0.0)

        filas: list[list[float]] = []
        tokens = 0
        inicio = time.perf_counter()
        try:
            for desde in range(0, len(textos), lote):
                respuesta = cliente.embeddings.create(
                    model=deployment,
                    input=list(textos[desde : desde + lote]),
                    timeout=TIMEOUT_SEGUNDOS,
                )
                # La API devuelve un `index` por vector: se ordena por él en vez de confiar en el orden de la lista.
                filas.extend(item.embedding for item in sorted(respuesta.data, key=lambda item: item.index))
                # `.usage` puede faltar: se cuenta 0 (igual que en chat.py).
                tokens += respuesta.usage.total_tokens if respuesta.usage else 0
        except FoundryError:
            raise
        except (AzureError, OpenAIError) as error:
            # Se loguea el tipo, el estado HTTP y el código de error de Azure (por ejemplo PermissionDenied), nunca los
            # textos ni el mensaje completo: podrían tener datos personales.
            logger.error("Falló la llamada a embeddings: %s", _detalle_seguro(error))
            raise FoundryError("No se pudieron calcular los embeddings.") from error
        latencia_ms = (time.perf_counter() - inicio) * 1000

        if len(filas) != len(textos):
            raise FoundryError("El modelo de embeddings devolvió una cantidad distinta de vectores.")
        return ResultadoEmbeddings(np.asarray(filas, dtype=np.float32), tokens, deployment, latencia_ms)
