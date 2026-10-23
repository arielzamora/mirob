"""Tests de los límites de costo: configuración, presupuesto diario y el decorador del generador."""

from datetime import datetime, timedelta, timezone

import pytest

from app.finops import (
    EstadoLimites,
    GeneradorConPresupuesto,
    LimiteExcedido,
    Limites,
    LimitesConfigError,
    PresupuestoAgotado,
    PresupuestoDiario,
)
from app.foundry.tipos import FoundryError
from app.tenants import TenantConfig
from tests.conftest import GeneradorFalso


def _tenant(tenant_id: str = "uno", tope: int | None = None) -> TenantConfig:
    return TenantConfig(tenant_id, tenant_id.title(), "Prompt.", 0.2, (), False, tope)


class RelojUtcFalso:
    """Devuelve una fecha/hora UTC que el test puede adelantar."""

    def __init__(self) -> None:
        self.ahora = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.ahora


# --- Limites.from_env ---------------------------------------------------------


def test_los_limites_usan_defaults_si_no_hay_variables() -> None:
    assert Limites.from_env({}) == Limites()


def test_los_limites_se_leen_del_entorno() -> None:
    limites = Limites.from_env(
        {
            "MIROB_RATE_CHAT_PER_MINUTE": "5",
            "MIROB_RATE_FOUNDRY_PER_HOUR": "7",
            "MIROB_RATE_ADMIN_PER_MINUTE": "3",
            "MIROB_DAILY_TOKEN_CAP": "1000",
        }
    )

    assert limites == Limites(chat_por_minuto=5, foundry_por_hora=7, admin_por_minuto=3, tope_diario_global=1000)


@pytest.mark.parametrize("valor", ["0", "-1", "mucho", "1.5"])
def test_un_limite_invalido_falla_con_un_mensaje_claro(valor: str) -> None:
    with pytest.raises(LimitesConfigError, match="MIROB_DAILY_TOKEN_CAP"):
        Limites.from_env({"MIROB_DAILY_TOKEN_CAP": valor})


# --- PresupuestoDiario --------------------------------------------------------


def test_el_presupuesto_corta_al_llegar_al_tope_global() -> None:
    presupuesto = PresupuestoDiario(tope_global=100, ahora=RelojUtcFalso())
    presupuesto.verificar(_tenant())  # con 0 gastado, pasa

    presupuesto.registrar("uno", 100)

    with pytest.raises(PresupuestoAgotado):
        presupuesto.verificar(_tenant())
    # El tope global corta a TODOS los tenants, no solo al que gastó.
    with pytest.raises(PresupuestoAgotado):
        presupuesto.verificar(_tenant("dos"))


def test_el_tope_de_un_tenant_no_corta_a_los_demas() -> None:
    presupuesto = PresupuestoDiario(tope_global=10_000, ahora=RelojUtcFalso())
    presupuesto.registrar("uno", 500)

    with pytest.raises(PresupuestoAgotado):
        presupuesto.verificar(_tenant("uno", tope=500))
    presupuesto.verificar(_tenant("dos", tope=500))  # "dos" no gastó nada
    presupuesto.verificar(_tenant("uno", tope=None))  # sin tope propio, solo rige el global


def test_el_presupuesto_se_reinicia_al_cambiar_el_dia_utc() -> None:
    reloj = RelojUtcFalso()
    presupuesto = PresupuestoDiario(tope_global=100, ahora=reloj)
    presupuesto.registrar("uno", 100)
    with pytest.raises(PresupuestoAgotado):
        presupuesto.verificar(_tenant())

    reloj.ahora += timedelta(days=1)

    presupuesto.verificar(_tenant())
    assert presupuesto.gastado_hoy() == 0


def test_informa_los_segundos_hasta_el_proximo_dia_utc() -> None:
    reloj = RelojUtcFalso()  # 10:00 UTC
    presupuesto = PresupuestoDiario(tope_global=1, ahora=reloj)
    presupuesto.registrar("uno", 1)

    with pytest.raises(PresupuestoAgotado) as info:
        presupuesto.verificar(_tenant())

    assert info.value.reintentar_en == 14 * 3600  # faltan 14 h para las 00:00 UTC


# --- GeneradorConPresupuesto ----------------------------------------------------


def test_el_decorador_delega_y_anota_los_tokens_gastados() -> None:
    estado = EstadoLimites(Limites(tope_diario_global=1000))
    interno = GeneradorFalso("hola")

    resultado = GeneradorConPresupuesto(interno, estado, _tenant(), "1.2.3.4").generar("i", "m", 250)

    assert resultado.texto == "hola"
    assert len(interno.llamadas) == 1
    assert estado.presupuesto.gastado_hoy() == 150  # los tokens_total del GeneradorFalso


def test_con_el_presupuesto_agotado_no_se_llama_al_llm() -> None:
    estado = EstadoLimites(Limites(tope_diario_global=100))
    estado.presupuesto.registrar("uno", 100)
    interno = GeneradorFalso()

    with pytest.raises(PresupuestoAgotado):
        GeneradorConPresupuesto(interno, estado, _tenant(), "1.2.3.4").generar("i", "m")

    assert interno.llamadas == []


def test_el_limite_por_ip_corta_antes_de_llamar_al_llm() -> None:
    estado = EstadoLimites(Limites(foundry_por_hora=1))
    interno = GeneradorFalso()
    generador = GeneradorConPresupuesto(interno, estado, _tenant(), "1.2.3.4")
    generador.generar("i", "m")

    with pytest.raises(LimiteExcedido):
        generador.generar("i", "m")

    assert len(interno.llamadas) == 1


def test_un_error_del_modelo_no_consume_tokens() -> None:
    estado = EstadoLimites(Limites())
    interno = GeneradorFalso(error=FoundryError("falló"))

    with pytest.raises(FoundryError):
        GeneradorConPresupuesto(interno, estado, _tenant(), "1.2.3.4").generar("i", "m")

    assert estado.presupuesto.gastado_hoy() == 0
