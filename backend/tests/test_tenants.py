"""Tests del registro de tenants y del aislamiento de datos entre ellos.

Los de aislamiento arman dos tenants temporales ("uno" y "dos") con contenido
distinto, para afirmar que lo de uno jamás aparece en las respuestas del otro.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.reindex as reindex_module
import app.tenants as tenants_module
from app.api.security import VAR_ADMIN_API_KEY
from app.main import app
from app.tenants import (
    LARGO_MAXIMO_TENANT_ID,
    TenantInvalido,
    TenantNoEncontrado,
    listar_tenants,
    obtener_tenant,
    validar_tenant_id,
)
from tests.conftest import CLAVE_ADMIN_TEST

# --- Formato del tenant_id ---------------------------------------------------


@pytest.mark.parametrize("tenant_id", ["ariel-zamora", "digital-zur", "chocolateria", "a1", "x-1-y"])
def test_acepta_ids_con_formato_valido(tenant_id: str) -> None:
    assert validar_tenant_id(tenant_id) == tenant_id


@pytest.mark.parametrize(
    "tenant_id",
    [
        "",
        "Ariel",  # mayúsculas
        "ariel_zamora",  # guion bajo
        "ariel zamora",  # espacio
        "-ariel",  # empieza con guion
        "ariel-",  # termina con guion
        "ariel--zamora",  # guiones dobles
        "../ariel-zamora",  # path traversal
        "..\\ariel-zamora",
        "ariel-zamora/../digital-zur",
        "ariel.zamora",
        "a" * (LARGO_MAXIMO_TENANT_ID + 1),
    ],
)
def test_rechaza_ids_con_formato_invalido(tenant_id: str) -> None:
    with pytest.raises(TenantInvalido):
        validar_tenant_id(tenant_id)


# --- Configuración versionada -----------------------------------------------


def test_todos_los_tenants_versionados_tienen_configuracion_valida() -> None:
    # Si alguien rompe un tenant.json o deja un prompt.md vacío, este test lo atrapa.
    tenants = listar_tenants()
    ids = {t.tenant_id for t in tenants}

    assert {"demo", "ariel-zamora", "digital-zur"} <= ids
    for tenant in tenants:
        assert tenant.nombre
        assert tenant.system_prompt
        assert 0.0 <= tenant.umbral_similitud <= 1.0


def test_cada_tenant_tiene_su_propio_system_prompt() -> None:
    prompts = {t.tenant_id: t.system_prompt for t in listar_tenants()}

    # Prompts independientes: ninguno es copia de otro.
    assert len(set(prompts.values())) == len(prompts)
    assert "Ariel" in prompts["ariel-zamora"]
    assert "Digital Zur" in prompts["digital-zur"]


def test_un_tenant_deshabilitado_se_comporta_como_inexistente() -> None:
    # "chocolateria" está reservado pero deshabilitado hasta que tenga contenido.
    with pytest.raises(TenantNoEncontrado):
        obtener_tenant("chocolateria")
    assert "chocolateria" not in {t.tenant_id for t in listar_tenants()}


def test_un_tenant_con_formato_valido_pero_desconocido_no_existe() -> None:
    with pytest.raises(TenantNoEncontrado):
        obtener_tenant("no-existe")


# --- Aislamiento entre tenants -----------------------------------------------


def _crear_tenant(raiz: Path, tenant_id: str) -> None:
    """Escribe un tenant.json y un prompt.md mínimos en la carpeta de tenants temporal."""
    carpeta = raiz / tenant_id
    carpeta.mkdir(parents=True)
    (carpeta / "tenant.json").write_text(json.dumps({"nombre": tenant_id.upper()}), encoding="utf-8")
    (carpeta / "prompt.md").write_text(f"Prompt de {tenant_id}.", encoding="utf-8")


@pytest.fixture
def dos_tenants(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, Path]:
    """Cliente con dos tenants vacíos ("uno" y "dos") y todo el disco en tmp_path."""
    monkeypatch.setenv(VAR_ADMIN_API_KEY, CLAVE_ADMIN_TEST)
    config = tmp_path / "tenants"
    _crear_tenant(config, "uno")
    _crear_tenant(config, "dos")
    data = tmp_path / "data"
    monkeypatch.setattr(tenants_module, "TENANTS_DIR", config)
    monkeypatch.setattr(reindex_module, "CONTENT_DIR", data)
    monkeypatch.setattr(reindex_module, "_states", {})
    return TestClient(app), data


def _subir(client: TestClient, tenant_id: str, nombre: str, texto: str):
    return client.post(
        "/admin/content",
        data={"tenant_id": tenant_id},
        files={"file": (nombre, texto.encode("utf-8"), "text/markdown")},
        headers={"X-API-Key": CLAVE_ADMIN_TEST},
    )


def test_el_contenido_se_guarda_en_la_carpeta_de_su_tenant(dos_tenants: tuple[TestClient, Path]) -> None:
    client, data = dos_tenants

    assert _subir(client, "uno", "a.md", "## Pan\nP: ¿Qué hornea?\nR: Hornea pan.").status_code == 200

    assert (data / "tenants" / "uno" / "a.md").exists()
    assert not (data / "tenants" / "dos").exists()


def test_el_chat_de_un_tenant_nunca_devuelve_chunks_de_otro(dos_tenants: tuple[TestClient, Path]) -> None:
    client, _ = dos_tenants
    _subir(client, "uno", "uno.md", "## Panadería\nP: ¿Qué hornea?\nR: Hornea pan casero.")
    _subir(client, "dos", "dos.md", "## Ferretería\nP: ¿Qué vende?\nR: Vende tornillos y martillos.")

    # La misma pregunta, a dos tenants: cada uno responde solo con lo suyo.
    de_uno = client.post("/api/v1/chat", json={"tenant_id": "uno", "message": "¿Qué vende y qué hornea?"}).json()
    de_dos = client.post("/api/v1/chat", json={"tenant_id": "dos", "message": "¿Qué vende y qué hornea?"}).json()

    assert de_uno["chunks"][0]["source"] == "uno.md"
    assert de_dos["chunks"][0]["source"] == "dos.md"
    assert "tornillos" not in de_uno["answer"]
    assert "pan casero" not in de_dos["answer"]


def test_subir_contenido_a_un_tenant_no_cambia_el_indice_del_otro(dos_tenants: tuple[TestClient, Path]) -> None:
    client, _ = dos_tenants
    _subir(client, "uno", "uno.md", "## Panadería\nP: ¿Qué hornea?\nR: Hornea pan casero.")

    respuesta = _subir(client, "dos", "dos.md", "## Ferretería\nP: ¿Qué vende?\nR: Vende tornillos y martillos.")

    # El total de "dos" cuenta solo sus archivos, no los de "uno".
    assert respuesta.json() == {"filename": "dos.md", "total_chunks": 1, "total_files": 1}
    assert reindex_module.get_state("uno").corpus["source"].tolist() == ["uno.md"]


def test_un_tenant_sin_contenido_no_usa_el_de_otro(dos_tenants: tuple[TestClient, Path]) -> None:
    client, _ = dos_tenants
    _subir(client, "uno", "uno.md", "## Panadería\nP: ¿Qué hornea?\nR: Hornea pan casero.")

    respuesta = client.post("/api/v1/chat", json={"tenant_id": "dos", "message": "¿Qué hornea?"})

    assert respuesta.status_code == 503
    assert "No hay contenido" in respuesta.json()["detail"]


# --- Errores de tenant en la API ---------------------------------------------


def test_chat_con_tenant_inexistente_responde_404(client: TestClient) -> None:
    respuesta = client.post("/api/v1/chat", json={"tenant_id": "no-existe", "message": "hola"})

    assert respuesta.status_code == 404


@pytest.mark.parametrize("tenant_id", ["../etc", "Ariel", "a b"])
def test_chat_con_tenant_de_formato_invalido_responde_422(client: TestClient, tenant_id: str) -> None:
    assert client.post("/api/v1/chat", json={"tenant_id": tenant_id, "message": "hola"}).status_code == 422


def test_chat_sin_tenant_responde_422(client: TestClient) -> None:
    # No hay tenant por defecto: un request sin tenant_id no puede caer en el contenido de otro.
    assert client.post("/api/v1/chat", json={"message": "hola"}).status_code == 422


def test_admin_con_tenant_inexistente_responde_404_y_no_escribe(
    dos_tenants: tuple[TestClient, Path],
) -> None:
    client, data = dos_tenants

    assert _subir(client, "no-existe", "a.md", "## A\nx").status_code == 404
    assert not data.exists()


def test_admin_con_tenant_traversal_responde_422_y_no_escribe(dos_tenants: tuple[TestClient, Path]) -> None:
    client, data = dos_tenants

    assert _subir(client, "../uno", "a.md", "## A\nx").status_code == 422
    assert not data.exists()


def test_el_tenant_demo_es_de_solo_lectura(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VAR_ADMIN_API_KEY, CLAVE_ADMIN_TEST)

    respuesta = _subir(client, "demo", "intruso.md", "## A\nx")

    assert respuesta.status_code == 403


def test_en_modo_foundry_el_llm_solo_recibe_el_contenido_y_el_prompt_de_su_tenant(
    dos_tenants: tuple[TestClient, Path], generador_falso
) -> None:
    client, _ = dos_tenants
    _subir(client, "uno", "uno.md", "## Panadería\nP: ¿Qué hornea?\nR: Hornea pan casero.")
    _subir(client, "dos", "dos.md", "## Ferretería\nP: ¿Qué vende?\nR: Vende tornillos y martillos.")

    client.post(
        "/api/v1/chat", json={"tenant_id": "uno", "message": "¿Qué hornea y qué vende?", "mode": "foundry"}
    )

    assert len(generador_falso.llamadas) == 1
    instrucciones, mensaje, _ = generador_falso.llamadas[0]
    assert instrucciones == "Prompt de uno."
    assert "pan casero" in mensaje
    assert "tornillos" not in mensaje  # nada del tenant "dos" llega al modelo


# --- GET /api/v1/tenants ------------------------------------------------------


def test_listar_tenants_devuelve_solo_id_nombre_y_acceso_de_los_habilitados(client: TestClient) -> None:
    respuesta = client.get("/api/v1/tenants")

    assert respuesta.status_code == 200
    tenants = respuesta.json()
    ids = [t["tenant_id"] for t in tenants]
    assert ids == sorted(ids)
    assert {"demo", "ariel-zamora", "digital-zur"} <= set(ids)
    assert "chocolateria" not in ids  # deshabilitado
    for tenant in tenants:
        # Nada de configuración interna: ni prompt, ni umbral, ni orígenes.
        assert set(tenant) == {"tenant_id", "nombre", "acceso"}
