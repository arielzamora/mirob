"""Tests del retrieval semántico (app/rag/semantico.py): coseno con numpy y vectorización, sin red."""

import numpy as np
import pandas as pd
import pytest

from app.rag.almacen_vectores import AlmacenVectores, construir_vectores
from app.rag.semantico import normalizar, similitudes, top_k_semantico, vectorizar_pregunta
from tests.conftest import EmbeddingsFalso


def test_normalizar_deja_cada_fila_con_largo_uno() -> None:
    matriz = normalizar(np.array([[3.0, 4.0], [0.0, 2.0]]))

    assert np.allclose(matriz, [[0.6, 0.8], [0.0, 1.0]])
    assert np.allclose(np.linalg.norm(matriz, axis=1), 1.0)


def test_normalizar_un_vector_de_ceros_queda_en_ceros_sin_nan() -> None:
    resultado = normalizar(np.zeros((2, 3)))

    assert not np.isnan(resultado).any()
    assert (resultado == 0).all()


def test_normalizar_acepta_un_vector_1d() -> None:
    assert np.allclose(normalizar(np.array([0.0, 5.0])), [0.0, 1.0])


def test_similitudes_es_el_coseno_cuando_los_vectores_estan_normalizados() -> None:
    matriz = normalizar(np.array([[1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]))
    consulta = normalizar(np.array([1.0, 0.0]))

    puntajes = similitudes(consulta, matriz)

    # Contra sí mismo 1, a 45 grados raíz(2)/2 y perpendicular 0: coseno exacto.
    assert np.allclose(puntajes, [1.0, np.sqrt(2) / 2, 0.0])


def test_top_k_devuelve_las_columnas_del_corpus_mas_similarity_en_orden_descendente() -> None:
    corpus = pd.DataFrame({"chunk_id": [0, 1, 2], "source": ["a.md"] * 3, "text": ["x", "y", "z"]})
    matriz = normalizar(np.array([[0.0, 1.0], [1.0, 1.0], [1.0, 0.0]]))

    resultado = top_k_semantico(normalizar(np.array([1.0, 0.1])), matriz, corpus, k=2)

    assert list(resultado.columns) == ["chunk_id", "source", "text", "similarity"]
    assert list(resultado["chunk_id"]) == [2, 1]  # el más cercano primero, y solo k filas
    assert resultado["similarity"].is_monotonic_decreasing
    assert list(resultado.index) == [0, 1]  # índice renumerado, igual que retrieve.top_k


def test_top_k_no_modifica_el_corpus_original() -> None:
    corpus = pd.DataFrame({"chunk_id": [0], "source": ["a.md"], "text": ["x"]})

    top_k_semantico(np.array([1.0]), np.array([[1.0]]), corpus)

    assert "similarity" not in corpus.columns


def test_construir_vectores_pide_todos_los_chunks_en_una_llamada_y_normaliza() -> None:
    falso = EmbeddingsFalso()
    corpus = pd.DataFrame({"chunk_id": [0, 1], "source": ["a.md", "a.md"], "text": ["hola mundo", "chau mundo cruel"]})

    vectorizacion = construir_vectores(corpus, falso, AlmacenVectores(None))

    assert falso.llamadas == [["hola mundo", "chau mundo cruel"]]  # una sola llamada para todo el corpus
    assert vectorizacion.matriz.shape == (2, EmbeddingsFalso.DIMENSIONES)
    assert np.allclose(np.linalg.norm(vectorizacion.matriz, axis=1), 1.0)
    assert vectorizacion.tokens == 5  # 2 + 3 palabras: el gasto se devuelve para registrarlo en el presupuesto


def test_vectorizar_pregunta_devuelve_un_vector_normalizado_y_sus_tokens() -> None:
    falso = EmbeddingsFalso()

    vector, tokens = vectorizar_pregunta("¿qué idiomas hablás?", falso)

    assert vector.shape == (EmbeddingsFalso.DIMENSIONES,)
    assert np.isclose(np.linalg.norm(vector), 1.0)
    assert tokens == 3


def test_un_sinonimo_acerca_la_pregunta_al_chunk_aunque_no_compartan_palabras() -> None:
    # Esto es lo que el TF-IDF no puede hacer: "lenguas" no aparece en el chunk, pero el modelo la trata como "idiomas".
    falso = EmbeddingsFalso(sinonimos={"lenguas": "idiomas"})
    corpus = pd.DataFrame(
        {"chunk_id": [0, 1], "source": ["a.md"] * 2, "text": ["## Idiomas español e inglés", "## Contacto por email"]}
    )
    matriz = construir_vectores(corpus, falso, AlmacenVectores(None)).matriz
    pregunta, _ = vectorizar_pregunta("lenguas", falso)

    resultado = top_k_semantico(pregunta, matriz, corpus, k=1)

    assert resultado.loc[0, "chunk_id"] == 0
    assert resultado.loc[0, "similarity"] > 0


def test_el_error_del_generador_se_propaga_para_que_buscar_lo_degrade() -> None:
    from app.foundry.tipos import FoundryError

    falso = EmbeddingsFalso(error=FoundryError("sin red"))

    with pytest.raises(FoundryError):
        vectorizar_pregunta("hola", falso)
