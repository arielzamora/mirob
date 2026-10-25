"""Tests del acceso con access key: POST /api/v1/acceso y la guardia del chat.

Tenants y keys viven en carpetas temporales, el reloj es falso y el LLM también: sin red, sin tokens
y sin tocar backend/data/ real.
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
from app.main import app
from app.tenants import TenantConfigError, _cargar_config
from tests.conftest import LIMITES_HOLGADOS, usar_limites

SECRETO = "un-secreto-de-prueba-de-al-menos-32-caracteres"
INICIO = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
CONTACTO = "Escribile a Ariel por LinkedIn para pedir otra."


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
    datos = {"nombre": tenant_id.title(), **extra}
    (carpeta / "tenant.json").write_text(json.dumps(datos), encoding="utf-8")
    (carpeta / "prompt.md").write_text("Prompt de prueba.", encoding="utf-8")


@dataclass
class Entorno:
    cliente: TestClient
    servicio: ServicioClaves
    reloj: RelojFalso


@pytest.fixture
def entorno(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sample_dir: Path, generador_falso) -> Entorno:
    """Tres tenants: "cerrado" (con contacto), "cerrado-b" (sin contacto) y "abierto" (público)."""
    raiz = tmp_path / "tenants"
    _crear_tenant(raiz, "cerrado", acceso="access_key", contacto_acceso=CONTACTO)
    _crear_tenant(raiz, "cerrado-b", acceso="access_key")
    _crear_tenant(raiz, "abierto")
    monkeypatch.setattr(tenants_module, "TENANTS_DIR", raiz)
    monkeypatch.setenv(sesiones.VAR_SECRETO_SESION, SECRETO)
    monkeypatch.delenv(sesiones.VAR_MINUTOS_SESION, raising=False)

    # Los tres usan el perfil ficticio como contenido: el chat responde sin tocar data/ real.
    monkeypatch.setattr(reindex_module, "_states", {})
    for tenant_id in ("cerrado", "cerrado-b", "abierto"):
        reindex_module.reindex(tenant_id, sample_dir)

    reloj = RelojFalso()
    servicio = ServicioClaves(AlmacenClavesJson(tmp_path / "_keys" / "claves.json"), reloj)
    app.dependency_overrides[obtener_servicio_claves] = lambda: servicio
    app.dependency_overrides[obtener_reloj] = lambda: reloj
    yield Entorno(TestClient(app), servicio, reloj)
    app.dependency_overrides.pop(obtener_servicio_claves, None)
    app.dependency_overrides.pop(obtener_reloj, None)


def _canjear(entorno: Entorno, key: str, tenant_id: str = "cerrado"):
    return entorno.cliente.post("/api/v1/acceso", json={"tenant_id": tenant_id, "key": key})


def _chat(entorno: Entorno, tenant_id: str = "cerrado", token: str | None = None, modo: str = "raw"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return entorno.cliente.post(
        "/api/v1/chat", json={"tenant_id": tenant_id, "message": "¿Qué tecnologías usás?", "mode": modo}, headers=headers
    )


def _sesion(entorno: Entorno, tenant_id: str = "cerrado") -> str:
    _, key = entorno.servicio.crear(tenant_id, "Ana")
    return _canjear(entorno, key, tenant_id).json()["session_token"]


def _codigo(respuesta) -> str:
    return respuesta.json()["detail"]["codigo"]


# --- Canje ----------------------------------------------------------------------------


def test_una_key_vigente_se_canjea_por_una_sesion(entorno: Entorno):
    registro, key = entorno.servicio.crear("cerrado", "Ana, Acme")

    respuesta = _canjear(entorno, key)

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["tenant_id"] == "cerrado"
    assert cuerpo["etiqueta"] == "Ana, Acme"
    assert cuerpo["expires_in"] == 2 * 3600
    assert cuerpo["key_expires_at"].startswith("2026-11-15T12:00:00")
    assert key not in respuesta.text  # la key en claro no vuelve en la respuesta
    assert registro.hash_clave not in respuesta.text


def test_la_duracion_de_la_sesion_se_configura(entorno: Entorno, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(sesiones.VAR_MINUTOS_SESION, "30")
    _, key = entorno.servicio.crear("cerrado", "Ana")

    assert _canjear(entorno, key).json()["expires_in"] == 30 * 60


def test_una_key_inexistente_responde_401_key_invalida(entorno: Entorno):
    respuesta = _canjear(entorno, "no-existe")

    assert respuesta.status_code == 401
    assert _codigo(respuesta) == "key_invalida"


def test_una_key_de_otro_tenant_responde_igual_que_una_inexistente(entorno: Entorno):
    _, key_de_b = entorno.servicio.crear("cerrado-b", "Ana")

    ajena = _canjear(entorno, key_de_b, "cerrado")
    inexistente = _canjear(entorno, "no-existe", "cerrado")

    assert ajena.status_code == inexistente.status_code == 401
    assert ajena.json() == inexistente.json()


def test_una_key_vencida_responde_403_con_los_datos_de_contacto(entorno: Entorno):
    _, key = entorno.servicio.crear("cerrado", "Ana")
    entorno.reloj.avanzar(days=14)

    respuesta = _canjear(entorno, key)

    assert respuesta.status_code == 403
    assert _codigo(respuesta) == "key_vencida"
    assert CONTACTO in respuesta.json()["detail"]["mensaje"]


def test_una_key_revocada_responde_403_y_sin_contacto_propio_usa_el_mensaje_generico(entorno: Entorno):
    registro, key = entorno.servicio.crear("cerrado-b", "Ana")
    entorno.servicio.revocar(registro.key_id)

    respuesta = _canjear(entorno, key, "cerrado-b")

    assert respuesta.status_code == 403
    assert _codigo(respuesta) == "key_revocada"
    assert "quien te la envió" in respuesta.json()["detail"]["mensaje"]


def test_un_tenant_publico_no_se_canjea(entorno: Entorno):
    respuesta = _canjear(entorno, "lo-que-sea", "abierto")

    assert respuesta.status_code == 400
    assert _codigo(respuesta) == "tenant_publico"


def test_tenant_inexistente_o_invalido(entorno: Entorno):
    assert _canjear(entorno, "k", "no-existe").status_code == 404
    assert _canjear(entorno, "k", "../x").status_code == 422


def test_una_key_gigante_se_rechaza_sin_procesarla(entorno: Entorno):
    assert _canjear(entorno, "x" * 500).status_code == 422


def test_probar_keys_al_azar_se_frena_con_429(entorno: Entorno):
    usar_limites(replace(LIMITES_HOLGADOS, acceso_por_minuto=2))

    codigos = [_canjear(entorno, f"intento-{n}").status_code for n in range(4)]

    assert codigos == [401, 401, 429, 429]


def test_sin_secreto_de_sesion_el_acceso_queda_deshabilitado(entorno: Entorno, monkeypatch: pytest.MonkeyPatch):
    _, key = entorno.servicio.crear("cerrado", "Ana")
    monkeypatch.delenv(sesiones.VAR_SECRETO_SESION)

    respuesta = _canjear(entorno, key)

    assert respuesta.status_code == 503
    assert _codigo(respuesta) == "acceso_no_configurado"


# --- Chat protegido ---------------------------------------------------------------------


def test_el_chat_de_un_tenant_cerrado_exige_sesion_y_no_gasta_tokens(entorno: Entorno, generador_falso):
    respuesta = _chat(entorno, modo="foundry")

    assert respuesta.status_code == 401
    assert _codigo(respuesta) == "acceso_requerido"
    assert generador_falso.llamadas == []  # sin sesión no se llegó al modelo


def test_con_sesion_valida_el_chat_responde_en_raw_y_en_foundry(entorno: Entorno, generador_falso):
    token = _sesion(entorno)

    assert _chat(entorno, token=token).status_code == 200
    assert _chat(entorno, token=token, modo="foundry").status_code == 200
    assert len(generador_falso.llamadas) == 1


def test_un_tenant_publico_sigue_abierto_con_o_sin_token(entorno: Entorno):
    assert _chat(entorno, "abierto").status_code == 200
    assert _chat(entorno, "abierto", token="cualquier-cosa").status_code == 200


def test_la_sesion_de_un_tenant_no_sirve_en_otro(entorno: Entorno):
    token_de_cerrado = _sesion(entorno, "cerrado")

    respuesta = _chat(entorno, "cerrado-b", token=token_de_cerrado)

    assert respuesta.status_code == 401
    assert _codigo(respuesta) == "acceso_requerido"


@pytest.mark.parametrize("autorizacion", ["Basic abc", "Bearer", "Bearer    ", "v1.a.b", "Bearer v1.a.b"])
def test_headers_raros_o_tokens_falsos_se_rechazan(entorno: Entorno, autorizacion: str):
    respuesta = entorno.cliente.post(
        "/api/v1/chat",
        json={"tenant_id": "cerrado", "message": "hola", "mode": "raw"},
        headers={"Authorization": autorizacion},
    )

    assert respuesta.status_code == 401
    assert _codigo(respuesta) == "acceso_requerido"


def test_un_token_alterado_se_rechaza(entorno: Entorno):
    token = _sesion(entorno)
    version, cuerpo, firma = token.split(".")
    alterado = f"{version}.{cuerpo}.{'A' * len(firma)}"

    assert _chat(entorno, token=alterado).status_code == 401


def test_la_sesion_vence_y_pide_abrir_de_nuevo_el_enlace(entorno: Entorno):
    token = _sesion(entorno)

    entorno.reloj.avanzar(hours=2)
    respuesta = _chat(entorno, token=token)

    assert respuesta.status_code == 401
    assert _codigo(respuesta) == "sesion_vencida"


def test_revocar_la_key_corta_el_chat_al_instante(entorno: Entorno):
    registro, key = entorno.servicio.crear("cerrado", "Ana")
    token = _canjear(entorno, key).json()["session_token"]
    assert _chat(entorno, token=token).status_code == 200

    entorno.servicio.revocar(registro.key_id)
    respuesta = _chat(entorno, token=token)

    assert respuesta.status_code == 403
    assert _codigo(respuesta) == "key_revocada"
    assert CONTACTO in respuesta.json()["detail"]["mensaje"]


def test_la_sesion_se_corta_cuando_vence_la_key(entorno: Entorno, monkeypatch: pytest.MonkeyPatch):
    # Sesiones de 48 horas contra una key de 1 día: la sesión queda topada al vencimiento de la key.
    monkeypatch.setenv(sesiones.VAR_MINUTOS_SESION, str(48 * 60))
    _, key = entorno.servicio.crear("cerrado", "Ana", dias_vigencia=1)
    canje = _canjear(entorno, key).json()
    token = canje["session_token"]
    assert canje["expires_in"] == 24 * 3600  # no 48 horas

    entorno.reloj.avanzar(days=1)
    respuesta = _chat(entorno, token=token)

    assert respuesta.status_code == 401
    assert _codigo(respuesta) == "sesion_vencida"


def test_sin_secreto_el_chat_cerrado_responde_503_pero_el_publico_sigue(entorno: Entorno, monkeypatch: pytest.MonkeyPatch):
    token = _sesion(entorno)
    monkeypatch.delenv(sesiones.VAR_SECRETO_SESION)

    assert _chat(entorno, token=token).status_code == 503
    assert _chat(entorno, "abierto").status_code == 200


# --- Lista de tenants y configuración -------------------------------------------------------


def test_la_lista_de_tenants_dice_cuales_piden_key(entorno: Entorno):
    tenants = {t["tenant_id"]: t["acceso"] for t in entorno.cliente.get("/api/v1/tenants").json()}

    assert tenants == {"abierto": "publico", "cerrado": "access_key", "cerrado-b": "access_key"}


def test_la_lista_publica_no_revela_el_contacto(entorno: Entorno):
    assert CONTACTO not in entorno.cliente.get("/api/v1/tenants").text


def test_una_configuracion_de_acceso_invalida_se_rechaza(tmp_path: Path):
    _crear_tenant(tmp_path, "mal-acceso", acceso="cualquiera")
    _crear_tenant(tmp_path, "mal-contacto", acceso="access_key", contacto_acceso="x" * 301)

    with pytest.raises(TenantConfigError):
        _cargar_config(tmp_path / "mal-acceso")
    with pytest.raises(TenantConfigError):
        _cargar_config(tmp_path / "mal-contacto")


def test_el_acceso_por_defecto_es_publico_y_el_contacto_en_blanco_no_cuenta(tmp_path: Path):
    _crear_tenant(tmp_path, "sin-acceso")
    _crear_tenant(tmp_path, "en-blanco", acceso="access_key", contacto_acceso="   ")

    assert _cargar_config(tmp_path / "sin-acceso")[0].acceso == "publico"
    assert _cargar_config(tmp_path / "en-blanco")[0].contacto_acceso is None
