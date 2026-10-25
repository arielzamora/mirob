"""Tests de app/accesskeys.py: creación, validación, vencimiento, revocación y persistencia.

Todo corre sobre carpetas temporales (tmp_path) y un reloj falso: nunca se toca backend/data/ real
y no hace falta esperar días para probar un vencimiento.
"""

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.accesskeys import (
    DIAS_VENCIMIENTO_POR_DEFECTO,
    RUTA_POR_DEFECTO,
    AccessKey,
    AlmacenClavesError,
    AlmacenClavesJson,
    ClaveInvalida,
    ClaveRevocada,
    ClaveVencida,
    DatosInvalidos,
    ServicioClaves,
    hashear_clave,
)
from app.reindex import CONTENT_DIR
from app.tenants import TenantInvalido, validar_tenant_id

TENANT = "ariel-zamora"
OTRO_TENANT = "digital-zur"
INICIO = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


class RelojFalso:
    """Reloj que se mueve a mano. Se pasa al servicio como `reloj` (en C#: un FakeTimeProvider)."""

    def __init__(self, ahora: datetime = INICIO) -> None:
        self.ahora = ahora

    def __call__(self) -> datetime:
        return self.ahora

    def avanzar(self, **kwargs) -> None:
        self.ahora += timedelta(**kwargs)


@pytest.fixture
def ruta(tmp_path):
    return tmp_path / "_keys" / "claves.json"


@pytest.fixture
def reloj() -> RelojFalso:
    return RelojFalso()


@pytest.fixture
def servicio(ruta, reloj) -> ServicioClaves:
    return ServicioClaves(AlmacenClavesJson(ruta), reloj)


# --- Crear ---------------------------------------------------------------------


def test_crear_devuelve_la_clave_en_claro_y_guarda_solo_el_hash(servicio, ruta):
    registro, clave = servicio.crear(TENANT, "Ana, Acme Recruiting")

    assert registro.hash_clave == hashear_clave(clave)
    assert registro.tenant_id == TENANT
    assert registro.etiqueta == "Ana, Acme Recruiting"
    # El valor de la key NO está en el disco, solo su hash.
    en_disco = ruta.read_text(encoding="utf-8")
    assert clave not in en_disco
    assert registro.hash_clave in en_disco


def test_la_clave_generada_es_larga_y_distinta_cada_vez(servicio):
    _, primera = servicio.crear(TENANT, "A")
    _, segunda = servicio.crear(TENANT, "B")

    assert len(primera) >= 40
    assert primera != segunda


def test_el_identificador_publico_no_revela_la_clave_y_es_unico(servicio):
    registro_a, clave_a = servicio.crear(TENANT, "A")
    registro_b, _ = servicio.crear(TENANT, "B")

    assert registro_a.key_id != registro_b.key_id
    assert registro_a.key_id not in clave_a


def test_vence_a_los_14_dias_por_defecto(servicio, reloj):
    registro, _ = servicio.crear(TENANT, "A")

    assert registro.creada_en == INICIO
    assert registro.vence_en == INICIO + timedelta(days=DIAS_VENCIMIENTO_POR_DEFECTO)
    assert DIAS_VENCIMIENTO_POR_DEFECTO == 14


def test_se_puede_elegir_otra_vigencia_y_un_cupo(servicio):
    registro, _ = servicio.crear(TENANT, "A", dias_vigencia=3, cupo_preguntas=25)

    assert registro.vence_en == INICIO + timedelta(days=3)
    assert registro.cupo_preguntas == 25
    assert registro.preguntas_usadas == 0


@pytest.mark.parametrize(
    "argumentos",
    [
        {"etiqueta": ""},
        {"etiqueta": "   "},
        {"etiqueta": "x" * 81},
        {"etiqueta": "con\nsalto"},
        {"etiqueta": "ok", "dias_vigencia": 0},
        {"etiqueta": "ok", "dias_vigencia": 366},
        {"etiqueta": "ok", "cupo_preguntas": 0},
        {"etiqueta": "ok", "cupo_preguntas": -5},
    ],
)
def test_datos_invalidos_se_rechazan_y_no_se_guarda_nada(servicio, ruta, argumentos):
    with pytest.raises(DatosInvalidos):
        servicio.crear(TENANT, **argumentos)

    assert not ruta.exists()


@pytest.mark.parametrize("tenant_malo", ["", "../x", "ARIEL", "_keys", "con espacio"])
def test_el_tenant_tiene_que_tener_formato_valido(servicio, tenant_malo):
    with pytest.raises(DatosInvalidos):
        servicio.crear(tenant_malo, "A")


# --- Validar -------------------------------------------------------------------


def test_una_clave_vigente_valida_para_su_tenant(servicio):
    registro, clave = servicio.crear(TENANT, "A")

    assert servicio.validar(clave, TENANT) == registro


@pytest.mark.parametrize("clave", ["", "no-existe", "x" * 500])
def test_una_clave_inexistente_o_basura_es_invalida(servicio, clave):
    servicio.crear(TENANT, "A")

    with pytest.raises(ClaveInvalida):
        servicio.validar(clave, TENANT)


