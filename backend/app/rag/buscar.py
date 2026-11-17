"""Elige el retrieval (léxico, semántico o híbrido) y degrada al léxico si el pedido no se puede cumplir.

El retrieval léxico (retrieve.py) queda intacto y sigue siendo el default (ADR 0001). Los modos con embeddings
(ADR 0011) son opcionales y, si algo falla, el chat NO devuelve un error: responde con el léxico y avisa por qué
(`motivo_degradacion`). Así un problema con el deployment de embeddings nunca deja al bot mudo.

Este módulo tampoco conoce el SDK de Azure: recibe un `GeneradorEmbeddings` (en producción, el decorado con
presupuesto; en los tests, uno falso).
"""

import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from app.finops import PresupuestoAgotado
from app.foundry.tipos import FoundryError, FoundryNoConfigurado, GeneradorEmbeddings
from app.rag.almacen_vectores import NOMBRE_CARPETA, AlmacenVectores, construir_vectores
from app.rag.hibrido import top_k_hibrido
from app.rag.retrieve import puntajes_lexicos, top_k
from app.rag.semantico import similitudes, top_k_semantico, vectorizar_pregunta
from app.reindex import IndexState
from app.tenants import TenantConfig

logger = logging.getLogger(__name__)

# Los valores que acepta el campo `retrieval` del chat. Es el mismo orden que mostrará el toggle del front.
Retrieval = Literal["lexico", "semantico", "hibrido"]

# Por qué un pedido de retrieval con embeddings terminó resuelto con el léxico. Son valores estables: los ve el front.
MOTIVO_NO_CALIBRADO = "modo_no_calibrado"  # el tenant no tiene umbral para ese modo en su tenant.json
MOTIVO_SIN_EMBEDDINGS = "embeddings_no_configurados"  # no hay deployment de embeddings
MOTIVO_FALLO = "embeddings_no_disponibles"  # Azure respondió con un error
MOTIVO_PRESUPUESTO = "presupuesto_agotado"  # se alcanzó el tope diario de tokens


@dataclass(frozen=True)
class Busqueda:
    """Lo que devuelve `buscar`: los chunks, con qué retrieval se obtuvieron y cuánto costó."""

    resultado: pd.DataFrame  # columnas del corpus + `similarity`, ordenado descendente
    retrieval_usado: str  # el que REALMENTE se aplicó (puede diferir del pedido)
    umbral: float  # el umbral que corresponde a ese retrieval (cada modo tiene su escala)
    tokens_embeddings: int = 0  # tokens de embeddings gastados en este pedido (incluye armar los vectores la 1ª vez)
    motivo_degradacion: str | None = None  # None si se usó lo pedido


@dataclass(frozen=True)
class _Embebido:
    """Lo necesario de los embeddings para puntuar: los vectores del corpus y la pregunta, y lo que costaron."""

    puntajes: np.ndarray  # similitud semántica de la pregunta con cada chunk
    consulta: np.ndarray
    vectores: np.ndarray
    tokens: int


@dataclass(frozen=True)
class _Fallo:
    """Los embeddings no se pudieron usar: el motivo y los tokens que se alcanzaron a gastar antes de fallar."""

    motivo: str
    tokens: int


def buscar(
    retrieval: Retrieval,
    pregunta: str,
    tenant: TenantConfig,
    indice: IndexState,
    embeddings: GeneradorEmbeddings | None,
    k: int,
) -> Busqueda:
    """Recupera los k chunks más relevantes con el retrieval pedido, o con el léxico si no se puede."""
    if retrieval == "lexico":
        return _lexico(pregunta, tenant, indice, k)

    # Cada modo con embeddings tiene su umbral en su propia escala. Sin él, el modo no está calibrado: no se ofrece.
    umbral = _umbral_del_modo(retrieval, tenant)
    if umbral is None:
        return _lexico(pregunta, tenant, indice, k, motivo=MOTIVO_NO_CALIBRADO)
    if embeddings is None:
        return _lexico(pregunta, tenant, indice, k, motivo=MOTIVO_SIN_EMBEDDINGS)

    embebido = _embeber(pregunta, tenant, indice, embeddings)
    if isinstance(embebido, _Fallo):
        return _lexico(pregunta, tenant, indice, k, embebido.motivo, embebido.tokens)

    if retrieval == "semantico":
        resultado = top_k_semantico(embebido.consulta, embebido.vectores, indice.corpus, k=k)
    else:
        # Híbrido: el puntaje léxico de cada chunk + el semántico, normalizados y ponderados (app/rag/hibrido.py).
        lexicos = puntajes_lexicos(pregunta, indice.vectorizer, indice.matrix)
        resultado = top_k_hibrido(lexicos, embebido.puntajes, tenant.retrieval.hibrido, indice.corpus, k=k)
    return Busqueda(resultado, retrieval, umbral, tokens_embeddings=embebido.tokens)


