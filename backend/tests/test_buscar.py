"""Tests de app/rag/buscar.py: qué retrieval se aplica y cuándo se degrada al léxico. Sin red (EmbeddingsFalso).

Usan el tenant "demo" (perfil ficticio) con un umbral semántico agregado en memoria: el tenant.json versionado de
demo no lo tiene, y estos tests no deben depender de ninguna calibración real.
"""

from dataclasses import replace
from pathlib import Path

import pytest

import app.reindex as reindex_module
from app.finops import EstadoLimites, GeneradorEmbeddingsConPresupuesto, Limites
from app.foundry.tipos import FoundryError, FoundryNoConfigurado
from app.rag.buscar import (
    MOTIVO_FALLO,
    MOTIVO_NO_CALIBRADO,
    MOTIVO_PRESUPUESTO,
    MOTIVO_SIN_EMBEDDINGS,
    buscar,
)
from app.rag.retrieve import top_k
from app.reindex import IndexState
from app.tenants import ConfigRetrieval, TenantConfig, obtener_tenant
from tests.conftest import TENANT_DEMO, EmbeddingsFalso

UMBRAL_SEMANTICO = 0.05
PREGUNTA_SINONIMO = "lenguas"  # no aparece en el contenido; el falso la trata como "idiomas"


@pytest.fixture
def estado_demo(monkeypatch: pytest.MonkeyPatch, sample_dir: Path) -> IndexState:
    monkeypatch.setattr(reindex_module, "_states", {})
    return reindex_module.reindex(TENANT_DEMO, sample_dir)


@pytest.fixture
def tenant_calibrado() -> TenantConfig:
    return replace(obtener_tenant(TENANT_DEMO), retrieval=ConfigRetrieval(umbral_semantico=UMBRAL_SEMANTICO))


def _embeddings() -> EmbeddingsFalso:
    return EmbeddingsFalso(sinonimos={"lenguas": "idiomas"})


# --- Léxico -------------------------------------------------------------------


def test_lexico_es_identico_a_top_k_y_no_toca_los_embeddings(estado_demo: IndexState, tenant_calibrado: TenantConfig) -> None:
    falso = _embeddings()

    busqueda = buscar("lexico", "¿Qué idiomas hablás?", tenant_calibrado, estado_demo, falso, k=3)

    esperado = top_k("¿Qué idiomas hablás?", estado_demo.vectorizer, estado_demo.matrix, estado_demo.corpus, k=3)
    assert busqueda.resultado.equals(esperado)
    assert busqueda.retrieval_usado == "lexico"
    assert busqueda.umbral == tenant_calibrado.umbral_similitud
    assert busqueda.tokens_embeddings == 0
    assert busqueda.motivo_degradacion is None
    assert falso.llamadas == []


# --- Semántico ---------------------------------------------------------------------


def test_semantico_encuentra_por_significado_lo_que_el_lexico_no(estado_demo: IndexState, tenant_calibrado: TenantConfig) -> None:
    lexico = buscar("lexico", PREGUNTA_SINONIMO, tenant_calibrado, estado_demo, None, k=3)
    semantico = buscar("semantico", PREGUNTA_SINONIMO, tenant_calibrado, estado_demo, _embeddings(), k=3)

    assert lexico.resultado.loc[0, "similarity"] == 0  # sin palabras en común no hay coincidencia
    assert semantico.retrieval_usado == "semantico"
    assert semantico.resultado.loc[0, "text"].startswith("## Idiomas")
    assert semantico.resultado.loc[0, "similarity"] > UMBRAL_SEMANTICO
    assert semantico.umbral == UMBRAL_SEMANTICO  # el umbral del modo, no el del léxico
    assert semantico.motivo_degradacion is None


def test_los_vectores_del_corpus_se_calculan_una_sola_vez(estado_demo: IndexState, tenant_calibrado: TenantConfig) -> None:
    falso = _embeddings()

    primera = buscar("semantico", PREGUNTA_SINONIMO, tenant_calibrado, estado_demo, falso, k=3)
    segunda = buscar("semantico", PREGUNTA_SINONIMO, tenant_calibrado, estado_demo, falso, k=3)

    # 1ª vez: una llamada con todo el corpus + una con la pregunta. 2ª vez: solo la pregunta.
    assert [len(llamada) for llamada in falso.llamadas] == [len(estado_demo.corpus), 1, 1]
    assert primera.tokens_embeddings > segunda.tokens_embeddings > 0  # la 1ª paga además el indexado
    assert estado_demo.vectores is not None


