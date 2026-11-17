"""Tests del bloque opcional `retrieval` de tenant.json (umbrales de los modos con embeddings)."""

import json
from pathlib import Path

import pytest

import app.tenants as tenants_module
from app.tenants import RETRIEVALS_VALIDOS, ConfigHibrido, TenantConfigError, listar_tenants, obtener_tenant


def _tenant(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **extra) -> None:
    """Crea un tenant temporal "t1" y hace que el registro lea de tmp_path."""
    carpeta = tmp_path / "t1"
    carpeta.mkdir()
    (carpeta / "tenant.json").write_text(json.dumps({"nombre": "T1", **extra}), encoding="utf-8")
    (carpeta / "prompt.md").write_text("prompt", encoding="utf-8")
    monkeypatch.setattr(tenants_module, "TENANTS_DIR", tmp_path)


def test_sin_bloque_retrieval_el_modo_semantico_no_esta_calibrado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _tenant(monkeypatch, tmp_path)

    assert obtener_tenant("t1").retrieval.umbral_semantico is None


def test_lee_el_umbral_semantico(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _tenant(monkeypatch, tmp_path, retrieval={"semantico": {"umbral": 0.45}})

    config = obtener_tenant("t1")

    assert config.retrieval.umbral_semantico == 0.45
    assert config.umbral_similitud == 0.10  # el del léxico no cambia


def test_un_umbral_entero_valido_se_acepta_como_float(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _tenant(monkeypatch, tmp_path, retrieval={"semantico": {"umbral": 1}})

    assert obtener_tenant("t1").retrieval.umbral_semantico == 1.0


@pytest.mark.parametrize(
    "retrieval",
    [
        "semantico",  # no es un objeto
        {"semantico": 0.4},  # falta el objeto con "umbral"
        {"semantico": {}},  # sin umbral
        {"semantico": {"umbral": "0.4"}},  # texto
        {"semantico": {"umbral": True}},  # bool no es un umbral
        {"semantico": {"umbral": 1.5}},  # fuera de rango
        {"semantico": {"umbral": -0.1}},
    ],
)
def test_un_bloque_retrieval_invalido_es_un_error_de_configuracion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, retrieval: object
) -> None:
    _tenant(monkeypatch, tmp_path, retrieval=retrieval)

    with pytest.raises(TenantConfigError, match="retrieval"):
        obtener_tenant("t1")


HIBRIDO = {
    "umbral": 0.4,
    "peso_semantico": 0.6,
    "piso_lexico": 0.0,
    "techo_lexico": 0.5,
    "piso_semantico": 0.25,
    "techo_semantico": 0.7,
}


def test_lee_el_bloque_hibrido_completo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _tenant(monkeypatch, tmp_path, retrieval={"hibrido": HIBRIDO})

    config = obtener_tenant("t1").retrieval

    assert config.umbral_semantico is None  # los modos se calibran por separado
    assert config.hibrido == ConfigHibrido(0.4, 0.6, 0.0, 0.5, 0.25, 0.7)


def test_el_peso_semantico_es_opcional_y_vale_0_7(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    sin_peso = {k: v for k, v in HIBRIDO.items() if k != "peso_semantico"}
    _tenant(monkeypatch, tmp_path, retrieval={"hibrido": sin_peso})

    assert obtener_tenant("t1").retrieval.hibrido.peso_semantico == 0.7


@pytest.mark.parametrize(
    "cambio",
    [
        {"umbral": 1.5},  # fuera de rango
        {"umbral": "0.4"},  # texto
        {"peso_semantico": -0.1},
        {"peso_semantico": True},  # bool no es un peso
        {"piso_lexico": None},  # falta un borde
        {"techo_semantico": 2},  # fuera de rango
        {"techo_lexico": 0.0},  # techo igual al piso: la normalización dividiría por cero
        {"techo_semantico": 0.2},  # techo menor que el piso
    ],
)
def test_un_bloque_hibrido_invalido_es_un_error_de_configuracion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cambio: dict
) -> None:
    _tenant(monkeypatch, tmp_path, retrieval={"hibrido": {**HIBRIDO, **cambio}})

    with pytest.raises(TenantConfigError, match="retrieval.hibrido"):
        obtener_tenant("t1")


def test_un_hibrido_que_no_es_un_objeto_es_un_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _tenant(monkeypatch, tmp_path, retrieval={"hibrido": 0.5})

    with pytest.raises(TenantConfigError, match="retrieval.hibrido"):
        obtener_tenant("t1")


def test_sin_por_defecto_el_tenant_responde_con_lexico(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _tenant(monkeypatch, tmp_path, retrieval={"hibrido": HIBRIDO})  # calibrado, pero nadie lo eligió por defecto

    assert obtener_tenant("t1").retrieval.por_defecto == "lexico"


@pytest.mark.parametrize(
    ("por_defecto", "bloque"),
    [("lexico", {}), ("semantico", {"semantico": {"umbral": 0.4}}), ("hibrido", {"hibrido": HIBRIDO})],
)
def test_el_por_defecto_puede_ser_cualquier_modo_disponible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, por_defecto: str, bloque: dict
) -> None:
    _tenant(monkeypatch, tmp_path, retrieval={**bloque, "por_defecto": por_defecto})

    assert obtener_tenant("t1").retrieval.por_defecto == por_defecto


@pytest.mark.parametrize(
    "retrieval",
    [
        {"por_defecto": "vectorial"},  # no existe ese modo
        {"por_defecto": 3},
        {"por_defecto": "semantico"},  # sin su bloque calibrado
        {"por_defecto": "hibrido", "semantico": {"umbral": 0.4}},  # calibró otro modo, no este
    ],
)
def test_un_por_defecto_invalido_o_sin_calibrar_es_un_error_de_configuracion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, retrieval: dict
) -> None:
    _tenant(monkeypatch, tmp_path, retrieval=retrieval)

    with pytest.raises(TenantConfigError, match="por_defecto"):
        obtener_tenant("t1")


def test_los_tenants_versionados_cargan_y_su_por_defecto_esta_calibrado() -> None:
    tenants = listar_tenants()

    assert tenants  # hay al menos uno
    for tenant in tenants:
        # Un tenant puede no tener el bloque `retrieval` (solo léxico) o tenerlo calibrado: en ambos casos su modo por
        # defecto tiene que estar disponible. Si no, `obtener_tenant` ya habría fallado al cargarlo.
        modo = tenant.retrieval.por_defecto
        assert modo in RETRIEVALS_VALIDOS
        if modo == "semantico":
            assert tenant.retrieval.umbral_semantico is not None
        if modo == "hibrido":
            assert tenant.retrieval.hibrido is not None
