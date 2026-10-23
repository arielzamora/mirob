"""Tests de GET /health y POST /admin/reindex."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.security import VAR_ADMIN_API_KEY
from app.finops import Limites
from tests.conftest import CLAVE_ADMIN_TEST, TENANT_AISLADO, usar_limites

# --- /health -----------------------------------------------------------------


def test_health_responde_ok_sin_autenticacion(client: TestClient) -> None:
    respuesta = client.get("/health")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"status": "ok"}


def test_health_no_depende_de_foundry_ni_del_contenido(client_aislado: tuple[TestClient, Path]) -> None:
    # Con el tenant sin contenido y sin Foundry configurado, el proceso sigue "vivo".
    client, _ = client_aislado

    assert client.get("/health").status_code == 200


def test_health_no_cuenta_para_el_limite_del_chat(client: TestClient) -> None:
    usar_limites(Limites(chat_por_minuto=1))

    assert all(client.get("/health").status_code == 200 for _ in range(5))


# --- /admin/reindex -------------------------------------------------------------


def _reindexar(client: TestClient, clave: str | None = CLAVE_ADMIN_TEST, tenant_id: str | None = None):
    headers = {"X-API-Key": clave} if clave else {}
    return client.post("/admin/reindex", data={"tenant_id": tenant_id} if tenant_id else {}, headers=headers)


def test_reindex_levanta_archivos_copiados_por_fuera_de_la_api(
    client_aislado: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, carpeta = client_aislado
    monkeypatch.setenv(VAR_ADMIN_API_KEY, CLAVE_ADMIN_TEST)
    chat = {"tenant_id": TENANT_AISLADO, "message": "¿Qué toma?"}
    assert client.post("/api/v1/chat", json=chat).status_code == 503  # todavía sin contenido

    # El archivo llega directo al disco (como subirlo al share de Azure Files), no por /admin/content.
    (carpeta / "cafe.md").write_text("## Cafetería\nP: ¿Qué toma?\nR: Toma café con leche.", encoding="utf-8")
    respuesta = _reindexar(client, tenant_id=TENANT_AISLADO)

    assert respuesta.status_code == 200
    assert respuesta.json() == {"chunks_por_tenant": {TENANT_AISLADO: 1}}
    asistente = client.post("/api/v1/chat", json=chat)
    assert asistente.status_code == 200
    assert asistente.json()["chunks"][0]["source"] == "cafe.md"


def test_reindex_sin_tenant_reindexa_todos_los_habilitados(
    client_aislado: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = client_aislado
    monkeypatch.setenv(VAR_ADMIN_API_KEY, CLAVE_ADMIN_TEST)

    cuerpo = _reindexar(client).json()["chunks_por_tenant"]

    assert {"demo", "ariel-zamora", "digital-zur"} <= set(cuerpo)
    assert "chocolateria" not in cuerpo  # deshabilitado
    assert cuerpo["demo"] > 0  # el perfil ficticio sí tiene contenido
    assert cuerpo["ariel-zamora"] == 0  # carpeta vacía


def test_reindex_exige_la_api_key(client_aislado: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = client_aislado
    monkeypatch.setenv(VAR_ADMIN_API_KEY, CLAVE_ADMIN_TEST)

    assert _reindexar(client, clave=None).status_code == 401
    assert _reindexar(client, clave="equivocada").status_code == 401


def test_reindex_con_tenant_inexistente_o_invalido(
    client_aislado: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = client_aislado
    monkeypatch.setenv(VAR_ADMIN_API_KEY, CLAVE_ADMIN_TEST)

    assert _reindexar(client, tenant_id="no-existe").status_code == 404
    assert _reindexar(client, tenant_id="../etc").status_code == 422


def test_reindex_tambien_esta_limitado_por_ip(
    client_aislado: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = client_aislado
    monkeypatch.setenv(VAR_ADMIN_API_KEY, CLAVE_ADMIN_TEST)
    usar_limites(Limites(admin_por_minuto=1))

    assert _reindexar(client, clave="mala").status_code == 401
    assert _reindexar(client).status_code == 429


def test_un_tenant_sin_contenido_empieza_a_responder_al_aparecer_archivos_sin_reindexar(
    client_aislado: tuple[TestClient, Path],
) -> None:
    client, carpeta = client_aislado
    chat = {"tenant_id": TENANT_AISLADO, "message": "¿Qué toma?"}
    assert client.post("/api/v1/chat", json=chat).status_code == 503  # el índice vacío queda cacheado

    (carpeta / "cafe.md").write_text("## Cafetería\nP: ¿Qué toma?\nR: Toma café con leche.", encoding="utf-8")

    # Sin llamar a /admin/reindex: el índice vacío se revisa de nuevo y encuentra el archivo.
    respuesta = client.post("/api/v1/chat", json=chat)
    assert respuesta.status_code == 200
    assert respuesta.json()["chunks"][0]["source"] == "cafe.md"
