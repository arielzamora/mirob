"""Retrieval de chunks por similitud coseno (TF-IDF + numpy, calculado a mano)."""

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer


def cosine_similarities(query_vector: csr_matrix, corpus_matrix: csr_matrix) -> np.ndarray:
    """Similitud coseno entre el vector de la pregunta y cada fila del corpus.

    Se calcula a mano (dot product / (norma_a * norma_b)) en vez de usar
    sklearn.metrics.pairwise.cosine_similarity, que es el punto de esta etapa.
    """
    # query_vector es 1 fila x N columnas (N = tamaño del vocabulario TF-IDF),
    # en formato sparse. .todense() lo pasa a matriz densa (2D), y
    # np.asarray(...).ravel() lo aplana a un vector 1D de numpy.
    # Solo densificamos LA PREGUNTA (un vector chico) — el corpus completo
    # se deja sparse por eficiencia, ver abajo.
    query_dense = np.asarray(query_vector.todense()).ravel()

    # --- Numerador del coseno: producto punto entre cada chunk y la pregunta ---
    # corpus_matrix es sparse (chunks x vocabulario). Multiplicar una matriz
    # sparse por un vector denso 1D con .dot() devuelve directamente un
    # array 1D de numpy con un dot product por fila (uno por chunk).
    # Matemáticamente es lo mismo que sum(chunk[i] * query[i]) para cada
    # palabra i del vocabulario, para cada chunk.
    dot_products = corpus_matrix.dot(query_dense)

    # --- Norma de cada chunk (para el denominador) ---
    # La norma euclídea de un vector v es sqrt(suma de cada componente al
    # cuadrado). corpus_matrix.multiply(corpus_matrix) eleva al cuadrado
    # cada elemento SIN salir del formato sparse (evita crear una matriz
    # densa de chunks x vocabulario, que podría ser gigante).
    # .sum(axis=1) suma cada fila (por chunk) y devuelve una matriz sparse
    # de una sola columna; .A la convierte a array denso normal de numpy,
    # y .ravel() la aplana de 2D a 1D.
    corpus_norms = np.sqrt(corpus_matrix.multiply(corpus_matrix).sum(axis=1)).A.ravel()

    # Norma de la pregunta: acá sí es un vector 1D normal, así que se usa
    # directamente la función de numpy.
    query_norm = np.linalg.norm(query_dense)

    # --- Coseno = dot_product / (norma_chunk * norma_query) ---
    denom = corpus_norms * query_norm

    # np.divide con `where=` evita la división por cero: si denom es 0 para
    # algún chunk (chunk vacío o sin ninguna palabra del vocabulario de la
    # pregunta), ese resultado queda en 0 en vez de tirar un warning/NaN.
    # `out=` prellena el array de salida con ceros para esos casos.
    similarities = np.divide(
        dot_products,
        denom,
        out=np.zeros_like(dot_products, dtype=float),
        where=denom != 0,
    )
    return similarities


def top_k(
    question: str,
    vectorizer: TfidfVectorizer,
    corpus_matrix: csr_matrix,
    corpus: pd.DataFrame,
    k: int = 3,
) -> pd.DataFrame:
    """Vectoriza la pregunta y devuelve los k chunks más similares del corpus.

    El resultado incluye la columna `similarity` y está ordenado descendente.
    """
    # OJO: .transform(), NO .fit_transform(). La pregunta se proyecta sobre
    # el vocabulario que YA aprendió el vectorizer con el corpus (fit_vectorizer
    # en embeddings.py). Si acá se hiciera fit de nuevo, el vocabulario
    # cambiaría y las columnas de query_vector no coincidirían con las de
    # corpus_matrix — la comparación dejaría de tener sentido.
    # .transform() espera una LISTA de textos, por eso `[question]` entre
    # corchetes aunque sea una sola pregunta.
    query_vector = vectorizer.transform([question])
    similarities = cosine_similarities(query_vector, corpus_matrix)

    # .copy() para no pisar el DataFrame original del corpus al agregarle
    # la columna nueva (evita el warning de pandas por mutar una vista).
    result = corpus.copy()
    result["similarity"] = similarities  # numpy array del mismo largo que las filas

    # sort_values ordena de mayor a menor similitud, .head(k) se queda con
    # los primeros k, y .reset_index(drop=True) renumera el índice de 0 a
    # k-1 (si no, quedarían los índices originales salteados y desordenados).
    return result.sort_values("similarity", ascending=False).head(k).reset_index(drop=True)
