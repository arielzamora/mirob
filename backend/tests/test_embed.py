"""Tests de GET /embed/{tenant_id}: el frame-ancestors sale del tenant, no de un valor global."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.tenants as tenants_module
from app.api.embed import VAR_FRONTEND_URL
from app.main import app

FRONT = "https://front.example.com"


def _crear_tenant(raiz: Path, tenant_id: str, origenes: list[str]) -> None:
    carpeta = raiz / tenant_id
    carpeta.mkdir(parents=True)
    datos = {"nombre": tenant_id.title(), "origenes_permitidos": origenes}
    (carpeta / "tenant.json").write_text(json.dumps(datos), encoding="utf-8")
    (carpeta / "prompt.md").write_text("Prompt.", encoding="utf-8")


@pytest.fixture
def embed_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """Dos tenants: "uno" se puede embeber en dos sitios y "dos" en ninguno."""
    raiz = tmp_path / "tenants"
    _crear_tenant(raiz, "uno", ["https://sitio.com", "https://www.sitio.com"])
    _crear_tenant(raiz, "dos", [])
    monkeypatch.setattr(tenants_module, "TENANTS_DIR", raiz)
    monkeypatch.setenv(VAR_FRONTEND_URL, FRONT)
    return TestClient(app)


def test_el_frame_ancestors_son_los_origenes_del_tenant(embed_client: TestClient) -> None:
    respuesta = embed_client.get("/embed/uno")

    assert respuesta.status_code == 200
    assert respuesta.headers["content-security-policy"] == (
        f"default-src 'none'; style-src 'unsafe-inline'; frame-src {FRONT}; "
        "frame-ancestors https://sitio.com https://www.sitio.com"
    )


def test_cada_tenant_tiene_su_propio_frame_ancestors(embed_client: TestClient) -> None:
    uno = embed_client.get("/embed/uno").headers["content-security-policy"]
    dos = embed_client.get("/embed/dos").headers["content-security-policy"]

    assert "sitio.com" in uno
    assert "sitio.com" not in dos


def test_un_tenant_sin_origenes_no_se_puede_embeber_en_ningun_sitio(embed_client: TestClient) -> None:
    politica = embed_client.get("/embed/dos").headers["content-security-policy"]

    assert politica.endswith("frame-ancestors 'none'")


def test_la_pagina_embebe_el_front_con_el_tenant_y_el_modo_publico(embed_client: TestClient) -> None:
    html = embed_client.get("/embed/uno").text

    # En el HTML el "&" va escapado como &amp; (así lo exige el atributo src).
    assert f'src="{FRONT}/?tenant=uno&amp;embed=1"' in html
    assert "Asistente de Uno" in html


def test_la_pagina_lleva_los_headers_de_seguridad(embed_client: TestClient) -> None:
    cabeceras = embed_client.get("/embed/uno").headers

    assert cabeceras["x-content-type-options"] == "nosniff"
    assert cabeceras["referrer-policy"] == "no-referrer"
    assert cabeceras["content-type"].startswith("text/html")


def test_la_barra_final_de_la_url_del_front_se_tolera(
    embed_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VAR_FRONTEND_URL, FRONT + "/")

    assert f'src="{FRONT}/?tenant=uno' in embed_client.get("/embed/uno").text


def test_tenant_inexistente_responde_404_y_formato_invalido_422(embed_client: TestClient) -> None:
    assert embed_client.get("/embed/no-existe").status_code == 404
    assert embed_client.get("/embed/Ariel").status_code == 422


@pytest.mark.parametrize("valor", ["", "javascript:alert(1)", "https://front.example.com; script-src *", "*"])
def test_sin_url_del_front_valida_responde_503(
    embed_client: TestClient, monkeypatch: pytest.MonkeyPatch, valor: str
) -> None:
    monkeypatch.setenv(VAR_FRONTEND_URL, valor)

    respuesta = embed_client.get("/embed/uno")

    assert respuesta.status_code == 503
    assert VAR_FRONTEND_URL in respuesta.json()["detail"]
