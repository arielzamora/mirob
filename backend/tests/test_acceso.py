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
    ruta_claves: Path


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
    ruta_claves = tmp_path / "_keys" / "claves.json"
    servicio = ServicioClaves(AlmacenClavesJson(ruta_claves), reloj)
    app.dependency_overrides[obtener_servicio_claves] = lambda: servicio
    app.dependency_overrides[obtener_reloj] = lambda: reloj
    yield Entorno(TestClient(app), servicio, reloj, ruta_claves)
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


# --- Cupo y límites por key (grupo C) -----------------------------------------------------------


def _usadas(entorno: Entorno, tenant_id: str = "cerrado") -> int:
    return entorno.servicio.listar(tenant_id)[0].preguntas_usadas


def test_cada_pregunta_cuenta_y_la_respuesta_dice_cuantas_quedan(entorno: Entorno, generador_falso):
    _, key = entorno.servicio.crear("cerrado", "Ana", cupo_preguntas=3)
    token = _canjear(entorno, key).json()["session_token"]

    restantes = [_chat(entorno, token=token, modo="foundry").json()["remaining_questions"] for _ in range(3)]

    assert restantes == [2, 1, 0]
    assert _usadas(entorno) == 3


def test_con_el_cupo_agotado_el_chat_responde_403_con_contacto_y_no_gasta_tokens(entorno: Entorno, generador_falso):
    _, key = entorno.servicio.crear("cerrado", "Ana", cupo_preguntas=1)
    token = _canjear(entorno, key).json()["session_token"]
    _chat(entorno, token=token, modo="foundry")
    llamadas_antes = len(generador_falso.llamadas)

    respuesta = _chat(entorno, token=token, modo="foundry")

    assert respuesta.status_code == 403
    assert _codigo(respuesta) == "cupo_agotado"
    assert CONTACTO in respuesta.json()["detail"]["mensaje"]
    assert len(generador_falso.llamadas) == llamadas_antes  # la pregunta rechazada no llegó al modelo
    assert _usadas(entorno) == 1


def test_sin_cupo_propio_la_respuesta_no_trae_restantes_pero_se_cuenta(entorno: Entorno):
    token = _sesion(entorno)

    cuerpo = _chat(entorno, token=token).json()

    assert cuerpo["remaining_questions"] is None
    assert _usadas(entorno) == 1


def test_un_tenant_publico_no_cuenta_ni_informa_restantes(entorno: Entorno):
    cuerpo = _chat(entorno, "abierto").json()

    assert cuerpo["remaining_questions"] is None


def test_un_pedido_rechazado_por_falta_de_sesion_no_gasta_cupo(entorno: Entorno):
    _, key = entorno.servicio.crear("cerrado", "Ana", cupo_preguntas=1)
    token = _canjear(entorno, key).json()["session_token"]

    assert _chat(entorno).status_code == 401  # sin sesión
    assert _usadas(entorno) == 0
    assert _chat(entorno, token=token).status_code == 200


def test_si_el_tenant_no_tiene_contenido_la_pregunta_no_gasta_cupo(entorno: Entorno):
    _crear_tenant(tenants_module.TENANTS_DIR, "vacio", acceso="access_key")
    _, key = entorno.servicio.crear("vacio", "Ana", cupo_preguntas=1)
    token = _canjear(entorno, key, "vacio").json()["session_token"]

    assert _chat(entorno, "vacio", token=token).status_code == 503
    assert _usadas(entorno, "vacio") == 0


def test_una_key_no_puede_pasar_su_limite_por_minuto_y_la_rafaga_no_gasta_cupo(entorno: Entorno):
    usar_limites(replace(LIMITES_HOLGADOS, key_por_minuto=2))
    _, key = entorno.servicio.crear("cerrado", "Ana", cupo_preguntas=10)
    token = _canjear(entorno, key).json()["session_token"]

    codigos = [_chat(entorno, token=token).status_code for _ in range(4)]

    assert codigos == [200, 200, 429, 429]
    assert _usadas(entorno) == 2  # solo las respondidas gastaron cupo


def test_el_limite_por_key_es_independiente_entre_keys(entorno: Entorno):
    usar_limites(replace(LIMITES_HOLGADOS, key_por_minuto=1))
    token_a = _sesion(entorno)
    token_b = _sesion(entorno)

    assert _chat(entorno, token=token_a).status_code == 200
    assert _chat(entorno, token=token_a).status_code == 429
    assert _chat(entorno, token=token_b).status_code == 200


def test_abrir_el_enlace_con_el_cupo_agotado_avisa_de_entrada(entorno: Entorno):
    registro, key = entorno.servicio.crear("cerrado", "Ana", cupo_preguntas=1)
    entorno.servicio.consumir_pregunta(registro.key_id, "cerrado")

    respuesta = _canjear(entorno, key)

    assert respuesta.status_code == 403
    assert _codigo(respuesta) == "cupo_agotado"


def test_el_canje_cuenta_accesos_e_informa_las_preguntas_restantes(entorno: Entorno):
    registro, key = entorno.servicio.crear("cerrado", "Ana", cupo_preguntas=5)

    primero = _canjear(entorno, key).json()
    entorno.reloj.avanzar(hours=1)
    _canjear(entorno, key)

    guardada = entorno.servicio.listar("cerrado")[0]
    assert primero["remaining_questions"] == 5
    assert guardada.accesos == 2
    assert guardada.ultimo_acceso_en == INICIO + timedelta(hours=1)


def test_nunca_se_guarda_el_texto_de_las_preguntas(entorno: Entorno):
    token = _sesion(entorno)
    entorno.cliente.post(
        "/api/v1/chat",
        json={"tenant_id": "cerrado", "message": "mi dato secreto 12345 quiero saber", "mode": "raw"},
        headers={"Authorization": f"Bearer {token}"},
    )

    en_disco = entorno.ruta_claves.read_text(encoding="utf-8")

    assert "secreto" not in en_disco and "12345" not in en_disco


# --- Configuración real versionada ------------------------------------------------------------------


def test_la_configuracion_real_de_los_tenants_define_quien_exige_key():
    from app.tenants import obtener_tenant

    # ariel-zamora tiene el contenido para reclutadores: nunca debe quedar público por un cambio de config.
    assert obtener_tenant("ariel-zamora").acceso == "access_key"
    assert obtener_tenant("digital-zur").acceso == "publico"
    assert obtener_tenant("demo").acceso == "publico"
