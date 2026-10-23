"""Tests del filtro de stopwords en español y de strip_accents del TF-IDF (ver ADR 0001)."""

import unicodedata

import pandas as pd
import pytest
from scipy.sparse import csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer

from app.rag.retrieve import top_k
from app.rag.stopwords_es import STOPWORDS_ES

# --- La lista en sí ---------------------------------------------------------------


def test_la_lista_esta_en_minusculas_sin_acentos_y_sin_repetidos() -> None:
    # Tiene que estar sin acentos porque el vectorizer los quita ANTES de buscar en la lista.
    sin_acentos = unicodedata.normalize("NFKD", " ".join(STOPWORDS_ES))
    assert not any(unicodedata.combining(c) for c in sin_acentos)
    assert all(palabra == palabra.lower() for palabra in STOPWORDS_ES)
    assert len(STOPWORDS_ES) == len(set(STOPWORDS_ES))


# --- El vectorizer ----------------------------------------------------------------


def test_el_vocabulario_no_contiene_stopwords(indice: tuple[TfidfVectorizer, csr_matrix]) -> None:
    vectorizer, _ = indice

    palabras = set(vectorizer.vocabulary_)

    assert palabras.isdisjoint(STOPWORDS_ES)
    assert "idiomas" in palabras  # las palabras con contenido sí quedan


def test_con_y_sin_tildes_la_pregunta_da_el_mismo_resultado(
    corpus: pd.DataFrame, indice: tuple[TfidfVectorizer, csr_matrix]
) -> None:
    vectorizer, matriz = indice

    con = top_k("¿Cómo manejás el estrés?", vectorizer, matriz, corpus, k=1).iloc[0]
    sin = top_k("como manejas el estres", vectorizer, matriz, corpus, k=1).iloc[0]

    assert con["chunk_id"] == sin["chunk_id"]
    assert con["similarity"] == pytest.approx(sin["similarity"])


# --- El objetivo: las preguntas fuera de tema ya no pasan el umbral -----------------


@pytest.mark.parametrize(
    "pregunta",
    [
        "¿Cuál es la capital de Mongolia?",
        "¿Quién ganó el mundial?",
        "¿Qué hora es?",
        "¿Cuál es el mejor chocolate?",
    ],
)
def test_una_pregunta_fuera_de_tema_da_score_cero(
    pregunta: str, corpus: pd.DataFrame, indice: tuple[TfidfVectorizer, csr_matrix]
) -> None:
    vectorizer, matriz = indice

    # Antes de filtrar stopwords, "es", "la" o "cuál" hacían coincidir estas preguntas con
    # cualquier chunk (score ≈ 0,22) y el bot gastaba tokens del LLM para decir "no sé".
    assert top_k(pregunta, vectorizer, matriz, corpus, k=1).iloc[0]["similarity"] == 0.0


def test_una_pregunta_compuesta_solo_de_stopwords_no_rompe(
    corpus: pd.DataFrame, indice: tuple[TfidfVectorizer, csr_matrix]
) -> None:
    vectorizer, matriz = indice

    resultado = top_k("¿Y qué es eso de la?", vectorizer, matriz, corpus, k=3)

    assert resultado["similarity"].tolist() == [0.0, 0.0, 0.0]  # vector vacío: sin NaN ni error
