"""Tests de retrieve.py: similitud coseno a mano y top-k sobre el perfil ficticio.

TF-IDF + coseno es determinístico (sin azar ni red), así que los tests pueden
afirmar resultados EXACTOS: "esta pregunta debe traer este chunk como top-1".
"""

import numpy as np
import pandas as pd
import pytest
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.rag.humanize import UMBRAL_BAJA_CONFIANZA
from app.rag.retrieve import cosine_similarities, top_k

# --- cosine_similarities (matemática pura, con matrices chicas armadas a mano) ---


def test_coseno_de_vectores_identicos_es_uno_y_ortogonales_es_cero() -> None:
    # Dos chunks sobre un vocabulario de 2 palabras: [1, 0] y [0, 1].
    corpus_matrix = csr_matrix(np.array([[1.0, 0.0], [0.0, 1.0]]))
    query = csr_matrix(np.array([[1.0, 0.0]]))

    resultado = cosine_similarities(query, corpus_matrix)

    # allclose compara floats con tolerancia (como Assert.Equal con precisión en C#).
    assert np.allclose(resultado, [1.0, 0.0])


def test_coseno_ignora_la_magnitud_del_vector() -> None:
    # [2, 0] y [1, 0] apuntan al mismo lado: el coseno solo mide ángulo, no largo.
    corpus_matrix = csr_matrix(np.array([[2.0, 0.0]]))
    query = csr_matrix(np.array([[1.0, 0.0]]))

    assert np.allclose(cosine_similarities(query, corpus_matrix), [1.0])


def test_coseno_con_pregunta_sin_palabras_conocidas_devuelve_ceros_sin_nan() -> None:
    corpus_matrix = csr_matrix(np.array([[1.0, 0.0], [0.0, 1.0]]))
    query = csr_matrix(np.zeros((1, 2)))  # ninguna palabra de la pregunta está en el vocabulario

    resultado = cosine_similarities(query, corpus_matrix)

    assert np.array_equal(resultado, [0.0, 0.0])
    assert not np.isnan(resultado).any()  # sin división por cero


def test_coseno_a_mano_coincide_con_el_de_sklearn(
    corpus: pd.DataFrame, indice: tuple[TfidfVectorizer, csr_matrix]
) -> None:
    vectorizer, matriz = indice
    query = vectorizer.transform(["¿Qué idiomas hablás?"])

    a_mano = cosine_similarities(query, matriz)
    de_sklearn = cosine_similarity(query, matriz).ravel()

    # Es el punto de esta etapa: la versión manual debe dar lo mismo que la librería.
    assert np.allclose(a_mano, de_sklearn)


# --- top_k: asserts exactos de top-1 sobre el perfil ficticio ---

# (pregunta, archivo esperado, header esperado). Es el equivalente a
# [Theory] + [InlineData(...)] de xUnit: el mismo test corre una vez por fila.
CASOS_TOP1 = [
    ("¿Qué certificaciones tiene Camila?", "tecnica.md", "## Certificaciones"),
    ("¿Dónde trabaja actualmente?", "tecnica.md", "## Experiencia actual — Acme Robotics"),
    ("¿Cuál fue su experiencia anterior?", "tecnica.md", "## Experiencia previa — Globex Logística"),
    ("¿Qué tecnologías de backend usa?", "tecnica.md", "## Stack técnico — Backend"),
    (
        "¿Qué experiencia tiene con infraestructura y despliegues?",
        "tecnica.md",
        "## Stack técnico — Infraestructura",
    ),
    ("¿Qué idiomas hablás?", "personal.md", "## Idiomas"),
    ("¿Tenés mascotas?", "personal.md", "## Mascotas"),
    ("¿Practicás algún deporte?", "personal.md", "## Deportes"),
    ("¿Qué hacés en tu tiempo libre?", "personal.md", "## Hobbies y pasatiempos"),
    ("¿Cómo manejás el estrés?", "habilidades-blandas.md", "## Manejo del estrés"),
    ("¿Para qué sirve MiRob?", "objetivo.txt", None),  # los .txt no tienen header
]


@pytest.mark.parametrize(("pregunta", "archivo", "header"), CASOS_TOP1)
def test_top1_es_el_chunk_esperado(
    pregunta: str,
    archivo: str,
    header: str | None,
    corpus: pd.DataFrame,
    indice: tuple[TfidfVectorizer, csr_matrix],
) -> None:
    vectorizer, matriz = indice

    top1 = top_k(pregunta, vectorizer, matriz, corpus, k=1).iloc[0]

    assert top1["source"] == archivo
    if header is not None:
        # La primera línea del chunk es el header "## ..." (ingest lo conserva).
        assert top1["text"].splitlines()[0] == header
    assert top1["similarity"] > UMBRAL_BAJA_CONFIANZA  # una respuesta "con confianza"


def test_top_k_devuelve_k_filas_ordenadas_de_mayor_a_menor(
    corpus: pd.DataFrame, indice: tuple[TfidfVectorizer, csr_matrix]
) -> None:
    vectorizer, matriz = indice

    resultado = top_k("¿Qué tecnologías usa?", vectorizer, matriz, corpus, k=3)

    assert len(resultado) == 3
    similitudes = resultado["similarity"].tolist()
    assert similitudes == sorted(similitudes, reverse=True)
    # reset_index(drop=True): el índice del resultado vuelve a ser 0, 1, 2.
    assert resultado.index.tolist() == [0, 1, 2]


def test_top_k_no_modifica_el_corpus_original(
    corpus: pd.DataFrame, indice: tuple[TfidfVectorizer, csr_matrix]
) -> None:
    vectorizer, matriz = indice

    top_k("¿Tenés mascotas?", vectorizer, matriz, corpus, k=1)

    # top_k agrega la columna `similarity` sobre una COPIA, no sobre el original.
    assert "similarity" not in corpus.columns


def test_pregunta_fuera_de_tema_queda_bajo_el_umbral_de_confianza(
    corpus: pd.DataFrame, indice: tuple[TfidfVectorizer, csr_matrix]
) -> None:
    vectorizer, matriz = indice

    top1 = top_k("¿Cuál es la capital de Mongolia?", vectorizer, matriz, corpus, k=1).iloc[0]

    # Nada en el perfil habla de esto: el score tiene que quedar por debajo del
    # umbral que humanize.py usa para decir "no estoy seguro". Esta es la base
    # de la política de "no tengo esa información" de la Etapa 2.
    assert top1["similarity"] < UMBRAL_BAJA_CONFIANZA
