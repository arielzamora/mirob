"""Vectorización TF-IDF del corpus de chunks."""

import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer

from app.rag.stopwords_es import STOPWORDS_ES


def fit_vectorizer(corpus: pd.DataFrame) -> tuple[TfidfVectorizer, csr_matrix]:
    """Ajusta un TfidfVectorizer sobre la columna `text` del corpus.

    Devuelve el vectorizer ya ajustado (para vectorizar preguntas después)
    junto con la matriz TF-IDF de todos los chunks.
    """
    # Configuración del TfidfVectorizer:
    # - tokeniza separando por palabras (regex interno, minúsculas)
    # - strip_accents="unicode": "cómo" y "como", "está" y "esta" valen lo
    #   mismo (las preguntas se escriben con y sin tildes).
    # - stop_words=STOPWORDS_ES: descarta palabras vacías como "el", "qué" o
    #   "cuál". La lista built-in de sklearn es solo en inglés, por eso usamos
    #   una propia en español (app/rag/stopwords_es.py). Sin este filtro, una
    #   pregunta fuera de tema compartía palabras vacías con cualquier chunk y
    #   pasaba el umbral del bot (ver ADR 0001).
    # - pondera cada término por TF-IDF: frecuencia en el chunk (TF) x
    #   qué tan poco común es ese término en TODO el corpus (IDF). Una
    #   palabra que aparece en un solo chunk pesa más que una que aparece
    #   en todos (esas últimas casi no ayudan a diferenciar chunks).
    vectorizer = TfidfVectorizer(strip_accents="unicode", stop_words=list(STOPWORDS_ES))

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
