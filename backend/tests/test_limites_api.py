"""Tests de los límites aplicados en la API: pedidos por IP, llamadas al LLM por IP y tope diario."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.dependencias import VAR_CONFIAR_EN_PROXY
from app.api.security import VAR_ADMIN_API_KEY
from app.finops import Limites
from tests.conftest import CLAVE_ADMIN_TEST, TENANT_AISLADO, TENANT_DEMO, GeneradorFalso, usar_limites

URL = "/api/v1/chat"
PREGUNTA_CON_RESPUESTA = "¿Qué idiomas hablás?"
PREGUNTA_SIN_RESPUESTA = "xyzzy plugh"  # score 0: bajo el umbral, el LLM no se usa


def _chat(client: TestClient, mensaje: str = PREGUNTA_CON_RESPUESTA, mode: str = "raw", **kwargs):
    return client.post(URL, json={"tenant_id": TENANT_DEMO, "message": mensaje, "mode": mode}, **kwargs)


# --- Límite de pedidos por IP (chat) --------------------------------------------


def test_el_chat_corta_con_429_y_retry_after_al_superar_el_limite(client: TestClient) -> None:
    usar_limites(Limites(chat_por_minuto=3))

    codigos = [_chat(client).status_code for _ in range(3)]
    bloqueado = _chat(client)

    assert codigos == [200, 200, 200]
    assert bloqueado.status_code == 429
    assert int(bloqueado.headers["retry-after"]) >= 1
    assert "Demasiados mensajes" in bloqueado.json()["detail"]


def test_el_limite_aplica_a_todos_los_modos(client: TestClient, generador_falso: GeneradorFalso) -> None:
    usar_limites(Limites(chat_por_minuto=2))

    _chat(client, mode="raw")
    _chat(client, mode="humanized")

    assert _chat(client, mode="foundry").status_code == 429
    assert generador_falso.llamadas == []


def test_el_limite_es_por_ip_y_no_se_puede_evadir_falsificando_x_forwarded_for(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    usar_limites(Limites(chat_por_minuto=1))
    monkeypatch.setenv(VAR_CONFIAR_EN_PROXY, "1")

    # Detrás de un proxy de confianza cuenta el ÚLTIMO valor (el que agregó el proxy).
    primero = _chat(client, headers={"X-Forwarded-For": "6.6.6.6, 9.9.9.9"})
    falsificado = _chat(client, headers={"X-Forwarded-For": "7.7.7.7, 9.9.9.9"})  # mismo cliente real
    otro_cliente = _chat(client, headers={"X-Forwarded-For": "6.6.6.6, 8.8.8.8"})

    assert primero.status_code == 200
    assert falsificado.status_code == 429
    assert otro_cliente.status_code == 200


def test_sin_proxy_de_confianza_se_ignora_x_forwarded_for(client: TestClient) -> None:
    usar_limites(Limites(chat_por_minuto=1))

    primero = _chat(client, headers={"X-Forwarded-For": "1.1.1.1"})
    segundo = _chat(client, headers={"X-Forwarded-For": "2.2.2.2"})  # inventar una IP no sirve

    assert (primero.status_code, segundo.status_code) == (200, 429)


# --- Límite de llamadas al LLM por IP -------------------------------------------


def test_solo_las_llamadas_reales_al_llm_cuentan_para_el_limite_por_hora(
    client: TestClient, generador_falso: GeneradorFalso
) -> None:
    usar_limites(Limites(foundry_por_hora=2))

    # Sin respuesta en el contenido: bajo el umbral, 0 tokens y NO consumen cupo del LLM.
    for _ in range(5):
        assert _chat(client, PREGUNTA_SIN_RESPUESTA, mode="foundry").status_code == 200
    assert generador_falso.llamadas == []

    # Con respuesta: van al LLM y sí consumen cupo.
    codigos = [_chat(client, mode="foundry").status_code for _ in range(2)]
    bloqueado = _chat(client, mode="foundry")

    assert codigos == [200, 200]
    assert bloqueado.status_code == 429
    assert int(bloqueado.headers["retry-after"]) >= 1
    assert len(generador_falso.llamadas) == 2


# --- Tope diario de tokens ------------------------------------------------------


def test_al_alcanzar_el_tope_diario_foundry_responde_429_pero_lo_gratis_sigue_andando(
    client: TestClient, generador_falso: GeneradorFalso
) -> None:
    # El GeneradorFalso gasta 150 tokens por llamada: con tope 200, la 2ª pasa (150 < 200) y la 3ª no.
    estado = usar_limites(Limites(tope_diario_global=200))

    codigos = [_chat(client, mode="foundry").status_code for _ in range(2)]
    agotado = _chat(client, mode="foundry")

    assert codigos == [200, 200]
    assert agotado.status_code == 429
    assert "límite diario" in agotado.json()["detail"]
    assert int(agotado.headers["retry-after"]) >= 1
    assert estado.presupuesto.gastado_hoy() == 300
    assert len(generador_falso.llamadas) == 2  # el tercero nunca llegó al modelo

    # Lo que no cuesta tokens no se corta.
    assert _chat(client, mode="raw").status_code == 200
    assert _chat(client, mode="humanized").status_code == 200
    assert _chat(client, PREGUNTA_SIN_RESPUESTA, mode="foundry").status_code == 200


def test_la_respuesta_ok_informa_los_tokens_que_se_anotaron(
    client: TestClient, generador_falso: GeneradorFalso
) -> None:
    estado = usar_limites(Limites())

    cuerpo = _chat(client, mode="foundry").json()

    assert cuerpo["usage"]["total_tokens"] == 150
    assert estado.presupuesto.gastado_hoy() == 150


# --- Límite del admin -------------------------------------------------------------


def test_el_admin_frena_los_intentos_repetidos_de_adivinar_la_clave(
    client_aislado: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = client_aislado
    monkeypatch.setenv(VAR_ADMIN_API_KEY, CLAVE_ADMIN_TEST)
    usar_limites(Limites(admin_por_minuto=2))

    def intento(clave: str) -> int:
        return client.post(
            "/admin/content",
            data={"tenant_id": TENANT_AISLADO},
            files={"file": ("a.md", b"## A\nx", "text/markdown")},
            headers={"X-API-Key": clave},
        ).status_code

    assert [intento("mala-1"), intento("mala-2")] == [401, 401]
    # Tercer intento: ya ni se compara la clave, ni siquiera la correcta.
    assert intento(CLAVE_ADMIN_TEST) == 429