def test_una_clave_de_otro_tenant_responde_igual_que_una_inexistente(servicio):
    _, clave = servicio.crear(TENANT, "A")

    with pytest.raises(ClaveInvalida) as error_otro_tenant:
        servicio.validar(clave, OTRO_TENANT)
    with pytest.raises(ClaveInvalida) as error_inexistente:
        servicio.validar("no-existe", OTRO_TENANT)

    # Mismo tipo y mismo mensaje: no se revela que la clave existe en otro tenant.
    assert str(error_otro_tenant.value) == str(error_inexistente.value)


def test_vence_exactamente_a_los_14_dias(servicio, reloj):
    _, clave = servicio.crear(TENANT, "A")

    reloj.avanzar(days=13, hours=23, minutes=59)
    servicio.validar(clave, TENANT)  # un minuto antes: todavía sirve

    reloj.avanzar(minutes=1)
    with pytest.raises(ClaveVencida):
        servicio.validar(clave, TENANT)


def test_una_clave_revocada_deja_de_servir_aunque_no_haya_vencido(servicio):
    registro, clave = servicio.crear(TENANT, "A")

    servicio.revocar(registro.key_id)

    with pytest.raises(ClaveRevocada):
        servicio.validar(clave, TENANT)


def test_revocar_una_clave_no_afecta_a_las_demas(servicio):
    revocada, _ = servicio.crear(TENANT, "A")
    _, otra_clave = servicio.crear(TENANT, "B")

    servicio.revocar(revocada.key_id)

    servicio.validar(otra_clave, TENANT)


def test_revocar_dos_veces_no_falla_y_revocar_algo_inexistente_si(servicio):
    registro, _ = servicio.crear(TENANT, "A")

    servicio.revocar(registro.key_id)
    assert servicio.revocar(registro.key_id).revocada is True
    with pytest.raises(ClaveInvalida):
        servicio.revocar("deadbeef")


def test_el_estado_prioriza_la_revocacion_sobre_el_vencimiento(servicio, reloj):
    registro, _ = servicio.crear(TENANT, "A")
    revocada = servicio.revocar(registro.key_id)
    reloj.avanzar(days=30)

    assert revocada.estado(reloj()) == "revocada"
    assert registro.estado(reloj()) == "vencida"


# --- Listar --------------------------------------------------------------------


def test_listar_filtra_por_tenant_y_ordena_de_la_mas_nueva_a_la_mas_vieja(servicio, reloj):
    primera, _ = servicio.crear(TENANT, "primera")
    reloj.avanzar(hours=1)
    segunda, _ = servicio.crear(TENANT, "segunda")
    servicio.crear(OTRO_TENANT, "ajena")

    assert [c.key_id for c in servicio.listar(TENANT)] == [segunda.key_id, primera.key_id]
    assert len(servicio.listar()) == 3


# --- Persistencia --------------------------------------------------------------


def test_las_claves_sobreviven_a_un_reinicio(ruta, reloj):
    registro, clave = ServicioClaves(AlmacenClavesJson(ruta), reloj).crear(TENANT, "A", cupo_preguntas=10)

    # Un servicio nuevo con un almacén nuevo sobre el mismo archivo = el backend se reinició.
    reiniciado = ServicioClaves(AlmacenClavesJson(ruta), reloj)

    assert reiniciado.validar(clave, TENANT) == registro


def test_el_archivo_guarda_las_fechas_en_iso_utc(servicio, ruta):
    servicio.crear(TENANT, "A")

    datos = json.loads(ruta.read_text(encoding="utf-8"))["claves"][0]
    assert datos["creada_en"] == "2026-11-01T12:00:00+00:00"
    assert datos["vence_en"] == "2026-11-15T12:00:00+00:00"


def test_no_quedan_archivos_temporales_despues_de_guardar(servicio, ruta):
    servicio.crear(TENANT, "A")
    servicio.crear(TENANT, "B")

    assert [p.name for p in ruta.parent.iterdir()] == ["claves.json"]


def test_un_archivo_corrupto_falla_y_no_se_pisa(servicio, ruta):
    servicio.crear(TENANT, "A")
    ruta.write_text("{ esto no es json", encoding="utf-8")

    with pytest.raises(AlmacenClavesError):
        servicio.listar()
    with pytest.raises(AlmacenClavesError):
        servicio.crear(TENANT, "B")
    # Seguimos viendo el archivo roto tal cual: no se lo reemplazó por uno vacío.
    assert ruta.read_text(encoding="utf-8") == "{ esto no es json"


def test_crear_claves_desde_varios_hilos_no_pierde_ninguna(servicio):
    def crear(numero: int) -> None:
        servicio.crear(TENANT, f"hilo {numero}")

    hilos = [threading.Thread(target=crear, args=(n,)) for n in range(20)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    assert len(servicio.listar()) == 20


# --- Ubicación del archivo -----------------------------------------------------


def test_el_archivo_real_vive_junto_al_contenido_y_no_choca_con_ningun_tenant():
    assert RUTA_POR_DEFECTO == CONTENT_DIR / "tenants" / "_keys" / "claves.json"
    # Ningún tenant_id válido empieza con "_", así que la carpeta _keys nunca es la de un tenant.
    with pytest.raises(TenantInvalido):
        validar_tenant_id("_keys")


def test_el_registro_no_expone_la_clave_ni_en_su_representacion():
    registro = AccessKey(
        key_id="abcd1234",
        tenant_id=TENANT,
        etiqueta="A",
        hash_clave=hashear_clave("secreta"),
        creada_en=INICIO,
        vence_en=INICIO + timedelta(days=1),
    )

    assert "secreta" not in repr(registro)
