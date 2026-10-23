"""Vectorización TF-IDF del corpus de chunks."""

import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer


def fit_vectorizer(corpus: pd.DataFrame) -> tuple[TfidfVectorizer, csr_matrix]:
    """Ajusta un TfidfVectorizer sobre la columna `text` del corpus.

    Devuelve el vectorizer ya ajustado (para vectorizar preguntas después)
    junto con la matriz TF-IDF de todos los chunks.
    """
    # TfidfVectorizer sin argumentos usa su configuración default:
    # - tokeniza separando por palabras (regex interno, minúsculas)
    # - NO filtra stopwords (stop_words=None) — a propósito, porque la
    #   lista built-in de sklearn es solo en inglés y nuestro contenido
    #   está en español; pasarla igual filtraría mal.
    # - pondera cada término por TF-IDF: frecuencia en el chunk (TF) x
    #   qué tan poco común es ese término en TODO el corpus (IDF). Una
    #   palabra que aparece en un solo chunk pesa más que una que aparece
    #   en todos (esas últimas casi no ayudan a diferenciar chunks).
    vectorizer = TfidfVectorizer()

    # fit_transform hace dos cosas en un solo paso:
    #   1) fit: aprende el vocabulario del corpus (qué palabras existen,
    #      y su IDF) y lo guarda adentro del objeto `vectorizer`.
    #   2) transform: usa ese vocabulario para convertir cada chunk de
    #      texto en un vector numérico de pesos TF-IDF.
    # `matrix` es una matriz sparse (fila = chunk, columna = palabra del
    # vocabulario). Es sparse porque la gran mayoría de los chunks NO
    # contienen la gran mayoría de las palabras del vocabulario total —
    # guardar todos esos ceros en una matriz densa desperdiciaría memoria.
    matrix = vectorizer.fit_transform(corpus["text"])

    # Devolvemos el vectorizer YA AJUSTADO porque retrieve.py lo va a
    # necesitar para transformar la pregunta con el MISMO vocabulario
    # (si se creara un vectorizer nuevo para la pregunta, sus columnas
    # no coincidirían con las de `matrix` y la comparación no tendría sentido).
    return vectorizer, matrix
