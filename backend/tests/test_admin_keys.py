"""Tests de /admin/keys: crear, listar y revocar access keys.

Tenants y keys viven en carpetas temporales, el reloj y el LLM son falsos: sin red ni tocar backend/data/.
"""

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.reindex as reindex_module
import app.tenants as tenants_module
from app import sesiones
from app.accesskeys import AlmacenClavesJson, ServicioClaves
from app.api.dependencias import obtener_reloj, obtener_servicio_claves
from app.api.embed import VAR_FRONTEND_URL
from app.api.security import VAR_ADMIN_API_KEY
from app.main import app
from tests.conftest import CLAVE_ADMIN_TEST, LIMITES_HOLGADOS, usar_limites

SECRETO = "un-secreto-de-prueba-de-al-menos-32-caracteres"
FRONT = "https://front.example.com"
INICIO = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
HEADERS = {"X-API-Key": CLAVE_ADMIN_TEST}


class RelojFalso:
    def __init__(self) -> None:
        self.ahora = INICIO

    def __call__(self) -> datetime:
        return self.ahora

    def avanzar(self, **kwargs) -> None:
        self.ahora += timedelta(**kwargs)


def _crear_tenant(raiz: Path, tenant_id: str, **extra) -> None:
    carpeta = raiz / tenant_id
    carpeta.mkdir(parents=True)
    (carpeta / "tenant.json").write_text(json.dumps({"nombre": tenant_id.title(), **extra}), encoding="utf-8")
    (carpeta / "prompt.md").write_text("Prompt de prueba.", encoding="utf-8")


@dataclass
class Entorno:
    cliente: TestClient
    reloj: RelojFalso
    ruta_claves: Path


@pytest.fixture
def entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sample_dir: Path, generador_falso) -> Entorno:
    raiz = tmp_path / "tenants"
    _crear_tenant(raiz, "cerrado", acceso="access_key", contacto_acceso="Escribile a Ariel.")
    _crear_tenant(raiz, "cerrado-b", acceso="access_key")
    _crear_tenant(raiz, "abierto")
    monkeypatch.setattr(tenants_module, "TENANTS_DIR", raiz)
    monkeypatch.setenv(VAR_ADMIN_API_KEY, CLAVE_ADMIN_TEST)
    monkeypatch.setenv(VAR_FRONTEND_URL, FRONT)
    monkeypatch.setenv(sesiones.VAR_SECRETO_SESION, SECRETO)
    monkeypatch.setattr(reindex_module, "_states", {})
    for tenant_id in ("cerrado", "cerrado-b", "abierto"):
        reindex_module.reindex(tenant_id, sample_dir)

    reloj = RelojFalso()
    ruta_claves = tmp_path / "_keys" / "claves.json"
    servicio = ServicioClaves(AlmacenClavesJson(ruta_claves), reloj)
    app.dependency_overrides[obtener_servicio_claves] = lambda: servicio
    app.dependency_overrides[obtener_reloj] = lambda: reloj
    yield Entorno(TestClient(app), reloj, ruta_claves)
    app.dependency_overrides.pop(obtener_servicio_claves, None)
    app.dependency_overrides.pop(obtener_reloj, None)


def _crear(entorno: Entorno, **datos):
    cuerpo = {"tenant_id": "cerrado", "etiqueta": "Ana, Acme", **datos}
    return entorno.cliente.post("/admin/keys", json=cuerpo, headers=HEADERS)


# --- Protección ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [("post", "/admin/keys"), ("get", "/admin/keys"), ("post", "/admin/keys/abcd1234/revoke")],
)
def test_sin_la_api_key_del_admin_se_rechaza_todo(entorno: Entorno, metodo: str, ruta: str):
    sin_clave = getattr(entorno.cliente, metodo)(ruta)
    clave_mala = getattr(entorno.cliente, metodo)(ruta, headers={"X-API-Key": "incorrecta"})

    assert sin_clave.status_code == clave_mala.status_code == 401


