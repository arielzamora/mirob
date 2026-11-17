"""Retrieval semántico: embeddings + similitud coseno con numpy (ADR 0011).

Complementa al TF-IDF de retrieve.py sin reemplazarlo. Un embedding es un vector que representa el SIGNIFICADO de
un texto, así que "celular" y "teléfono" quedan cerca aunque no compartan ninguna palabra (lo que el léxico no logra).

Los vectores se normalizan UNA sola vez (largo 1). Con vectores de largo 1, el coseno es solo el producto punto,
y comparar una pregunta contra todos los chunks es una multiplicación de matriz por vector.

Este módulo NO conoce el SDK de Azure: recibe un `GeneradorEmbeddings` (ver app/foundry/tipos.py), igual que un
servicio de C# que recibe una interfaz por DI. Por eso se prueba sin red.
"""

import numpy as np
import pandas as pd

from app.foundry.tipos import GeneradorEmbeddings


def normalizar(vectores: np.ndarray) -> np.ndarray:
    """Deja cada fila (o el vector, si es 1D) con largo 1. Una fila de ceros queda en ceros, sin NaN."""
    vectores = np.asarray(vectores, dtype=np.float32)
    normas = np.linalg.norm(vectores, axis=-1, keepdims=True)
    # `where=` evita dividir por cero; `out=` deja esas filas en 0 (mismo criterio que cosine_similarities).
    return np.divide(vectores, normas, out=np.zeros_like(vectores), where=normas != 0)


def similitudes(consulta: np.ndarray, matriz: np.ndarray) -> np.ndarray:
    """Coseno entre la pregunta y cada chunk. Ambos YA normalizados: es un producto punto por fila."""
    return (matriz @ consulta).astype(float)


def top_k_de_puntajes(puntajes: np.ndarray, corpus: pd.DataFrame, k: int = 3) -> pd.DataFrame:
    """Los k chunks con mayor puntaje, con la columna `similarity` y orden descendente.

    Devuelve las mismas columnas que retrieve.top_k, así el resto del chat no distingue de qué retrieval vienen.
    """
    resultado = corpus.copy()  # .copy() para no pisar el corpus original al agregarle la columna
    resultado["similarity"] = puntajes
    return resultado.sort_values("similarity", ascending=False).head(k).reset_index(drop=True)


def top_k_semantico(consulta: np.ndarray, matriz: np.ndarray, corpus: pd.DataFrame, k: int = 3) -> pd.DataFrame:
    """Los k chunks más cercanos a la pregunta por significado (coseno de embeddings)."""
    return top_k_de_puntajes(similitudes(consulta, matriz), corpus, k)


def vectorizar_pregunta(pregunta: str, embeddings: GeneradorEmbeddings) -> tuple[np.ndarray, int]:
    """Embedding normalizado de la pregunta. Devuelve (vector, tokens gastados)."""
    resultado = embeddings.embeber([pregunta])
    return normalizar(resultado.vectores[0]), resultado.tokens
