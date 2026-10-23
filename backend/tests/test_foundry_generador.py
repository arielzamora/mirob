"""Tests de la capa Foundry (chat.py y generador.py) con clientes falsos: sin red ni Azure."""

from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

import app.foundry.generador as generador_module
from app.foundry.chat import generar
from app.foundry.config import FoundryConfigError, FoundrySettings
from app.foundry.generador import FoundryGenerador
from app.foundry.tipos import ChatResult, FoundryError, FoundryNoConfigurado

SETTINGS = FoundrySettings(endpoint="https://x.services.ai.azure.com/api/projects/p", chat_deployment="mi-gpt-4o-mini")


class ClienteOpenAIFalso:
    """Imita lo mínimo de openai.OpenAI que usa chat.generar: responses.create(...)."""

    def __init__(self, usage=None, status: str = "completed", incompleta_por: str | None = None) -> None:
        self.kwargs: dict = {}
        self._usage = usage
        self._status = status
        self._detalles = SimpleNamespace(reason=incompleta_por) if incompleta_por else None
        # SimpleNamespace arma un objeto con atributos al vuelo (como un tipo anónimo de C#).
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            output_text="Texto del modelo",
            usage=self._usage,
            status=self._status,
            incomplete_details=self._detalles,
        )


# --- chat.generar ----------------------------------------------------------------


def test_generar_pasa_el_deployment_el_tope_de_tokens_y_no_guarda_la_conversacion() -> None:
    cliente = ClienteOpenAIFalso(SimpleNamespace(input_tokens=100, output_tokens=40, total_tokens=140))

    resultado = generar(cliente, SETTINGS, "instrucciones", "mensaje")

    assert cliente.kwargs["model"] == "mi-gpt-4o-mini"
    assert cliente.kwargs["instructions"] == "instrucciones"
    assert cliente.kwargs["input"] == "mensaje"
    assert cliente.kwargs["max_output_tokens"] == 250  # el tope por defecto
    assert cliente.kwargs["store"] is False
    assert cliente.kwargs["timeout"] > 0
    assert resultado == ChatResult("Texto del modelo", 100, 40, 140, resultado.latencia_ms, "mi-gpt-4o-mini")
    assert resultado.latencia_ms >= 0


def test_generar_por_defecto_no_manda_temperature_ni_reasoning() -> None:
    # Los modelos de razonamiento rechazan temperature: solo se envía si está configurada.
    cliente = ClienteOpenAIFalso()

    generar(cliente, SETTINGS, "i", "m")

    assert "temperature" not in cliente.kwargs
    assert "reasoning" not in cliente.kwargs


def test_generar_manda_temperature_y_reasoning_si_estan_configurados() -> None:
    cliente = ClienteOpenAIFalso()
    settings = FoundrySettings(
        endpoint=SETTINGS.endpoint, chat_deployment="d", reasoning_effort="minimal", temperature=0.3
    )

    generar(cliente, settings, "i", "m")

    assert cliente.kwargs["temperature"] == 0.3
    assert cliente.kwargs["reasoning"] == {"effort": "minimal"}


def test_generar_informa_cuando_la_respuesta_vino_incompleta() -> None:
    usage = SimpleNamespace(input_tokens=10, output_tokens=250, total_tokens=260)
    cliente = ClienteOpenAIFalso(usage, status="incomplete", incompleta_por="max_output_tokens")

    resultado = generar(cliente, SETTINGS, "i", "m")

    assert resultado.incompleta_por == "max_output_tokens"
    assert resultado.tokens_total == 260  # los tokens gastados se cuentan igual


def test_generar_completa_no_marca_incompleta() -> None:
    assert generar(ClienteOpenAIFalso(), SETTINGS, "i", "m").incompleta_por is None


def test_generar_acepta_un_tope_de_tokens_propio() -> None:
    cliente = ClienteOpenAIFalso()

    generar(cliente, SETTINGS, "i", "m", max_tokens_salida=50)

    assert cliente.kwargs["max_output_tokens"] == 50


def test_generar_sin_usage_cuenta_cero_tokens() -> None:
    resultado = generar(ClienteOpenAIFalso(usage=None), SETTINGS, "i", "m")

    assert (resultado.tokens_entrada, resultado.tokens_salida, resultado.tokens_total) == (0, 0, 0)


# --- FoundryGenerador --------------------------------------------------------------


@pytest.fixture
def clientes_falsos(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Reemplaza la creación de credencial/proyecto/cliente; devuelve el log de qué se creó."""
    creados: list[str] = []
    monkeypatch.setattr(generador_module, "crear_credencial", lambda: creados.append("credencial") or "cred")
    monkeypatch.setattr(
        generador_module, "crear_cliente_proyecto", lambda settings, cred: creados.append("proyecto") or "proyecto"
    )
    monkeypatch.setattr(
        generador_module, "obtener_cliente_openai", lambda proyecto: creados.append("openai") or ClienteOpenAIFalso()
    )
    return creados


def test_los_clientes_se_crean_una_sola_vez_y_recien_en_el_primer_uso(clientes_falsos: list[str]) -> None:
    generador = FoundryGenerador(SETTINGS)
    assert clientes_falsos == []  # construirlo no toca Azure

    generador.generar("i", "m")
    generador.generar("i", "m")

    assert clientes_falsos == ["credencial", "proyecto", "openai"]


def test_los_errores_del_sdk_se_traducen_a_foundry_error(
    clientes_falsos: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def falla(*args, **kwargs):
        raise APIConnectionError(request=httpx.Request("POST", "https://x"))

    monkeypatch.setattr(generador_module, "generar", falla)

    with pytest.raises(FoundryError) as info:
        FoundryGenerador(SETTINGS).generar("i", "m")

    # El mensaje es genérico: no filtra detalles internos del SDK.
    assert str(info.value) == "No se pudo obtener respuesta del modelo."
    assert not isinstance(info.value, FoundryNoConfigurado)


def test_sin_variables_de_entorno_lanza_foundry_no_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    def sin_config(cls, env=None):
        raise FoundryConfigError("Faltan variables de entorno: FOUNDRY_ENDPOINT")

    monkeypatch.setattr(FoundrySettings, "from_env", classmethod(sin_config))

    with pytest.raises(FoundryNoConfigurado):
        FoundryGenerador().generar("i", "m")
