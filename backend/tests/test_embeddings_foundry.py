"""Tests de FoundryEmbeddings (el adaptador del SDK) con un cliente OpenAI falso: sin red ni Azure."""

from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from app.foundry.config import (
    LOTE_EMBEDDINGS_POR_DEFECTO,
    VAR_LOTE_EMBEDDINGS,
    FoundryConfigError,
    FoundrySettings,
    leer_lote_embeddings,
)
from app.foundry.embeddings import FoundryEmbeddings
from app.foundry.tipos import FoundryError, FoundryNoConfigurado

ENDPOINT = "https://x.services.ai.azure.com/api/projects/p"
SETTINGS = FoundrySettings(endpoint=ENDPOINT, chat_deployment="chat", embeddings_deployment="mirob-embeddings")


class ClienteFalso:
    """Imita lo mínimo de openai.OpenAI que usa FoundryEmbeddings: embeddings.create(...)."""

    def __init__(self, desordenar: bool = False, usage_por_lote: int | None = 10, error: Exception | None = None) -> None:
        self.llamadas: list[dict] = []
        self._desordenar = desordenar
        self._usage = usage_por_lote
        self._error = error
        self.embeddings = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.llamadas.append(kwargs)
        if self._error is not None:
            raise self._error
        # El vector de cada texto es [largo del texto, 1.0]: así los tests saben qué vector corresponde a cada texto.
        datos = [SimpleNamespace(index=i, embedding=[float(len(t)), 1.0]) for i, t in enumerate(kwargs["input"])]
        if self._desordenar:
            datos.reverse()
        usage = SimpleNamespace(prompt_tokens=self._usage, total_tokens=self._usage) if self._usage is not None else None
        return SimpleNamespace(data=datos, usage=usage)


def _generador(cliente: ClienteFalso, settings: FoundrySettings = SETTINGS, lote: int = 32) -> FoundryEmbeddings:
    return FoundryEmbeddings(lambda: (cliente, settings), tamano_lote=lote)


def test_pasa_el_deployment_y_un_timeout_y_devuelve_un_vector_por_texto() -> None:
    cliente = ClienteFalso()

    resultado = _generador(cliente).embeber(["hola", "buenos días"])

    assert cliente.llamadas[0]["model"] == "mirob-embeddings"
    assert cliente.llamadas[0]["input"] == ["hola", "buenos días"]
    assert cliente.llamadas[0]["timeout"] > 0
    assert resultado.vectores.shape == (2, 2)
    assert resultado.vectores[:, 0].tolist() == [4.0, 11.0]
    assert resultado.deployment == "mirob-embeddings"
    assert resultado.latencia_ms >= 0


def test_manda_los_textos_en_lotes_y_no_uno_por_uno() -> None:
    cliente = ClienteFalso()
    textos = [f"texto {n}" for n in range(70)]

    resultado = _generador(cliente, lote=32).embeber(textos)

    assert [len(llamada["input"]) for llamada in cliente.llamadas] == [32, 32, 6]
    assert resultado.vectores.shape[0] == 70
    # El orden global se conserva: el vector i corresponde al texto i, aunque viaje en otro lote.
    assert resultado.vectores[:, 0].tolist() == [float(len(t)) for t in textos]


def test_suma_los_tokens_de_todos_los_lotes() -> None:
    cliente = ClienteFalso(usage_por_lote=10)

    resultado = _generador(cliente, lote=2).embeber(["a", "b", "c", "d", "e"])

    assert len(cliente.llamadas) == 3
    assert resultado.tokens == 30


def test_ordena_por_index_aunque_la_respuesta_venga_desordenada() -> None:
    cliente = ClienteFalso(desordenar=True)

    resultado = _generador(cliente).embeber(["a", "bb", "ccc"])

    assert resultado.vectores[:, 0].tolist() == [1.0, 2.0, 3.0]


def test_si_la_respuesta_no_trae_usage_cuenta_cero_tokens() -> None:
    resultado = _generador(ClienteFalso(usage_por_lote=None)).embeber(["hola"])

    assert resultado.tokens == 0


def test_sin_textos_no_llama_al_modelo() -> None:
    cliente = ClienteFalso()

    resultado = _generador(cliente).embeber([])

    assert cliente.llamadas == []
    assert resultado.tokens == 0
    assert resultado.vectores.shape[0] == 0


def test_sin_deployment_de_embeddings_lanza_foundry_no_configurado() -> None:
    sin_embeddings = FoundrySettings(endpoint=ENDPOINT, chat_deployment="chat")

    with pytest.raises(FoundryNoConfigurado):
        _generador(ClienteFalso(), sin_embeddings).embeber(["hola"])


def test_un_error_del_sdk_se_traduce_a_foundry_error() -> None:
    cliente = ClienteFalso(error=APIConnectionError(request=httpx.Request("POST", "https://x")))

    with pytest.raises(FoundryError):
        _generador(cliente).embeber(["hola"])


def test_si_vuelven_menos_vectores_que_textos_es_un_error() -> None:
    cliente = ClienteFalso()
    original = cliente._create
    cliente.embeddings = SimpleNamespace(
        create=lambda **kw: SimpleNamespace(data=original(**kw).data[:1], usage=None)
    )

    with pytest.raises(FoundryError):
        _generador(cliente).embeber(["a", "b"])


def test_deployment_devuelve_el_nombre_sin_llamar_a_azure() -> None:
    cliente = ClienteFalso()

    assert _generador(cliente).deployment() == "mirob-embeddings"
    assert cliente.llamadas == []


def test_deployment_sin_configurar_lanza_foundry_no_configurado() -> None:
    sin_embeddings = FoundrySettings(endpoint=ENDPOINT, chat_deployment="chat")

    with pytest.raises(FoundryNoConfigurado):
        _generador(ClienteFalso(), sin_embeddings).deployment()


def test_el_tamano_de_lote_se_lee_del_entorno_o_usa_el_default() -> None:
    assert leer_lote_embeddings({}) == LOTE_EMBEDDINGS_POR_DEFECTO
    assert leer_lote_embeddings({VAR_LOTE_EMBEDDINGS: " 8 "}) == 8


@pytest.mark.parametrize("valor", ["0", "-3", "muchos"])
def test_un_tamano_de_lote_invalido_falla_con_un_mensaje_claro(valor: str) -> None:
    with pytest.raises(FoundryConfigError, match=VAR_LOTE_EMBEDDINGS):
        leer_lote_embeddings({VAR_LOTE_EMBEDDINGS: valor})


def test_el_log_de_un_error_de_azure_incluye_estado_y_codigo_pero_no_el_mensaje(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from openai import AuthenticationError

    respuesta = httpx.Response(401, request=httpx.Request("POST", "https://x"))
    error = AuthenticationError(
        "mensaje con datos que no deben loguearse", response=respuesta, body={"code": "PermissionDenied", "message": "x"}
    )

    with caplog.at_level(logging.ERROR), pytest.raises(FoundryError):
        _generador(ClienteFalso(error=error)).embeber(["hola"])

    registro = "\n".join(caplog.messages)
    assert "AuthenticationError estado=401 codigo=PermissionDenied" in registro
    assert "datos que no deben loguearse" not in registro
