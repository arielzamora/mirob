"""Tests de POST /api/v1/chat con el campo `retrieval` (léxico por defecto; semántico si el tenant lo calibró).

Usan el tenant "demo" con un umbral semántico agregado en memoria y un EmbeddingsFalso (sin red ni Azure).
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import app.api.dependencias as dependencias_module
from app.finops import EstadoLimites
from app.foundry.tipos import FoundryError
from app.rag.generate import MENSAJE_SIN_INFORMACION
from app.tenants import ConfigHibrido, ConfigRetrieval, TenantConfig, obtener_tenant
from tests.conftest import TENANT_DEMO, EmbeddingsFalso, GeneradorFalso

URL = "/api/v1/chat"
UMBRAL_SEMANTICO = 0.05
PREGUNTA_SINONIMO = "lenguas"  # no está en el contenido: solo el modelo (el falso, acá) la acerca a "idiomas"


def _chat(client: TestClient, message: str, **extra):
    return client.post(URL, json={"tenant_id": TENANT_DEMO, "message": message, **extra})


@pytest.fixture
def demo_calibrado(monkeypatch: pytest.MonkeyPatch) -> TenantConfig:
    """El tenant demo con un umbral semántico: así el modo semántico queda disponible durante el test."""
    original = obtener_tenant
    calibrado = replace(original(TENANT_DEMO), retrieval=ConfigRetrieval(umbral_semantico=UMBRAL_SEMANTICO))
    monkeypatch.setattr(
        dependencias_module, "obtener_tenant", lambda tenant_id: calibrado if tenant_id == TENANT_DEMO else original(tenant_id)
    )
    return calibrado


@pytest.fixture
def embeddings(embeddings_falso: EmbeddingsFalso) -> EmbeddingsFalso:
    embeddings_falso.sinonimos["lenguas"] = "idiomas"
    return embeddings_falso


# --- Default léxico ---------------------------------------------------------------------


def test_sin_retrieval_se_usa_el_lexico_y_no_se_gastan_embeddings(client: TestClient, embeddings: EmbeddingsFalso) -> None:
    cuerpo = _chat(client, "¿Qué idiomas hablás?").json()

    assert cuerpo["retrieval_used"] == "lexico"
    assert cuerpo["retrieval_fallback_reason"] is None
    assert cuerpo["embedding_usage"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert cuerpo["threshold"] == 0.10
    assert embeddings.llamadas == []


def test_un_retrieval_desconocido_es_un_422(client: TestClient) -> None:
    assert _chat(client, "hola", retrieval="vectorial").status_code == 422


# --- Semántico ---------------------------------------------------------------------------


def test_semantico_encuentra_el_chunk_por_significado(
    client: TestClient, embeddings: EmbeddingsFalso, demo_calibrado: TenantConfig
) -> None:
    lexico = _chat(client, PREGUNTA_SINONIMO).json()
    semantico = _chat(client, PREGUNTA_SINONIMO, retrieval="semantico").json()

    assert lexico["top_score"] == 0  # el léxico no encuentra nada
    assert semantico["retrieval_used"] == "semantico"
    assert semantico["retrieval_fallback_reason"] is None
    assert semantico["chunks"][0]["section"] == "## Idiomas"
    assert semantico["threshold"] == UMBRAL_SEMANTICO  # el umbral del modo, no el 0.10 del léxico
    assert semantico["below_threshold"] is False


def test_los_tokens_de_embeddings_se_informan_y_cuentan_en_el_tope_diario(
    client: TestClient, embeddings: EmbeddingsFalso, demo_calibrado: TenantConfig, limites_holgados: EstadoLimites
) -> None:
    cuerpo = _chat(client, PREGUNTA_SINONIMO, retrieval="semantico").json()

    gastados = cuerpo["embedding_usage"]["total_tokens"]
    assert gastados > 0
    assert cuerpo["embedding_usage"]["input_tokens"] == gastados
    assert limites_holgados.presupuesto.gastado_hoy() == gastados  # lo mismo que se le mostró a quien preguntó


def test_en_foundry_el_modo_semantico_le_da_contexto_al_llm_donde_el_lexico_no_llamaba(
    client: TestClient, embeddings: EmbeddingsFalso, demo_calibrado: TenantConfig, generador_falso: GeneradorFalso
) -> None:
    sin_embeddings = _chat(client, PREGUNTA_SINONIMO, mode="foundry").json()
    assert sin_embeddings["llm_called"] is False  # léxico: nada supera el umbral, 0 tokens

    con_embeddings = _chat(client, PREGUNTA_SINONIMO, mode="foundry", retrieval="semantico").json()

    assert con_embeddings["llm_called"] is True
    assert "## Idiomas" in generador_falso.llamadas[0][1]  # el chunk correcto viajó como contexto


def test_en_foundry_una_pregunta_fuera_de_tema_sigue_sin_llamar_al_llm(
    client: TestClient, embeddings: EmbeddingsFalso, demo_calibrado: TenantConfig, generador_falso: GeneradorFalso
) -> None:
    cuerpo = _chat(client, "xyzzy plugh", mode="foundry", retrieval="semantico").json()

    assert cuerpo["retrieval_used"] == "semantico"
    assert cuerpo["llm_called"] is False
    assert cuerpo["answer"] == MENSAJE_SIN_INFORMACION.format(nombre=demo_calibrado.nombre)
    assert generador_falso.llamadas == []
    assert cuerpo["embedding_usage"]["total_tokens"] > 0  # igual se pagó el embedding de la pregunta


# --- Híbrido -----------------------------------------------------------------------------


@pytest.fixture
def demo_hibrido(monkeypatch: pytest.MonkeyPatch) -> TenantConfig:
    """El tenant demo con el modo híbrido calibrado (en su propia escala normalizada, con umbral 0,3)."""
    original = obtener_tenant
    config = ConfigHibrido(0.3, 0.7, 0.0, 0.5, 0.05, 0.4)
    calibrado = replace(original(TENANT_DEMO), retrieval=ConfigRetrieval(hibrido=config))
    monkeypatch.setattr(
        dependencias_module, "obtener_tenant", lambda tenant_id: calibrado if tenant_id == TENANT_DEMO else original(tenant_id)
    )
    return calibrado


def test_hibrido_responde_con_su_umbral_y_cuenta_los_embeddings(
    client: TestClient, embeddings: EmbeddingsFalso, demo_hibrido: TenantConfig
) -> None:
    cuerpo = _chat(client, PREGUNTA_SINONIMO, retrieval="hibrido").json()

    assert cuerpo["retrieval_used"] == "hibrido"
    assert cuerpo["retrieval_fallback_reason"] is None
    assert cuerpo["chunks"][0]["section"] == "## Idiomas"
    assert cuerpo["threshold"] == 0.3  # el umbral del híbrido, no el del léxico (0,10)
    assert cuerpo["below_threshold"] is False
    assert cuerpo["embedding_usage"]["total_tokens"] > 0


def test_hibrido_en_foundry_le_da_al_llm_el_contexto_correcto(
    client: TestClient, embeddings: EmbeddingsFalso, demo_hibrido: TenantConfig, generador_falso: GeneradorFalso
) -> None:
    cuerpo = _chat(client, PREGUNTA_SINONIMO, mode="foundry", retrieval="hibrido").json()

    assert cuerpo["llm_called"] is True
    assert "## Idiomas" in generador_falso.llamadas[0][1]


# --- Retrieval por defecto del tenant ----------------------------------------------------


@pytest.fixture
def demo_con_hibrido_por_defecto(monkeypatch: pytest.MonkeyPatch) -> TenantConfig:
    """Un tenant que responde con el híbrido cuando el pedido no dice nada (como el widget público)."""
    original = obtener_tenant
    config = ConfigHibrido(0.3, 0.7, 0.0, 0.5, 0.05, 0.4)
    calibrado = replace(original(TENANT_DEMO), retrieval=ConfigRetrieval(hibrido=config, por_defecto="hibrido"))
    monkeypatch.setattr(
        dependencias_module, "obtener_tenant", lambda tenant_id: calibrado if tenant_id == TENANT_DEMO else original(tenant_id)
    )
    return calibrado


def test_sin_retrieval_en_el_pedido_rige_el_por_defecto_del_tenant(
    client: TestClient, embeddings: EmbeddingsFalso, demo_con_hibrido_por_defecto: TenantConfig
) -> None:
    cuerpo = _chat(client, PREGUNTA_SINONIMO).json()  # sin campo `retrieval`, como el widget público

    assert cuerpo["retrieval_used"] == "hibrido"
    assert cuerpo["chunks"][0]["section"] == "## Idiomas"
    assert cuerpo["embedding_usage"]["total_tokens"] > 0


def test_un_retrieval_explicito_pisa_el_por_defecto(
    client: TestClient, embeddings: EmbeddingsFalso, demo_con_hibrido_por_defecto: TenantConfig
) -> None:
    cuerpo = _chat(client, PREGUNTA_SINONIMO, retrieval="lexico").json()

    assert cuerpo["retrieval_used"] == "lexico"
    assert cuerpo["retrieval_fallback_reason"] is None  # lo pidió así: no es una degradación
    assert embeddings.llamadas == []


def test_un_retrieval_nulo_equivale_a_no_mandarlo(
    client: TestClient, embeddings: EmbeddingsFalso, demo_con_hibrido_por_defecto: TenantConfig
) -> None:
    cuerpo = _chat(client, PREGUNTA_SINONIMO, retrieval=None).json()

    assert cuerpo["retrieval_used"] == "hibrido"


def test_si_el_por_defecto_falla_el_tenant_sigue_respondiendo_con_lexico(
    client: TestClient, embeddings: EmbeddingsFalso, demo_con_hibrido_por_defecto: TenantConfig
) -> None:
    embeddings.error = FoundryError("sin red")

    respuesta = _chat(client, "¿Qué idiomas hablás?")

    assert respuesta.status_code == 200
    assert respuesta.json()["retrieval_used"] == "lexico"
    assert respuesta.json()["retrieval_fallback_reason"] == "embeddings_no_disponibles"


def test_hibrido_sin_calibrar_responde_con_lexico(client: TestClient, embeddings: EmbeddingsFalso) -> None:
    cuerpo = _chat(client, "¿Qué idiomas hablás?", retrieval="hibrido").json()

    assert cuerpo["retrieval_used"] == "lexico"
    assert cuerpo["retrieval_fallback_reason"] == "modo_no_calibrado"
    assert embeddings.llamadas == []


# --- Degradación -------------------------------------------------------------------------


def test_sin_calibrar_el_modo_semantico_responde_con_lexico(client: TestClient, embeddings: EmbeddingsFalso) -> None:
    cuerpo = _chat(client, "¿Qué idiomas hablás?", retrieval="semantico").json()

    assert cuerpo["retrieval_used"] == "lexico"
    assert cuerpo["retrieval_fallback_reason"] == "modo_no_calibrado"
    assert cuerpo["threshold"] == 0.10
    assert embeddings.llamadas == []


def test_si_los_embeddings_fallan_el_chat_responde_igual_con_lexico(
    client: TestClient, embeddings: EmbeddingsFalso, demo_calibrado: TenantConfig
) -> None:
    embeddings.error = FoundryError("sin red")

    respuesta = _chat(client, "¿Qué idiomas hablás?", retrieval="semantico")

    assert respuesta.status_code == 200  # nunca un 5xx por culpa de los embeddings
    cuerpo = respuesta.json()
    assert cuerpo["retrieval_used"] == "lexico"
    assert cuerpo["retrieval_fallback_reason"] == "embeddings_no_disponibles"
    assert cuerpo["chunks"][0]["section"] == "## Idiomas"