def test_si_el_contenido_cambia_se_revectoriza(estado_demo: IndexState, tenant_calibrado: TenantConfig, sample_dir: Path) -> None:
    falso = _embeddings()
    buscar("semantico", PREGUNTA_SINONIMO, tenant_calibrado, estado_demo, falso, k=3)

    # Un reindex arma un IndexState nuevo (sin vectores): los de antes no pueden quedar pegados al contenido nuevo.
    nuevo = reindex_module.reindex(TENANT_DEMO, sample_dir)

    assert nuevo.vectores is None


# --- Degradación al léxico --------------------------------------------------------------


def test_sin_umbral_calibrado_el_modo_no_esta_disponible(estado_demo: IndexState) -> None:
    falso = _embeddings()

    busqueda = buscar("semantico", PREGUNTA_SINONIMO, obtener_tenant(TENANT_DEMO), estado_demo, falso, k=3)

    assert busqueda.retrieval_usado == "lexico"
    assert busqueda.motivo_degradacion == MOTIVO_NO_CALIBRADO
    assert falso.llamadas == []  # ni se llama a Azure si el modo no está calibrado


def test_hibrido_sin_calibrar_responde_con_lexico_sin_llamar_a_azure(
    estado_demo: IndexState, tenant_calibrado: TenantConfig
) -> None:
    falso = _embeddings()

    # tenant_calibrado tiene umbral semántico, pero NO el bloque híbrido: ese modo es independiente.
    busqueda = buscar("hibrido", "¿Qué idiomas hablás?", tenant_calibrado, estado_demo, falso, k=3)

    assert busqueda.retrieval_usado == "lexico"
    assert busqueda.motivo_degradacion == MOTIVO_NO_CALIBRADO
    assert falso.llamadas == []


def test_sin_generador_de_embeddings_responde_con_lexico(estado_demo: IndexState, tenant_calibrado: TenantConfig) -> None:
    busqueda = buscar("semantico", PREGUNTA_SINONIMO, tenant_calibrado, estado_demo, None, k=3)

    assert busqueda.retrieval_usado == "lexico"
    assert busqueda.motivo_degradacion == MOTIVO_SIN_EMBEDDINGS


def test_sin_deployment_de_embeddings_responde_con_lexico(estado_demo: IndexState, tenant_calibrado: TenantConfig) -> None:
    falso = EmbeddingsFalso(error=FoundryNoConfigurado("falta el deployment"))

    busqueda = buscar("semantico", PREGUNTA_SINONIMO, tenant_calibrado, estado_demo, falso, k=3)

    assert busqueda.retrieval_usado == "lexico"
    assert busqueda.motivo_degradacion == MOTIVO_SIN_EMBEDDINGS


def test_si_azure_falla_responde_con_lexico_y_no_con_error(estado_demo: IndexState, tenant_calibrado: TenantConfig) -> None:
    falso = EmbeddingsFalso(error=FoundryError("sin red"))

    busqueda = buscar("semantico", "¿Qué idiomas hablás?", tenant_calibrado, estado_demo, falso, k=3)

    assert busqueda.retrieval_usado == "lexico"
    assert busqueda.motivo_degradacion == MOTIVO_FALLO
    assert busqueda.resultado.loc[0, "text"].startswith("## Idiomas")  # y igual contesta bien


def test_con_el_tope_diario_agotado_no_llama_a_azure_y_responde_con_lexico(
    estado_demo: IndexState, tenant_calibrado: TenantConfig
) -> None:
    falso = _embeddings()
    estado = EstadoLimites(Limites(tope_diario_global=5))
    estado.presupuesto.registrar(TENANT_DEMO, 5)  # el día ya llegó al tope
    limitado = GeneradorEmbeddingsConPresupuesto(falso, estado, tenant_calibrado)

    busqueda = buscar("semantico", PREGUNTA_SINONIMO, tenant_calibrado, estado_demo, limitado, k=3)

    assert busqueda.retrieval_usado == "lexico"
    assert busqueda.motivo_degradacion == MOTIVO_PRESUPUESTO
    assert falso.llamadas == []


# --- Presupuesto ---------------------------------------------------------------------


def test_los_tokens_de_embeddings_se_suman_al_tope_diario(estado_demo: IndexState, tenant_calibrado: TenantConfig) -> None:
    estado = EstadoLimites(Limites())
    limitado = GeneradorEmbeddingsConPresupuesto(_embeddings(), estado, tenant_calibrado)

    busqueda = buscar("semantico", PREGUNTA_SINONIMO, tenant_calibrado, estado_demo, limitado, k=3)

    assert busqueda.tokens_embeddings > 0
    assert estado.presupuesto.gastado_hoy() == busqueda.tokens_embeddings  # indexado + pregunta, todo contado
