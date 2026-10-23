"""Tests de humanize.py: aperturas conversacionales y limpieza de formato.

humanize usa random.choice, así que fijamos la elección (siempre la primera
opción) para que el resultado sea determinístico. Es el equivalente a inyectar
un IRandom falso en C# en vez de usar new Random().
"""

import pytest

import app.rag.humanize as humanize_module
from app.rag.humanize import UMBRAL_BAJA_CONFIANZA, humanizar


@pytest.fixture(autouse=True)
def azar_fijo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reemplaza random.choice por "tomar el primero" en todos los tests de este archivo."""
    # autouse=True: se aplica solo a cada test del archivo, sin pedirla por parámetro.
    monkeypatch.setattr(humanize_module.random, "choice", lambda opciones: opciones[0])


def test_score_alto_usa_la_apertura_de_la_palabra_clave() -> None:
    resultado = humanizar("¿Cómo trabajás?", "con calma", 0.9)
    # "cómo" -> clave "como" (sin tilde) -> primera apertura del grupo.
    assert resultado == "Te cuento cómo lo encaro: con calma."


def test_score_bajo_usa_la_apertura_de_baja_confianza() -> None:
    resultado = humanizar("¿Cómo trabajás?", "con calma", UMBRAL_BAJA_CONFIANZA - 0.01)
    assert resultado.startswith("No tengo algo que responda esto puntualmente")


def test_pregunta_sin_palabra_clave_usa_apertura_generica() -> None:
    resultado = humanizar("Contame de vos", "soy de Córdoba", 0.9)
    assert resultado == "Te cuento: soy de Córdoba."


def test_limpia_espacios_capitaliza_y_cierra_con_punto() -> None:
    resultado = humanizar("hola", "  varias   palabras\ncon saltos ", 0.9)
    # \s+ colapsa espacios y saltos; el texto final cierra con punto.
    assert resultado == "Te cuento: varias palabras con saltos."


def test_no_duplica_el_punto_final() -> None:
    assert humanizar("hola", "ya termina bien.", 0.9).endswith("bien.")
    assert not humanizar("hola", "ya termina bien.", 0.9).endswith("..")
