"""Tests del retrieval híbrido (app/rag/hibrido.py y el modo "hibrido" de buscar), sin red: EmbeddingsFalso."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import app.reindex as reindex_module
from app.finops import EstadoLimites, GeneradorEmbeddingsConPresupuesto, Limites
from app.foundry.tipos import FoundryError
from app.rag.buscar import MOTIVO_FALLO, MOTIVO_PRESUPUESTO, buscar
from app.rag.hibrido import combinar, normalizar_puntaje, top_k_hibrido
from app.rag.retrieve import puntajes_lexicos
from app.rag.semantico import similitudes, vectorizar_pregunta
from app.tenants import ConfigHibrido, ConfigRetrieval, TenantConfig, obtener_tenant
from tests.conftest import TENANT_DEMO, EmbeddingsFalso

# Bordes de ejemplo: el léxico va de 0 a 0,6 y el semántico de 0,2 (ruido) a 0,7.
CONFIG = ConfigHibrido(
    umbral=0.4, peso_semantico=0.5, piso_lexico=0.0, techo_lexico=0.6, piso_semantico=0.2, techo_semantico=0.7
)


def _con_peso(peso: float) -> ConfigHibrido:
    return replace(CONFIG, peso_semantico=peso)


# --- Normalización y combinación ----------------------------------------------------------------


def test_normalizar_lleva_el_piso_a_cero_y_el_techo_a_uno_y_recorta_lo_de_afuera() -> None:
    resultado = normalizar_puntaje(np.array([-0.1, 0.2, 0.45, 0.7, 0.9]), piso=0.2, techo=0.7)

    assert np.allclose(resultado, [0.0, 0.0, 0.5, 1.0, 1.0])


def test_peso_cero_es_solo_lexico_y_peso_uno_es_solo_semantico() -> None:
    lexicos = np.array([0.3, 0.0])
    semanticos = np.array([0.2, 0.7])

    assert np.allclose(combinar(lexicos, semanticos, _con_peso(0.0)), [0.5, 0.0])
    assert np.allclose(combinar(lexicos, semanticos, _con_peso(1.0)), [0.0, 1.0])


def test_combina_con_el_peso_configurado() -> None:
    # léxico normalizado 0,5 y semántico normalizado 1,0: 0,7·1,0 + 0,3·0,5 = 0,85
    resultado = combinar(np.array([0.3]), np.array([0.7]), _con_peso(0.7))

    assert np.allclose(resultado, [0.85])


def test_el_ruido_del_semantico_no_suma_porque_el_piso_lo_lleva_a_cero() -> None:
    # Sin normalizar, un texto sin relación tendría ~0,2 de semántico y ya "pesaría". Con el piso en 0,2, vale 0.
    sin_relacion = combinar(np.array([0.0]), np.array([0.2]), CONFIG)

    assert np.allclose(sin_relacion, [0.0])


def test_la_palabra_exacta_desempata_cuando_el_semantico_se_confunde() -> None:
    lexicos = np.array([0.6, 0.0, 0.0])  # solo el chunk 0 contiene la sigla que preguntaron
    semanticos = np.array([0.3, 0.5, 0.2])  # el modelo prefiere el chunk 1
    corpus = pd.DataFrame({"chunk_id": [0, 1, 2], "source": ["a.md"] * 3, "text": ["x", "y", "z"]})

    solo_semantico = top_k_hibrido(lexicos, semanticos, _con_peso(1.0), corpus, k=1)
    hibrido = top_k_hibrido(lexicos, semanticos, CONFIG, corpus, k=1)

    assert solo_semantico.loc[0, "chunk_id"] == 1
    assert hibrido.loc[0, "chunk_id"] == 0  # 0,6 contra 0,3: la coincidencia exacta gana


def test_top_k_hibrido_devuelve_las_columnas_del_corpus_mas_similarity_en_orden() -> None:
    corpus = pd.DataFrame({"chunk_id": [0, 1, 2], "source": ["a.md"] * 3, "text": ["x", "y", "z"]})

    resultado = top_k_hibrido(np.array([0.0, 0.6, 0.3]), np.array([0.2, 0.7, 0.45]), CONFIG, corpus, k=2)

    assert list(resultado.columns) == ["chunk_id", "source", "text", "similarity"]
    assert list(resultado["chunk_id"]) == [1, 2]
    assert resultado["similarity"].is_monotonic_decreasing
    assert "similarity" not in corpus.columns  # no modifica el original


# --- buscar(..., "hibrido") --------------------------------------------------------------------------


@pytest.fixture
def estado_demo(monkeypatch: pytest.MonkeyPatch, sample_dir: Path):
    monkeypatch.setattr(reindex_module, "_states", {})
    return reindex_module.reindex(TENANT_DEMO, sample_dir)


@pytest.fixture
def tenant_hibrido() -> TenantConfig:
    config = ConfigHibrido(
        umbral=0.3, peso_semantico=0.7, piso_lexico=0.0, techo_lexico=0.5, piso_semantico=0.05, techo_semantico=0.4
    )
    return replace(obtener_tenant(TENANT_DEMO), retrieval=ConfigRetrieval(hibrido=config))


def _embeddings() -> EmbeddingsFalso:
    return EmbeddingsFalso(sinonimos={"lenguas": "idiomas"})


def test_hibrido_encuentra_por_significado_y_usa_su_propio_umbral(estado_demo, tenant_hibrido: TenantConfig) -> None:
    busqueda = buscar("hibrido", "lenguas", tenant_hibrido, estado_demo, _embeddings(), k=3)

    assert busqueda.retrieval_usado == "hibrido"
    assert busqueda.motivo_degradacion is None
    assert busqueda.resultado.loc[0, "text"].startswith("## Idiomas")
    assert busqueda.umbral == 0.3  # el del híbrido, en su escala normalizada
    assert busqueda.tokens_embeddings > 0


def test_el_puntaje_hibrido_es_la_formula_sobre_los_dos_puntajes_reales(estado_demo, tenant_hibrido: TenantConfig) -> None:
    falso = _embeddings()
    pregunta = "¿Qué idiomas hablás?"

    busqueda = buscar("hibrido", pregunta, tenant_hibrido, estado_demo, falso, k=3)

    lexicos = puntajes_lexicos(pregunta, estado_demo.vectorizer, estado_demo.matrix)
    consulta, _ = vectorizar_pregunta(pregunta, falso)
    esperado = combinar(lexicos, similitudes(consulta, estado_demo.vectores), tenant_hibrido.retrieval.hibrido)
    assert busqueda.resultado.loc[0, "similarity"] == pytest.approx(esperado.max())
    assert busqueda.resultado.loc[0, "chunk_id"] == int(np.argmax(esperado))


def test_una_pregunta_fuera_de_tema_queda_bajo_el_umbral_del_hibrido(estado_demo, tenant_hibrido: TenantConfig) -> None:
    busqueda = buscar("hibrido", "xyzzy plugh", tenant_hibrido, estado_demo, _embeddings(), k=3)

    assert busqueda.retrieval_usado == "hibrido"
    assert busqueda.resultado.loc[0, "similarity"] < busqueda.umbral


def test_el_hibrido_y_el_semantico_comparten_los_vectores_del_corpus(estado_demo, tenant_hibrido: TenantConfig) -> None:
    falso = _embeddings()
    tenant = replace(
        tenant_hibrido, retrieval=ConfigRetrieval(umbral_semantico=0.1, hibrido=tenant_hibrido.retrieval.hibrido)
    )

    buscar("semantico", "lenguas", tenant, estado_demo, falso, k=3)
    buscar("hibrido", "lenguas", tenant, estado_demo, falso, k=3)

    # El corpus se vectoriza una sola vez (1ª llamada); después cada modo solo vectoriza su pregunta.
    assert [len(llamada) for llamada in falso.llamadas] == [len(estado_demo.corpus), 1, 1]


def test_si_los_embeddings_fallan_el_hibrido_responde_con_lexico(estado_demo, tenant_hibrido: TenantConfig) -> None:
    falso = EmbeddingsFalso(error=FoundryError("sin red"))

    busqueda = buscar("hibrido", "¿Qué idiomas hablás?", tenant_hibrido, estado_demo, falso, k=3)

    assert busqueda.retrieval_usado == "lexico"
    assert busqueda.motivo_degradacion == MOTIVO_FALLO
    assert busqueda.umbral == tenant_hibrido.umbral_similitud  # vuelve al umbral léxico, no al del híbrido


def test_con_el_tope_diario_agotado_el_hibrido_no_llama_a_azure(estado_demo, tenant_hibrido: TenantConfig) -> None:
    falso = _embeddings()
    estado = EstadoLimites(Limites(tope_diario_global=5))
    estado.presupuesto.registrar(TENANT_DEMO, 5)
    limitado = GeneradorEmbeddingsConPresupuesto(falso, estado, tenant_hibrido)

    busqueda = buscar("hibrido", "lenguas", tenant_hibrido, estado_demo, limitado, k=3)

    assert busqueda.retrieval_usado == "lexico"
    assert busqueda.motivo_degradacion == MOTIVO_PRESUPUESTO
    assert falso.llamadas == []
