"""Tests del precalentamiento: se prueba con piezas falsas, sin Azure ni contenido real ni red."""

import pytest

import app.foundry.generador as generador_module
from app.foundry.config import FoundryConfigError, FoundrySettings
from app.foundry.generador import FoundryGenerador
from app.precalentamiento import precalentar, precalentar_en_segundo_plano


class GeneradorPrecalentable:
    """Un generador falso que registra si lo precalentaron."""

    def __init__(self, error: Exception | None = None) -> None:
        self.precalentado = False
        self._error = error

    def precalentar(self) -> None:
        if self._error is not None:
            raise self._error
        self.precalentado = True


def test_arma_el_indice_de_cada_tenant_y_precalienta_al_generador() -> None:
    armados: list[str] = []
    generador = GeneradorPrecalentable()

    precalentar(["ariel-zamora", "digital-zur"], armados.append, generador)

    assert armados == ["ariel-zamora", "digital-zur"]
    assert generador.precalentado is True


def test_un_tenant_que_falla_no_impide_precalentar_los_demas() -> None:
    armados: list[str] = []

    def armar(tenant_id: str) -> None:
        if tenant_id == "roto":
            raise ValueError("contenido inválido")
        armados.append(tenant_id)

    generador = GeneradorPrecalentable()
    precalentar(["roto", "digital-zur"], armar, generador)

    assert armados == ["digital-zur"]
    assert generador.precalentado is True


def test_un_generador_sin_precalentar_se_ignora() -> None:
    # Los falsos de los tests (y cualquier GeneradorTexto) no tienen precalentar(): no debe fallar.
    precalentar(["demo"], lambda _: None, object())


def test_si_el_generador_falla_al_precalentar_no_propaga_el_error() -> None:
    precalentar([], lambda _: None, GeneradorPrecalentable(error=RuntimeError("sin red")))


def test_en_segundo_plano_corre_en_otro_hilo_y_termina() -> None:
    armados: list[str] = []

    hilo = precalentar_en_segundo_plano(["digital-zur"], armados.append, GeneradorPrecalentable())
    hilo.join(timeout=5)

    assert not hilo.is_alive()
    assert armados == ["digital-zur"]


# --- FoundryGenerador.precalentar ---------------------------------------------------


def test_foundry_precalentar_crea_el_cliente_y_pide_el_token_sin_llamar_al_modelo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CredencialFalsa:
        pidio_alcance: str | None = None

        def get_token(self, alcance: str) -> None:
            CredencialFalsa.pidio_alcance = alcance

    monkeypatch.setattr(generador_module, "crear_credencial", lambda: CredencialFalsa())
    monkeypatch.setattr(generador_module, "crear_cliente_proyecto", lambda settings, credencial: object())
    monkeypatch.setattr(generador_module, "obtener_cliente_openai", lambda proyecto: object())
    settings = FoundrySettings(endpoint="https://x.services.ai.azure.com/api/projects/p", chat_deployment="d")

    FoundryGenerador(settings).precalentar()

    assert CredencialFalsa.pidio_alcance == "https://ai.azure.com/.default"


def test_foundry_precalentar_sin_configuracion_no_lanza(monkeypatch: pytest.MonkeyPatch) -> None:
    def sin_config(cls, env=None):
        raise FoundryConfigError("Faltan variables de entorno: FOUNDRY_ENDPOINT")

    monkeypatch.setattr(FoundrySettings, "from_env", classmethod(sin_config))

    FoundryGenerador().precalentar()  # solo avisa en el log