def test_sin_api_key_configurada_el_admin_de_keys_queda_deshabilitado(entorno: Entorno, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(VAR_ADMIN_API_KEY)

    assert entorno.cliente.get("/admin/keys", headers=HEADERS).status_code == 503


def test_el_admin_tiene_su_limite_de_pedidos(entorno: Entorno):
    usar_limites(replace(LIMITES_HOLGADOS, admin_por_minuto=2))

    codigos = [entorno.cliente.get("/admin/keys", headers=HEADERS).status_code for _ in range(3)]

    assert codigos == [200, 200, 429]


# --- Crear --------------------------------------------------------------------------


def test_crear_devuelve_la_key_una_vez_y_el_enlace_listo(entorno: Entorno):
    respuesta = _crear(entorno, cupo_preguntas=5)

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["etiqueta"] == "Ana, Acme"
    assert cuerpo["estado"] == "activa"
    assert cuerpo["cupo_preguntas"] == 5 and cuerpo["preguntas_restantes"] == 5
    assert cuerpo["vence_en"].startswith("2026-11-15T12:00:00")
    # El enlace lleva la key en el fragmento (#), no en la query.
    assert cuerpo["enlace"] == f"{FRONT}/?tenant=cerrado&embed=1#k={cuerpo['key']}"
    assert len(cuerpo["key"]) >= 40


def test_la_key_creada_funciona_de_verdad_en_el_acceso(entorno: Entorno):
    key = _crear(entorno).json()["key"]

    canje = entorno.cliente.post("/api/v1/acceso", json={"tenant_id": "cerrado", "key": key})

    assert canje.status_code == 200
    assert canje.json()["etiqueta"] == "Ana, Acme"


def test_el_archivo_guarda_solo_el_hash_nunca_la_key(entorno: Entorno):
    key = _crear(entorno).json()["key"]

    assert key not in entorno.ruta_claves.read_text(encoding="utf-8")


def test_sin_url_del_front_configurada_el_enlace_es_null_pero_la_key_se_crea(entorno: Entorno, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(VAR_FRONTEND_URL)

    cuerpo = _crear(entorno).json()

    assert cuerpo["enlace"] is None
    assert cuerpo["key"]


def test_se_puede_elegir_la_vigencia(entorno: Entorno):
    cuerpo = _crear(entorno, dias_vigencia=3).json()

    assert cuerpo["vence_en"].startswith("2026-11-04T12:00:00")


def test_un_tenant_publico_no_recibe_keys(entorno: Entorno):
    respuesta = _crear(entorno, tenant_id="abierto")

    assert respuesta.status_code == 400
    assert "access_key" in respuesta.json()["detail"]


def test_tenant_inexistente_o_invalido(entorno: Entorno):
    assert _crear(entorno, tenant_id="no-existe").status_code == 404
    assert _crear(entorno, tenant_id="../x").status_code == 422


@pytest.mark.parametrize(
    "datos",
    [{"etiqueta": ""}, {"etiqueta": "   "}, {"etiqueta": "x" * 81}, {"dias_vigencia": 0}, {"dias_vigencia": 366}, {"cupo_preguntas": 0}],
)
def test_datos_invalidos_responden_422_y_no_crean_nada(entorno: Entorno, datos: dict):
    assert _crear(entorno, **datos).status_code == 422

    assert entorno.cliente.get("/admin/keys", headers=HEADERS).json() == []


# --- Listar -------------------------------------------------------------------------


def test_listar_nunca_expone_la_key_ni_su_hash(entorno: Entorno):
    key = _crear(entorno).json()["key"]

    texto = entorno.cliente.get("/admin/keys", headers=HEADERS).text

    assert key not in texto
    assert "hash" not in texto


def test_listar_muestra_estado_y_contadores_de_uso(entorno: Entorno):
    key = _crear(entorno, cupo_preguntas=3).json()["key"]
    token = entorno.cliente.post("/api/v1/acceso", json={"tenant_id": "cerrado", "key": key}).json()["session_token"]
    entorno.reloj.avanzar(minutes=10)
    entorno.cliente.post(
        "/api/v1/chat",
        json={"tenant_id": "cerrado", "message": "¿Qué tecnologías usás?", "mode": "raw"},
        headers={"Authorization": f"Bearer {token}"},
    )

    [fila] = entorno.cliente.get("/admin/keys", headers=HEADERS).json()

    assert fila["estado"] == "activa"
    assert fila["accesos"] == 1
    assert fila["preguntas_usadas"] == 1 and fila["preguntas_restantes"] == 2
    assert fila["ultimo_acceso_en"].startswith("2026-11-01T12:00:00")
    assert fila["ultima_pregunta_en"].startswith("2026-11-01T12:10:00")


def test_listar_filtra_por_tenant_y_ordena_de_la_mas_nueva_a_la_mas_vieja(entorno: Entorno):
    _crear(entorno, etiqueta="primera")
    entorno.reloj.avanzar(hours=1)
    _crear(entorno, etiqueta="segunda")
    _crear(entorno, tenant_id="cerrado-b", etiqueta="ajena")

    todas = entorno.cliente.get("/admin/keys", headers=HEADERS).json()
    solo_cerrado = entorno.cliente.get("/admin/keys?tenant_id=cerrado", headers=HEADERS).json()

    assert len(todas) == 3
    assert [k["etiqueta"] for k in solo_cerrado] == ["segunda", "primera"]
    assert entorno.cliente.get("/admin/keys?tenant_id=no-existe", headers=HEADERS).status_code == 404


def test_el_estado_se_calcula_con_la_hora_actual(entorno: Entorno):
    _crear(entorno, dias_vigencia=1)

    entorno.reloj.avanzar(days=1)
    [fila] = entorno.cliente.get("/admin/keys", headers=HEADERS).json()

    assert fila["estado"] == "vencida"


# --- Revocar ------------------------------------------------------------------------


def test_revocar_corta_el_acceso_y_se_ve_en_la_lista(entorno: Entorno):
    creada = _crear(entorno).json()

    respuesta = entorno.cliente.post(f"/admin/keys/{creada['key_id']}/revoke", headers=HEADERS)

    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "revocada"
    canje = entorno.cliente.post("/api/v1/acceso", json={"tenant_id": "cerrado", "key": creada["key"]})
    assert canje.status_code == 403
    assert canje.json()["detail"]["codigo"] == "key_revocada"


def test_revocar_dos_veces_no_falla_y_revocar_algo_inexistente_responde_404(entorno: Entorno):
    creada = _crear(entorno).json()

    assert entorno.cliente.post(f"/admin/keys/{creada['key_id']}/revoke", headers=HEADERS).status_code == 200
    assert entorno.cliente.post(f"/admin/keys/{creada['key_id']}/revoke", headers=HEADERS).status_code == 200
    assert entorno.cliente.post("/admin/keys/deadbeef/revoke", headers=HEADERS).status_code == 404


def test_revocar_una_key_no_toca_las_demas(entorno: Entorno):
    revocada = _crear(entorno, etiqueta="A").json()
    otra = _crear(entorno, etiqueta="B").json()

    entorno.cliente.post(f"/admin/keys/{revocada['key_id']}/revoke", headers=HEADERS)

    estados = {k["etiqueta"]: k["estado"] for k in entorno.cliente.get("/admin/keys", headers=HEADERS).json()}
    assert estados == {"A": "revocada", "B": "activa"}
    assert entorno.cliente.post("/api/v1/acceso", json={"tenant_id": "cerrado", "key": otra["key"]}).status_code == 200


def test_el_flujo_completo_crear_usar_agotar_y_revocar(entorno: Entorno):
    creada = _crear(entorno, cupo_preguntas=1).json()
    sesion = entorno.cliente.post("/api/v1/acceso", json={"tenant_id": "cerrado", "key": creada["key"]}).json()
    cabecera = {"Authorization": f"Bearer {sesion['session_token']}"}
    pregunta = {"tenant_id": "cerrado", "message": "¿Qué tecnologías usás?", "mode": "raw"}

    assert entorno.cliente.post("/api/v1/chat", json=pregunta, headers=cabecera).status_code == 200
    agotada = entorno.cliente.post("/api/v1/chat", json=pregunta, headers=cabecera)
    assert agotada.json()["detail"]["codigo"] == "cupo_agotado"

    [fila] = entorno.cliente.get("/admin/keys", headers=HEADERS).json()
    assert fila["preguntas_restantes"] == 0
    entorno.cliente.post(f"/admin/keys/{creada['key_id']}/revoke", headers=HEADERS)
    revocada = entorno.cliente.post("/api/v1/chat", json=pregunta, headers=cabecera)
    assert revocada.json()["detail"]["codigo"] == "key_revocada"