def _umbral_del_modo(retrieval: Retrieval, tenant: TenantConfig) -> float | None:
    if retrieval == "semantico":
        return tenant.retrieval.umbral_semantico
    return tenant.retrieval.hibrido.umbral if tenant.retrieval.hibrido else None


def _lexico(
    pregunta: str, tenant: TenantConfig, indice: IndexState, k: int, motivo: str | None = None, tokens: int = 0
) -> Busqueda:
    resultado = top_k(pregunta, indice.vectorizer, indice.matrix, indice.corpus, k=k)
    return Busqueda(resultado, "lexico", tenant.umbral_similitud, tokens_embeddings=tokens, motivo_degradacion=motivo)


def _embeber(
    pregunta: str, tenant: TenantConfig, indice: IndexState, embeddings: GeneradorEmbeddings
) -> _Embebido | _Fallo:
    """Asegura los vectores del corpus, vectoriza la pregunta y puntúa. Devuelve un _Fallo si algo no se puede."""
    # Los vectores de cada tenant se guardan en su propia carpeta (no en la de otro). Los tenants de solo lectura
    # (demo, `usa_sample`) no guardan nada: viven en memoria.
    almacen = AlmacenVectores(None if tenant.usa_sample else indice.carpeta / NOMBRE_CARPETA)
    # Los tokens ya gastados cuentan aunque después algo falle: Azure los facturó igual.
    tokens = 0
    try:
        # El candado evita que dos consultas simultáneas armen los vectores del corpus dos veces.
        with indice.candado_vectores:
            if indice.vectores is None:
                # Tras un reinicio o un cambio de contenido: lee lo guardado y solo vectoriza lo nuevo o modificado.
                armados = construir_vectores(indice.corpus, embeddings, almacen)
                indice.vectores = armados.matriz
                tokens += armados.tokens
                logger.info(
                    "vectores tenant=%s nuevos=%d reutilizados=%d tokens=%d",
                    tenant.tenant_id,
                    armados.nuevos,
                    armados.reutilizados,
                    armados.tokens,
                )
            # Una referencia local: otro pedido podría descartar `indice.vectores` mientras este sigue.
            vectores = indice.vectores
        consulta, gastados = vectorizar_pregunta(pregunta, embeddings)
        tokens += gastados
    except PresupuestoAgotado:
        return _Fallo(MOTIVO_PRESUPUESTO, tokens)
    except FoundryNoConfigurado:
        return _Fallo(MOTIVO_SIN_EMBEDDINGS, tokens)
    except FoundryError:
        # Solo el tipo del error: ya se registró en la capa foundry y el mensaje podría traer datos.
        logger.warning("Retrieval con embeddings no disponible: se responde con el léxico (tenant=%s).", tenant.tenant_id)
        return _Fallo(MOTIVO_FALLO, tokens)

    if consulta.shape[0] != vectores.shape[1]:
        # El modelo cambió de dimensiones sin cambiar de nombre de deployment: los vectores guardados ya no sirven.
        # Se descartan (memoria y disco) para que el próximo pedido los recalcule, y este responde con el léxico.
        with indice.candado_vectores:
            indice.vectores = None
        almacen.invalidar()
        logger.warning("Las dimensiones de los embeddings cambiaron: se recalculan (tenant=%s).", tenant.tenant_id)
        return _Fallo(MOTIVO_FALLO, tokens)

    return _Embebido(similitudes(consulta, vectores), consulta, vectores, tokens)
