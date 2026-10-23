"""Tests de la validación de orígenes, el CORS por entorno y los campos nuevos de tenant.json."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.tenants as tenants_module
from app.main import crear_app
from app.origenes import ORIGENES_DESARROLLO, VAR_CORS_ORIGINS, origenes_cors, validar_origen
from app.tenants import TenantConfigError, obtener_tenant

# --- validar_origen -----------------------------------------------------------


@pytest.mark.parametrize(
    "origen",
    ["https://digitalzur.com", "https://www.digitalzur.com", "https://app.sitio.com:8443", "http://localhost:5173"],
)
def test_acepta_origenes_validos(origen: str) -> None:
    assert validar_origen(origen) == origen


def test_normaliza_mayusculas_y_espacios() -> None:
    assert validar_origen("  HTTPS://Sitio.COM ") == "https://sitio.com"


@pytest.mark.parametrize(
    "origen",
    [
        "",
        "*",
        "https://*.sitio.com",  # sin comodines
        "http://sitio.com",  # http solo se acepta para localhost
        "https://sitio.com/",  # sin barra final
        "https://sitio.com/ruta",
        "sitio.com",
        "https://sitio.com; script-src *",  # intento de inyectar otra directiva CSP
        "https://sitio.com https://otro.com",  # espacios: serían dos valores
        "javascript:alert(1)",
        "https://sitio.com\r\nX-Evil: 1",  # inyección de header
    ],
)
def test_rechaza_origenes_invalidos(origen: str) -> None:
    with pytest.raises(ValueError):
        validar_origen(origen)


# --- CORS ---------------------------------------------------------------------


def test_cors_siempre_incluye_los_origenes_de_desarrollo() -> None:
    assert origenes_cors({}) == list(ORIGENES_DESARROLLO)


def test_cors_suma_los_origenes_del_entorno_sin_duplicar() -> None:
    env = {VAR_CORS_ORIGINS: "https://mirob.example.com, https://otro.example.com,http://localhost:5173"}

    assert origenes_cors(env) == [*ORIGENES_DESARROLLO, "https://mirob.example.com", "https://otro.example.com"]


def test_cors_con_un_origen_invalido_falla_al_arrancar() -> None:
    with pytest.raises(ValueError):
        origenes_cors({VAR_CORS_ORIGINS: "*"})


def _preflight(origen: str, origenes_app: list[str]):
    """Simula el OPTIONS que manda el navegador antes de un POST de otro origen."""
    cliente = TestClient(crear_app(origenes_app))
    return cliente.options(
        "/api/v1/chat",
        headers={
            "Origin": origen,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )


def test_cors_permite_el_origen_configurado_y_no_otros() -> None:
    origenes = [*ORIGENES_DESARROLLO, "https://mirob.example.com"]

    permitido = _preflight("https://mirob.example.com", origenes)
    ajeno = _preflight("https://sitio-malo.com", origenes)

    assert permitido.headers["access-control-allow-origin"] == "https://mirob.example.com"
    assert "access-control-allow-origin" not in ajeno.headers


def test_cors_no_permite_metodos_de_escritura_innecesarios() -> None:
    cliente = TestClient(crear_app(list(ORIGENES_DESARROLLO)))

    respuesta = cliente.options(
        "/admin/content",
        headers={"Origin": ORIGENES_DESARROLLO[0], "Access-Control-Request-Method": "DELETE"},
    )

    assert respuesta.status_code == 400  # DELETE no está en la lista de métodos permitidos


# --- tenant.json: origenes_permitidos y tope_diario_tokens ---------------------


def _tenant_con(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, datos: dict) -> None:
    carpeta = tmp_path / "tenants" / "uno"
    carpeta.mkdir(parents=True)
    (carpeta / "tenant.json").write_text(json.dumps({"nombre": "Uno", **datos}), encoding="utf-8")
    (carpeta / "prompt.md").write_text("Prompt.", encoding="utf-8")
    monkeypatch.setattr(tenants_module, "TENANTS_DIR", tmp_path / "tenants")


def test_el_tenant_normaliza_sus_origenes_permitidos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _tenant_con(monkeypatch, tmp_path, {"origenes_permitidos": ["HTTPS://Sitio.com"], "tope_diario_tokens": 5000})

    tenant = obtener_tenant("uno")

    assert tenant.origenes_permitidos == ("https://sitio.com",)
    assert tenant.tope_diario_tokens == 5000


def test_un_tenant_con_origen_invalido_es_error_de_configuracion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _tenant_con(monkeypatch, tmp_path, {"origenes_permitidos": ["https://sitio.com; script-src *"]})

    with pytest.raises(TenantConfigError):
        obtener_tenant("uno")


@pytest.mark.parametrize("tope", [0, -5, "mucho", 1.5, True])
def test_un_tope_diario_invalido_es_error_de_configuracion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tope: object
) -> None:
    _tenant_con(monkeypatch, tmp_path, {"tope_diario_tokens": tope})

    with pytest.raises(TenantConfigError):
        obtener_tenant("uno")
