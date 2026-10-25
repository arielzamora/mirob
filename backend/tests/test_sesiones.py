"""Tests de app/sesiones.py: token firmado, vencimiento, alteración y configuración."""

from datetime import datetime, timedelta, timezone

import pytest

from app import sesiones
from app.accesskeys import AccessKey, hashear_clave
from app.sesiones import ConfigSesionError, SesionExpirada, SesionInvalida

SECRETO = b"un-secreto-de-prueba-de-al-menos-32-caracteres"
AHORA = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _clave(vence_en: datetime = AHORA + timedelta(days=14), tenant_id: str = "ariel-zamora") -> AccessKey:
    return AccessKey(
        key_id="abcd1234",
        tenant_id=tenant_id,
        etiqueta="Ana",
        hash_clave=hashear_clave("la-key"),
        creada_en=AHORA,
        vence_en=vence_en,
    )


def test_emitir_y_leer_devuelve_los_mismos_datos():
    token, expira_en = sesiones.emitir(_clave(), SECRETO, AHORA, timedelta(hours=2))

    sesion = sesiones.leer(token, SECRETO, AHORA)

    assert sesion.key_id == "abcd1234"
    assert sesion.tenant_id == "ariel-zamora"
    assert sesion.expira_en == expira_en == AHORA + timedelta(hours=2)


def test_el_token_no_contiene_la_key_ni_su_hash():
    token, _ = sesiones.emitir(_clave(), SECRETO, AHORA, timedelta(hours=2))

    assert "la-key" not in token
    assert hashear_clave("la-key") not in token
    assert token.startswith("v1.")


def test_la_sesion_nunca_dura_mas_que_la_key():
    clave = _clave(vence_en=AHORA + timedelta(minutes=30))

    _, expira_en = sesiones.emitir(clave, SECRETO, AHORA, timedelta(hours=2))

    assert expira_en == AHORA + timedelta(minutes=30)


def test_una_sesion_vencida_se_distingue_de_una_invalida():
    token, _ = sesiones.emitir(_clave(), SECRETO, AHORA, timedelta(hours=2))

    sesiones.leer(token, SECRETO, AHORA + timedelta(hours=1, minutes=59))
    with pytest.raises(SesionExpirada):
        sesiones.leer(token, SECRETO, AHORA + timedelta(hours=2))


def test_alterar_los_datos_invalida_la_firma():
    token, _ = sesiones.emitir(_clave(), SECRETO, AHORA, timedelta(hours=2))
    otra, _ = sesiones.emitir(_clave(tenant_id="digital-zur"), SECRETO, AHORA, timedelta(hours=2))
    version, _, firma = token.split(".")
    _, cuerpo_ajeno, _ = otra.split(".")

    # Los datos de OTRA sesión con la firma de esta: no coinciden.
    with pytest.raises(SesionInvalida):
        sesiones.leer(f"{version}.{cuerpo_ajeno}.{firma}", SECRETO, AHORA)


def test_firmar_con_otro_secreto_no_vale():
    token, _ = sesiones.emitir(_clave(), b"otro-secreto-distinto-de-32-caracteres!!", AHORA, timedelta(hours=2))

    with pytest.raises(SesionInvalida):
        sesiones.leer(token, SECRETO, AHORA)


@pytest.mark.parametrize(
    "token",
    ["", "basura", "v1.solo-dos", "v2.a.b", "v1.a.b.c", "v1..", "Bearer v1.a.b"],
)
def test_los_tokens_mal_formados_son_invalidos(token):
    with pytest.raises(SesionInvalida):
        sesiones.leer(token, SECRETO, AHORA)


# --- Configuración -------------------------------------------------------------


def test_sin_secreto_o_con_secreto_corto_falla_cerrado():
    with pytest.raises(ConfigSesionError):
        sesiones.leer_secreto({})
    with pytest.raises(ConfigSesionError):
        sesiones.leer_secreto({sesiones.VAR_SECRETO_SESION: "corto"})
    assert sesiones.leer_secreto({sesiones.VAR_SECRETO_SESION: "x" * 32}) == b"x" * 32


def test_la_duracion_por_defecto_son_dos_horas_y_se_puede_cambiar():
    assert sesiones.duracion_de_sesion({}) == timedelta(hours=2)
    assert sesiones.duracion_de_sesion({sesiones.VAR_MINUTOS_SESION: "45"}) == timedelta(minutes=45)


@pytest.mark.parametrize("valor", ["0", "-5", "abc"])
def test_una_duracion_invalida_falla(valor):
    with pytest.raises(ConfigSesionError):
        sesiones.duracion_de_sesion({sesiones.VAR_MINUTOS_SESION: valor})
