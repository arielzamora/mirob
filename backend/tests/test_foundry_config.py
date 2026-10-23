"""Tests de FoundrySettings.from_env: validación fail-fast, sin red ni Azure.

Se le pasa un diccionario como `env`, así los tests no dependen de tu .env ni
de las variables de tu máquina.
"""

import dataclasses

import pytest

from app.foundry.config import (
    VAR_CHAT,
    VAR_EMBEDDINGS,
    VAR_ENDPOINT,
    VAR_REASONING_EFFORT,
    VAR_TEMPERATURE,
    FoundryConfigError,
    FoundrySettings,
)

ENDPOINT = "https://mi-recurso.services.ai.azure.com/api/projects/mi-proyecto"


def test_lee_las_variables_obligatorias() -> None:
    settings = FoundrySettings.from_env({VAR_ENDPOINT: ENDPOINT, VAR_CHAT: "mi-chat"})

    assert settings.endpoint == ENDPOINT
    assert settings.chat_deployment == "mi-chat"
    assert settings.embeddings_deployment is None  # opcional: queda en None si no está


def test_lee_el_deployment_de_embeddings_si_existe() -> None:
    env = {VAR_ENDPOINT: ENDPOINT, VAR_CHAT: "mi-chat", VAR_EMBEDDINGS: "mis-embeddings"}

    assert FoundrySettings.from_env(env).embeddings_deployment == "mis-embeddings"


def test_recorta_espacios_y_la_barra_final_del_endpoint() -> None:
    env = {VAR_ENDPOINT: f"  {ENDPOINT}/  ", VAR_CHAT: " mi-chat "}

    settings = FoundrySettings.from_env(env)

    assert settings.endpoint == ENDPOINT
    assert settings.chat_deployment == "mi-chat"


def test_informa_todas_las_variables_faltantes_juntas() -> None:
    # Con un diccionario vacío tienen que aparecer las dos obligatorias en un solo error.
    with pytest.raises(FoundryConfigError) as error:
        FoundrySettings.from_env({})

    assert VAR_ENDPOINT in str(error.value)
    assert VAR_CHAT in str(error.value)


def test_una_variable_en_blanco_cuenta_como_faltante() -> None:
    with pytest.raises(FoundryConfigError, match=VAR_CHAT):
        FoundrySettings.from_env({VAR_ENDPOINT: ENDPOINT, VAR_CHAT: "   "})


def test_rechaza_un_endpoint_sin_https() -> None:
    with pytest.raises(FoundryConfigError, match="https://"):
        FoundrySettings.from_env({VAR_ENDPOINT: "http://mi-recurso", VAR_CHAT: "mi-chat"})


def test_la_configuracion_es_inmutable() -> None:
    settings = FoundrySettings.from_env({VAR_ENDPOINT: ENDPOINT, VAR_CHAT: "mi-chat"})

    # Reasignar un campo de un dataclass frozen lanza FrozenInstanceError
    # (como intentar modificar una propiedad `init` de un record en C#).
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.endpoint = "https://otro"  # type: ignore[misc]


# --- Parámetros opcionales para modelos de razonamiento ------------------------------


def _env(**extra: str) -> dict[str, str]:
    return {VAR_ENDPOINT: ENDPOINT, VAR_CHAT: "mi-chat", **extra}


def test_por_defecto_no_hay_temperature_ni_reasoning_effort() -> None:
    settings = FoundrySettings.from_env(_env())

    assert settings.temperature is None
    assert settings.reasoning_effort is None


def test_lee_reasoning_effort_y_temperature_si_estan_definidos() -> None:
    settings = FoundrySettings.from_env(_env(**{VAR_REASONING_EFFORT: " Minimal ", VAR_TEMPERATURE: "0.3"}))

    assert settings.reasoning_effort == "minimal"  # se normaliza a minúsculas y sin espacios
    assert settings.temperature == 0.3


@pytest.mark.parametrize("valor", ["mucho", "extremo", "1"])
def test_un_reasoning_effort_desconocido_falla_con_un_mensaje_claro(valor: str) -> None:
    with pytest.raises(FoundryConfigError, match=VAR_REASONING_EFFORT):
        FoundrySettings.from_env(_env(**{VAR_REASONING_EFFORT: valor}))


@pytest.mark.parametrize("valor", ["caliente", "-0.1", "2.5"])
def test_una_temperature_invalida_falla_con_un_mensaje_claro(valor: str) -> None:
    with pytest.raises(FoundryConfigError, match=VAR_TEMPERATURE):
        FoundrySettings.from_env(_env(**{VAR_TEMPERATURE: valor}))
