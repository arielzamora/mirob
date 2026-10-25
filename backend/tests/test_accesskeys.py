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
    CupoAgotado,
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


# --- Contadores y cupo (grupo C) ------------------------------------------------


def test_consumir_pregunta_suma_una_y_guarda_cuando_fue(servicio, reloj):
    registro, _ = servicio.crear(TENANT, "A", cupo_preguntas=3)
    reloj.avanzar(minutes=5)

    actualizada = servicio.consumir_pregunta(registro.key_id, TENANT)

    assert actualizada.preguntas_usadas == 1
    assert actualizada.preguntas_restantes() == 2
    assert actualizada.ultima_pregunta_en == INICIO + timedelta(minutes=5)


def test_con_el_cupo_agotado_ya_no_se_puede_preguntar_ni_abrir_sesion(servicio):
    registro, clave = servicio.crear(TENANT, "A", cupo_preguntas=2)
    servicio.consumir_pregunta(registro.key_id, TENANT)
    servicio.consumir_pregunta(registro.key_id, TENANT)

    with pytest.raises(CupoAgotado):
        servicio.consumir_pregunta(registro.key_id, TENANT)
    with pytest.raises(CupoAgotado):
        servicio.validar(clave, TENANT)
    # Un pedido rechazado por cupo no suma nada.
    assert servicio.listar(TENANT)[0].preguntas_usadas == 2


def test_sin_cupo_propio_igual_se_cuentan_las_preguntas(servicio):
    registro, _ = servicio.crear(TENANT, "A")

    for _ in range(5):
        actualizada = servicio.consumir_pregunta(registro.key_id, TENANT)

    assert actualizada.preguntas_usadas == 5
    assert actualizada.preguntas_restantes() is None
    assert actualizada.cupo_agotado() is False


def test_consumir_exige_key_vigente_del_mismo_tenant(servicio, reloj):
    revocada, _ = servicio.crear(TENANT, "A", cupo_preguntas=5)
    vencida, _ = servicio.crear(TENANT, "B", cupo_preguntas=5, dias_vigencia=1)
    servicio.revocar(revocada.key_id)
    reloj.avanzar(days=1)

    with pytest.raises(ClaveRevocada):
        servicio.consumir_pregunta(revocada.key_id, TENANT)
    with pytest.raises(ClaveVencida):
        servicio.consumir_pregunta(vencida.key_id, TENANT)
    with pytest.raises(ClaveInvalida):
        servicio.consumir_pregunta(vencida.key_id, OTRO_TENANT)
    with pytest.raises(ClaveInvalida):
        servicio.consumir_pregunta("deadbeef", TENANT)


def test_registrar_acceso_cuenta_las_entradas_y_la_ultima_fecha(servicio, reloj):
    registro, _ = servicio.crear(TENANT, "A")
    assert registro.accesos == 0 and registro.ultimo_acceso_en is None

    servicio.registrar_acceso(registro.key_id)
    reloj.avanzar(hours=3)
    actualizada = servicio.registrar_acceso(registro.key_id)

    assert actualizada.accesos == 2
    assert actualizada.ultimo_acceso_en == INICIO + timedelta(hours=3)
    with pytest.raises(ClaveInvalida):
        servicio.registrar_acceso("deadbeef")


def test_los_contadores_sobreviven_a_un_reinicio(ruta, reloj):
    primero = ServicioClaves(AlmacenClavesJson(ruta), reloj)
    registro, _ = primero.crear(TENANT, "A", cupo_preguntas=10)
    primero.registrar_acceso(registro.key_id)
    primero.consumir_pregunta(registro.key_id, TENANT)

    reiniciado = ServicioClaves(AlmacenClavesJson(ruta), reloj).listar(TENANT)[0]

    assert (reiniciado.accesos, reiniciado.preguntas_usadas) == (1, 1)
    assert reiniciado.ultima_pregunta_en == INICIO and reiniciado.ultimo_acceso_en == INICIO


def test_un_archivo_anterior_a_los_contadores_se_sigue_leyendo(ruta, reloj):
    # Formato del grupo A: sin accesos, sin fechas de uso.
    ruta.parent.mkdir(parents=True)
    ruta.write_text(
        json.dumps(
            {
                "claves": [
                    {
                        "key_id": "abcd1234",
                        "tenant_id": TENANT,
                        "etiqueta": "Vieja",
                        "hash_clave": hashear_clave("x"),
                        "creada_en": INICIO.isoformat(),
                        "vence_en": (INICIO + timedelta(days=14)).isoformat(),
                        "revocada": False,
                        "cupo_preguntas": 7,
                        "preguntas_usadas": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    clave = ServicioClaves(AlmacenClavesJson(ruta), reloj).listar()[0]

    assert (clave.preguntas_usadas, clave.accesos, clave.ultimo_acceso_en) == (2, 0, None)
    assert clave.preguntas_restantes() == 5


def test_el_cupo_no_se_pasa_aunque_haya_muchos_hilos_a_la_vez(servicio):
    registro, _ = servicio.crear(TENANT, "A", cupo_preguntas=10)
    resultados: list[str] = []

    def preguntar() -> None:
        try:
            servicio.consumir_pregunta(registro.key_id, TENANT)
            resultados.append("ok")
        except CupoAgotado:
            resultados.append("agotado")

    hilos = [threading.Thread(target=preguntar) for _ in range(40)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()

    # Exactamente 10 pasaron: ninguna pisó la cuenta de otra.
    assert resultados.count("ok") == 10
    assert resultados.count("agotado") == 30
    assert servicio.listar(TENANT)[0].preguntas_usadas == 10


def test_si_la_transformacion_falla_no_se_guarda_nada(servicio, ruta):
    registro, _ = servicio.crear(TENANT, "A")
    antes = ruta.read_text(encoding="utf-8")
    almacen = AlmacenClavesJson(ruta)

    def falla(_clave):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        almacen.modificar(registro.key_id, falla)

    assert ruta.read_text(encoding="utf-8") == antes
    assert almacen.modificar("inexistente", lambda c: c) is None


def test_el_archivo_guarda_solo_numeros_y_fechas_de_uso(servicio, ruta):
    registro, _ = servicio.crear(TENANT, "A", cupo_preguntas=5)
    servicio.registrar_acceso(registro.key_id)
    servicio.consumir_pregunta(registro.key_id, TENANT)

    datos = json.loads(ruta.read_text(encoding="utf-8"))["claves"][0]

    assert set(datos) == {
        "key_id", "tenant_id", "etiqueta", "hash_clave", "creada_en", "vence_en", "revocada",
        "cupo_preguntas", "preguntas_usadas", "accesos", "ultimo_acceso_en", "ultima_pregunta_en",
    }  # fmt: skip
